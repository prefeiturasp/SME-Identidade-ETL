from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_retryable_exceptions() -> tuple[type[BaseException], ...]:
    try:
        from keycloak.exceptions import (
            KeycloakConnectionError,
            KeycloakGetError,
            KeycloakPostError,
            KeycloakPutError,
        )
        return (
            KeycloakConnectionError,
            KeycloakGetError,
            KeycloakPostError,
            KeycloakPutError,
            ConnectionError,
            TimeoutError,
        )
    except ImportError:
        return (ConnectionError, TimeoutError)


RETRYABLE = _get_retryable_exceptions()


def _with_backoff(fn, *args, max_retries: int = 5, base_delay: float = 1.0, **kwargs):
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except RETRYABLE as e:
            attempt += 1
            if attempt > max_retries:
                logger.error("KC call exceeded retries (%d): %s", max_retries, e)
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), 60.0)
            logger.warning(
                "KC transient error (attempt %d/%d) — retrying in %.1fs: %s",
                attempt, max_retries, delay, e,
            )
            time.sleep(delay)


def get_admin_client(realm: str | None = None):
    from keycloak import KeycloakAdmin

    return KeycloakAdmin(
        server_url=settings.KEYCLOAK_SERVER_URL,
        username=settings.KEYCLOAK_ADMIN_USER,
        password=settings.KEYCLOAK_ADMIN_PASSWORD,
        realm_name=realm or settings.KEYCLOAK_REALM,
        user_realm_name="master",
        verify=settings.KEYCLOAK_VERIFY_SSL,
    )


# Mapa cargo → role Keycloak
CARGO_ROLE_MAP = {
    "PROFESSOR DE EDUCACAO INFANTIL E ENSINO FUNDAMENTAL I": "Professor",
    "PROFESSOR DE ENSINO FUNDAMENTAL II E MEDIO": "Professor",
    "DIRETOR DE ESCOLA": "Diretor",
    "ASSISTENTE DE DIRETOR DE ESCOLA": "AssistenteDiretor",
    "COORDENADOR PEDAGOGICO": "CoordenadorPedagogico",
    "AUXILIAR TECNICO DE EDUCACAO": "AuxiliarTecnico",
    "SECRETARIO DE ESCOLA": "SecretarioEscola",
    "SUPERVISOR ESCOLAR": "Supervisor",
    "AGENTE ESCOLAR": "AgenteEscolar",
}

FUNCAO_ROLE_MAP = {
    "DIRETOR DE ESCOLA": "Diretor",
    "ASSISTENTE DE DIRECAO": "AssistenteDiretor",
    "COORDENADOR PEDAGOGICO": "CoordenadorPedagogico",
    "PAAI": "PAAI",
    "POA": "POA",
    "POEI": "POEI",
    "POSL": "POSL",
}


def _derive_realm_roles(usuario) -> list[str]:
    roles: set[str] = set()
    cargo = getattr(usuario, "cargo", None)
    funcao = getattr(usuario, "funcao", None)
    if cargo:
        key = cargo.strip().upper()
        if key in CARGO_ROLE_MAP:
            roles.add(CARGO_ROLE_MAP[key])
    if funcao:
        key = funcao.strip().upper()
        if key in FUNCAO_ROLE_MAP:
            roles.add(FUNCAO_ROLE_MAP[key])
    return sorted(roles)


def _derive_group_paths(usuario) -> list[str]:
    paths: list[str] = []
    dre = getattr(usuario, "dre", None)
    ue = getattr(usuario, "ue", None)
    lotacao = getattr(usuario, "lotacao", None)
    if dre and ue:
        paths.append(f"/SME/DRE-{dre}/UE-{ue}")
    elif dre:
        paths.append(f"/SME/DRE-{dre}")
    elif lotacao:
        paths.append(f"/SME/LOTACAO-{lotacao}")
    return paths


def _resolve_username(usuario) -> str:
    cpf = "".join(c for c in (usuario.cpf or "") if c.isdigit())
    if cpf:
        return cpf
    rf = (getattr(usuario, "rf", None) or "").strip()
    if rf:
        return rf
    matricula = (getattr(usuario, "matricula", None) or "").strip()
    if matricula:
        return matricula
    return f"{usuario.source}-{usuario.id}"


def _generate_initial_password(usuario) -> str:
    return _resolve_username(usuario)


def build_kc_payload(usuario) -> dict[str, Any]:
    nome = (usuario.nome or "").strip()
    parts = nome.split()
    first_name = parts[0] if parts else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    return {
        "username": _resolve_username(usuario),
        "email": (usuario.email or "").strip(),
        "firstName": first_name,
        "lastName": last_name,
        "enabled": (usuario.situacao or "").lower() != "inativo",
        "emailVerified": False,
        "attributes": {
            "cpf": [(usuario.cpf or "").strip()],
            "rf": [(getattr(usuario, "rf", None) or "").strip()],
            "matricula": [(getattr(usuario, "matricula", None) or "").strip()],
            "source": [usuario.source],
            "tipo_usuario": [_infer_tipo_usuario(usuario)],
        },
        "realmRoles": _derive_realm_roles(usuario),
        "groups": _derive_group_paths(usuario),
    }


def _infer_tipo_usuario(usuario) -> str:
    from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro
    if isinstance(usuario, StagingUsuarioServidor):
        return "servidor"
    if isinstance(usuario, StagingUsuarioAluno):
        return "aluno"
    if isinstance(usuario, StagingUsuarioTerceiro):
        return getattr(usuario, "tipo_acesso", None) or "terceiro"
    # Fallback legado (StagingUsuario)
    if getattr(usuario, "matricula", None) and not getattr(usuario, "rf", None):
        return "aluno"
    if getattr(usuario, "rf", None):
        return "servidor"
    return "outro"


def build_token_ms_payload(usuario) -> dict[str, Any]:
    return {
        "rf": getattr(usuario, "rf", None),
        "cpf": usuario.cpf,
        "nome": usuario.nome,
        "email": usuario.email,
        "tipo_usuario": _infer_tipo_usuario(usuario),
        "cargo": getattr(usuario, "cargo", None),
        "funcao": getattr(usuario, "funcao", None),
        "unidade": getattr(usuario, "lotacao_nome", None) or getattr(usuario, "lotacao", None),
        "unidade_codigo": getattr(usuario, "lotacao", None),
        "dre": getattr(usuario, "dre", None),
        "ue": getattr(usuario, "ue", None),
        "matricula": getattr(usuario, "matricula", None),
        "cod_escola": getattr(usuario, "cod_escola", None),
        "turma": getattr(usuario, "turma", None),
        "tipo_acesso": getattr(usuario, "tipo_acesso", None),
        "situacao": usuario.situacao,
        "source": usuario.source,
        "execution_id": str(usuario.execution_id),
    }


def compute_content_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _assign_roles_and_groups(admin, kc_user_id: str, realm_roles: list[str], groups: list[str]):
    for role_name in realm_roles:
        try:
            role = _with_backoff(admin.get_realm_role, role_name)
            _with_backoff(admin.assign_realm_roles, kc_user_id, [role])
        except Exception as e:
            logger.info("KC realm role '%s' indisponível (ignorado): %s", role_name, e)

    for group_path in groups:
        try:
            group = _with_backoff(admin.get_group_by_path, group_path)
            if group and group.get("id"):
                _with_backoff(admin.group_user_add, kc_user_id, group["id"])
        except Exception as e:
            logger.info("KC group '%s' indisponível (ignorado): %s", group_path, e)




def _find_existing_kc_user(admin, usuario, payload: dict) -> str | None:
    new_username = payload.get("username", "")
    email = (payload.get("email") or "").strip()

    candidates: list[dict] = []
    if email:
        try:
            candidates = admin.get_users({"email": email, "exact": True})
        except Exception:
            pass

    if not candidates:
        rf = (usuario.rf or "").strip()
        if rf:
            try:
                candidates = admin.get_users({"username": rf, "exact": True})
            except Exception:
                pass

    if not candidates:
        return None

    existing = candidates[0]
    existing_id = existing["id"]
    existing_username = existing.get("username", "")

    if existing_username == new_username:
        return existing_id

    logger.info(
        "Migrando username KC: %s → %s (id=%s); deletando user legado.",
        existing_username, new_username, existing_id,
    )
    try:
        admin.delete_user(existing_id)
    except Exception as exc:
        logger.warning("Falha ao deletar user legado %s: %s", existing_id, exc)
    return None


def upsert_user_to_keycloak(
    admin,
    usuario,
    *,
    realm: str = "sme-apps",
    execution=None,
) -> dict[str, Any]:
    from .models import UpsertControl

    payload = build_kc_payload(usuario)
    content_hash = compute_content_hash(payload)

    _source_id = (
        "".join(c for c in (usuario.cpf or "") if c.isdigit())
        or (usuario.rf or "").strip()
        or str(usuario.id)
    )
    upsert, created = UpsertControl.objects.get_or_create(
        entity_type=UpsertControl.EntityType.USER,
        source_system=usuario.source,
        source_id=_source_id,
        target_realm=realm,
        defaults={
            "content_hash": content_hash,
            "last_execution": execution,
        },
    )

    if not created and upsert.target_id and upsert.content_hash == content_hash:
        return {
            "action": "skipped",
            "kc_user_id": upsert.target_id,
            "content_hash": content_hash,
        }

    realm_roles = payload.pop("realmRoles", []) or []
    groups = payload.pop("groups", []) or []

    if created or not upsert.target_id:
        existing_kc_id = _find_existing_kc_user(admin, usuario, payload)
        if existing_kc_id:
            _with_backoff(admin.update_user, existing_kc_id, payload)
            kc_user_id = existing_kc_id
            upsert.target_id = kc_user_id
            action = "updated"
        else:
            kc_user_id = _with_backoff(admin.create_user, payload, exist_ok=True)
            upsert.target_id = kc_user_id
            action = "created"
            try:
                initial_pwd = _generate_initial_password(usuario)
                _with_backoff(
                    admin.set_user_password,
                    kc_user_id,
                    initial_pwd,
                    temporary=True,
                )
            except Exception as _pwd_err:
                logger.warning(
                    "KC: senha inicial não definida para %s: %s",
                    kc_user_id, _pwd_err,
                )
    else:
        _with_backoff(admin.update_user, upsert.target_id, payload)
        kc_user_id = upsert.target_id
        upsert.version = (upsert.version or 1) + 1
        action = "updated"

    _assign_roles_and_groups(admin, kc_user_id, realm_roles, groups)

    upsert.content_hash = content_hash
    if execution is not None:
        upsert.last_execution = execution
    upsert.sync_error = None
    upsert.save()

    return {
        "action": action,
        "kc_user_id": kc_user_id,
        "content_hash": content_hash,
    }


def upsert_kc_client(admin, sistema, realm: str | None = None) -> dict[str, Any]:
    import json as _json
    import requests as _requests
    from keycloak.exceptions import KeycloakPostError
    from keycloak.keycloak_admin import raise_error_from_response

    realm = realm or settings.KEYCLOAK_REALM
    sigla = (sistema.sigla or _slugify_for_client(sistema.nome)).strip().lower()
    suffix = (getattr(settings, "KEYCLOAK_CLIENT_SUFFIX", None) or "prod").strip().lower()
    client_id = f"{sigla}-{suffix}" if suffix else sigla

    payload = {
        "clientId": client_id,
        "name": sistema.nome,
        "description": (sistema.descricao or "")[:255] if sistema.descricao else "",
        "enabled": sistema.situacao == 1,
        "protocol": "openid-connect",
        "publicClient": False,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": [sistema.url_callback] if sistema.url_callback else ["*"],
        "attributes": {
            "post.logout.redirect.uris": sistema.url_logout or "+",
            "coresso_sis_id": str(sistema.coresso_sis_id),
        },
    }

    registration_access_token = None
    existing_uuid = _with_backoff(admin.get_client_id, client_id)

    if existing_uuid:
        stored_token = getattr(sistema, "kc_registration_access_token", None) or ""
        used_registration_api = False
        if stored_token:
            reg_url = (
                f"{settings.KEYCLOAK_SERVER_URL.rstrip('/')}"
                f"/realms/{realm}/clients-registrations/default/{client_id}"
            )
            try:
                resp = _requests.put(
                    reg_url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {stored_token}",
                        "Content-Type": "application/json",
                    },
                    verify=settings.KEYCLOAK_VERIFY_SSL,
                    timeout=30,
                )
                if resp.status_code == 200:
                    client_data = resp.json()
                    registration_access_token = client_data.get("registrationAccessToken")
                    existing_uuid = client_data.get("id") or existing_uuid
                    used_registration_api = True
                else:
                    logger.warning(
                        "Client Registration API PUT retornou %d para %s — token inválido/expirado",
                        resp.status_code, client_id,
                    )
            except Exception as e:
                logger.warning("Client Registration API PUT erro para %s: %s", client_id, e)

        if not used_registration_api:
            _with_backoff(admin.update_client, existing_uuid, payload)

        action = "updated"
        kc_uuid = existing_uuid
    else:
        kc_uuid = _with_backoff(admin.create_client, payload, skip_exists=True)
        if not kc_uuid:
            kc_uuid = _with_backoff(admin.get_client_id, client_id)
        action = "created"

    if not registration_access_token and kc_uuid:
        try:
            token_url = f"admin/realms/{realm}/clients/{kc_uuid}/registration-access-token"
            data_raw = admin.connection.raw_post(token_url, data=_json.dumps({}))
            token_data = raise_error_from_response(data_raw, KeycloakPostError, expected_codes=[200])
            registration_access_token = (
                token_data.get("registrationAccessToken") if isinstance(token_data, dict) else None
            )
        except Exception as e:
            logger.warning("Não foi possível gerar registration access token para %s: %s", client_id, e)

    sistema.kc_client_id = client_id
    sistema.kc_client_uuid = kc_uuid
    sistema.kc_realm = realm
    if registration_access_token:
        sistema.kc_registration_access_token = registration_access_token
    sistema.status = sistema.__class__.Status.LOADED
    save_fields = ["kc_client_id", "kc_client_uuid", "kc_realm", "status", "updated_at"]
    if registration_access_token:
        save_fields.append("kc_registration_access_token")
    sistema.save(update_fields=save_fields)

    return {
        "action": action,
        "client_id": client_id,
        "kc_uuid": kc_uuid,
        "realm": realm,
        "registration_access_token": registration_access_token,
    }


def _slugify_for_client(nome: str) -> str:
    import re
    import unicodedata

    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "sistema-sem-nome"


def upsert_kc_client_role(admin, perfil) -> dict[str, Any]:
    from staging.models import StagingPerfilCoreSSO

    sistema = perfil.sistema
    if sistema is None or not sistema.kc_client_uuid:
        perfil.status = StagingPerfilCoreSSO.Status.ERROR
        perfil.error_detail = "Sistema sem kc_client_uuid (rode load-keycloak de sistemas antes)"
        perfil.save(update_fields=["status", "error_detail", "updated_at"])
        return {"action": "skipped", "reason": "sistema_sem_client"}

    role_name = perfil.kc_role_name
    role_payload = {
        "name": role_name,
        "description": f"{perfil.nome} (CoreSSO gru_id={perfil.coresso_gru_id})",
        "clientRole": True,
        "attributes": {
            "coresso_gru_id": [perfil.coresso_gru_id],
            "coresso_sis_id": [str(perfil.coresso_sis_id)],
        },
    }

    try:
        admin.create_client_role(
            sistema.kc_client_uuid, role_payload, skip_exists=True
        )
        action = "created"
    except Exception as e:
        if "already exists" not in str(e).lower():
            raise
        action = "updated"

    try:
        existing = admin.get_client_role(sistema.kc_client_uuid, role_name)
    except Exception:
        existing = None

    role_id = (existing or {}).get("id")
    perfil.kc_role_id = role_id
    perfil.status = StagingPerfilCoreSSO.Status.LOADED
    perfil.error_detail = None
    perfil.save(update_fields=["kc_role_id", "status", "error_detail", "updated_at"])

    return {
        "action": action,
        "client_uuid": sistema.kc_client_uuid,
        "client_id": sistema.kc_client_id,
        "role_name": role_name,
        "role_id": role_id,
    }


def assign_user_client_roles(admin, kc_user_id: str, login: str) -> dict[str, Any]:
    from extract.tasks import fetch_coresso_groups_for_login
    from staging.models import StagingPerfilCoreSSO

    if not login:
        return {"assigned": 0, "skipped": 0, "details": []}

    grupos = fetch_coresso_groups_for_login(login)
    if not grupos:
        return {"assigned": 0, "skipped": 0, "details": []}

    gru_ids = [g["gru_id"] for g in grupos]
    perfis = (
        StagingPerfilCoreSSO.objects
        .filter(coresso_gru_id__in=gru_ids, kc_role_id__isnull=False)
        .select_related("sistema")
    )

    by_client: dict[str, list[dict]] = {}
    for p in perfis:
        if not p.sistema or not p.sistema.kc_client_uuid:
            continue
        by_client.setdefault(p.sistema.kc_client_uuid, []).append(
            {"id": p.kc_role_id, "name": p.kc_role_name}
        )

    assigned = 0
    details = []
    for client_uuid, roles in by_client.items():
        try:
            _with_backoff(
                admin.assign_client_role,
                user_id=kc_user_id,
                client_id=client_uuid,
                roles=roles,
            )
            assigned += len(roles)
            details.append({"client_uuid": client_uuid, "roles": [r["name"] for r in roles]})
        except Exception as e:
            logger.warning("assign_client_role falhou (client %s): %s", client_uuid, e)
            details.append({"client_uuid": client_uuid, "error": str(e)})

    return {
        "login": login,
        "groups_in_coresso": len(grupos),
        "perfis_resolvidos": sum(len(v) for v in by_client.values()),
        "assigned": assigned,
        "details": details,
    }



def emit_retroalim(tipo: str, usuario, payload: dict | None = None) -> None:
    from staging.models import RetroalimentacaoCoreSSO

    try:
        RetroalimentacaoCoreSSO.objects.create(
            tipo=tipo,
            rf=(getattr(usuario, "rf", None) or None),
            cpf=(getattr(usuario, "cpf", None) or None),
            payload=payload or {},
            execution_id=getattr(usuario, "execution_id", None),
        )
    except Exception as e:
        logger.warning("Falha ao gravar retroalim event: %s", e)
