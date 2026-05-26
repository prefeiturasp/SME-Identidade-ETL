"""Auxiliares do cliente admin do Keycloak para provisionamento de usuarios, clients e roles."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from typing import Any

import requests
from django.conf import settings

try:
    from keycloak import KeycloakAdmin
    from keycloak.exceptions import (
        KeycloakConnectionError,
        KeycloakGetError,
        KeycloakPostError,
        KeycloakPutError,
    )
    from keycloak.keycloak_admin import raise_error_from_response
except ImportError:
    KeycloakAdmin = None  # type: ignore[assignment]
    KeycloakConnectionError = ConnectionError  # type: ignore[assignment,misc]
    KeycloakGetError = Exception  # type: ignore[assignment,misc]
    KeycloakPostError = Exception  # type: ignore[assignment,misc]
    KeycloakPutError = Exception  # type: ignore[assignment,misc]
    raise_error_from_response = None  # type: ignore[assignment]

from .models import UpsertControl
from extract.tasks import fetch_coresso_groups_for_login
from staging.models import (
    RetroalimentacaoCoreSSO,
    StagingPerfilCoreSSO,
    StagingUsuarioAluno,
    StagingUsuarioServidor,
    StagingUsuarioTerceiro,
)

logger = logging.getLogger(__name__)

RETRYABLE = (
    KeycloakConnectionError,
    KeycloakGetError,
    KeycloakPostError,
    KeycloakPutError,
    ConnectionError,
    TimeoutError,
)


def _with_backoff(fn, *args, max_retries: int = 5, base_delay: float = 1.0, **kwargs):
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except RETRYABLE as e:
            # Erros 4xx são permanentes (validação, conflito) — não faz retry
            response_code = getattr(e, "response_code", None)
            if response_code is not None and 400 <= response_code < 500:
                raise
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
    """Instancia e retorna um cliente KeycloakAdmin autenticado."""
    return KeycloakAdmin(
        server_url=settings.KEYCLOAK_SERVER_URL,
        username=settings.KEYCLOAK_ADMIN_USER,
        password=settings.KEYCLOAK_ADMIN_PAWD,
        realm_name=realm or settings.KEYCLOAK_REALM,
        user_realm_name="master",
        verify=settings.KEYCLOAK_VERIFY_SSL,
    )


def ensure_realm_exists(realm: str) -> bool:
    """Verifica se o realm existe no Keycloak; cria-o com config mínima se ausente.

    Retorna True se o realm foi criado, False se já existia.
    """
    admin_master = KeycloakAdmin(
        server_url=settings.KEYCLOAK_SERVER_URL,
        username=settings.KEYCLOAK_ADMIN_USER,
        password=settings.KEYCLOAK_ADMIN_PAWD,
        realm_name="master",
        user_realm_name="master",
        verify=settings.KEYCLOAK_VERIFY_SSL,
    )
    existing_realms = {r["realm"] for r in admin_master.get_realms()}
    if realm in existing_realms:
        logger.debug("Realm '%s' já existe — nenhuma ação necessária.", realm)
        return False

    logger.warning(
        "Realm '%s' não encontrado no Keycloak — criando automaticamente com config mínima.",
        realm,
    )
    admin_master.create_realm({"realm": realm, "enabled": True})
    logger.info("Realm '%s' criado com sucesso.", realm)
    return True


# Mapa cargo → role Keycloak (match exato — mantido para retrocompatibilidade com testes)
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

# Regras de padrão para cargo → Realm Role.
# Usadas como fallback quando o match exato (CARGO_ROLE_MAP) não encontra resultado.
# Cobrem formas abreviadas do eol_db (ex: "PROF.ED.INF.E ENS.FUND.I") e nomes
# completos do SE1426. Ordem importa: regras mais específicas devem vir primeiro.
_CARGO_RULES: list[tuple[re.Pattern[str], str]] = [
    # AssistenteDiretor antes de Diretor para evitar colisão
    (re.compile(r"\bASSISTENTE\s+DE\s+DIRETOR\b|\bASSIST\.?\s*DIR\b", re.IGNORECASE), "AssistenteDiretor"),
    (re.compile(r"\bDIRETOR\b", re.IGNORECASE), "Diretor"),
    (re.compile(r"\bCOORDENADOR\s+PEDAGOGICO\b", re.IGNORECASE), "CoordenadorPedagogico"),
    # Professores: PROF., PROF.ED.INF., PROF.ENS.FUND.II E MED.-, PROFESSOR ...
    (re.compile(r"\bPROF(ESSOR)?\b", re.IGNORECASE), "Professor"),
    (re.compile(r"\bAUXILIAR\s+TECNICO\s+DE\s+EDUCACAO\b", re.IGNORECASE), "AuxiliarTecnico"),
    (re.compile(r"\bSECRETARIO\s+DE\s+ESCOLA\b", re.IGNORECASE), "SecretarioEscola"),
    (re.compile(r"\bSUPERVISOR\s+ESCOLAR\b", re.IGNORECASE), "Supervisor"),
    (re.compile(r"\bAGENTE\s+ESCOLAR\b", re.IGNORECASE), "AgenteEscolar"),
]

# Regras de padrão para funcao → Realm Role.
# Cobre dc_funcao_atividade (nomes completos) e dc_tipo_funcao (abreviações como
# "PROF OR SALA DE LEITURA POSL", "AUX. DIRECAO", "PROF DE APOIO ACOMP INCL PAAI").
_FUNCAO_RULES: list[tuple[re.Pattern[str], str]] = [
    # AssistenteDiretor antes de Diretor
    (re.compile(r"\bASSISTENTE\s+DE\s+DIRECAO\b|\bAUX\.?\s+DIRECAO\b", re.IGNORECASE), "AssistenteDiretor"),
    (re.compile(r"\bDIRETOR\s+DE\s+ESCOLA\b", re.IGNORECASE), "Diretor"),
    (re.compile(r"\bCOORDENADOR\s+PEDAGOGICO\b", re.IGNORECASE), "CoordenadorPedagogico"),
    # Siglas especiais que aparecem como sufixo em dc_tipo_funcao
    (re.compile(r"\bPAAI\b", re.IGNORECASE), "PAAI"),
    (re.compile(r"\bPOEI\b", re.IGNORECASE), "POEI"),
    (re.compile(r"\bPOSL\b", re.IGNORECASE), "POSL"),
    (re.compile(r"\bPOA\b", re.IGNORECASE), "POA"),
]


def _resolve_role(
    value: str,
    exact_map: dict[str, str],
    pattern_rules: list[tuple[re.Pattern[str], str]],
) -> str | None:
    raw = value.strip()
    role = exact_map.get(raw.upper())
    if role:
        return role
    for pattern, r in pattern_rules:
        if pattern.search(raw):
            return r
    return None


def _derive_realm_roles(usuario) -> list[str]:
    roles: set[str] = set()
    cargo = getattr(usuario, "cargo", None)
    funcao = getattr(usuario, "funcao", None)

    if cargo:
        role = _resolve_role(cargo, CARGO_ROLE_MAP, _CARGO_RULES)
        if role:
            roles.add(role)

    if funcao:
        role = _resolve_role(funcao, FUNCAO_ROLE_MAP, _FUNCAO_RULES)
        if role:
            roles.add(role)

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
    # CPF: apenas dígitos, zero-padded à esquerda até 11 dígitos
    cpf = "".join(c for c in (usuario.cpf or "") if c.isdigit())
    if cpf:
        return cpf.zfill(11)
    rf = (getattr(usuario, "rf", None) or "").strip()
    if rf:
        return rf
    matricula = (getattr(usuario, "matricula", None) or "").strip()
    if matricula:
        return matricula
    return f"{usuario.source}-{usuario.id}"


def _generate_initial_password(usuario) -> str:
    return _resolve_username(usuario)


_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _build_email(usuario) -> str:
    """Retorna o e-mail do usuario, gerando um placeholder para alunos sem e-mail.

    Emails legados do CoreSSO vêm como 'HEXHASH@' (sem domínio) — são descartados.
    """
    email = (usuario.email or "").strip()
    if email and _EMAIL_RE.match(email):
        return email
    if isinstance(usuario, StagingUsuarioAluno):
        matricula = (getattr(usuario, "matricula", None) or "").strip()
        if matricula:
            return f"{matricula}@aluno.sme.prefeitura.sp.gov.br"
    return ""


def _sanitize_kc_name(name: str) -> str:
    """Remove caracteres inválidos para o validador de nome do Keycloak (error-person-name-invalid-character).
    Mantém qualquer letra Unicode (categoria L*), espaços, hífens e apóstrofos."""
    normalized = unicodedata.normalize("NFC", name or "")
    sanitized = "".join(
        c for c in normalized
        if unicodedata.category(c).startswith("L") or c in " -'"
    )
    return " ".join(sanitized.split()) or "-"


def build_kc_payload(usuario) -> dict[str, Any]:
    """Monta o dict de representacao do usuario para o Keycloak a partir de um registro de staging."""
    nome = (usuario.nome or "").strip()
    parts = nome.split()
    first_name = _sanitize_kc_name(parts[0] if parts else "-")
    last_name = _sanitize_kc_name(" ".join(parts[1:]) if len(parts) > 1 else "-")

    attributes: dict[str, list[str]] = {
        "cpf": [(usuario.cpf or "").strip()],
        "rf": [(getattr(usuario, "rf", None) or "").strip()],
        "matricula": [(getattr(usuario, "matricula", None) or "").strip()],
        "source": [usuario.source],
        "tipo_usuario": [_infer_tipo_usuario(usuario)],
    }

    # Campos específicos de alunos EOL
    cod_escola = (getattr(usuario, "cod_escola", None) or "").strip()
    cod_dre = (getattr(usuario, "dre", None) or "").strip()
    if cod_escola:
        attributes["cod_escola"] = [cod_escola]
    if cod_dre:
        attributes["cod_dre"] = [cod_dre]

    # Atributos de perfil — enviados sempre (vazios até serem populados no staging)
    attributes["cargo"] = [(getattr(usuario, "cargo", None) or "").strip()]
    attributes["coordenadoria"] = [(getattr(usuario, "coordenadoria", None) or "").strip()]
    attributes["ultimo_acesso"] = [(getattr(usuario, "ultimo_acesso", None) or "").strip()]
    attributes["tempo_sessao"] = [(getattr(usuario, "tempo_sessao", None) or "").strip()]

    return {
        "username": _resolve_username(usuario),
        "email": _build_email(usuario),
        "firstName": first_name,
        "lastName": last_name,
        "enabled": (usuario.situacao or "").lower() != "inativo",
        "emailVerified": False,
        "requiredActions": [],
        "attributes": attributes,
        "realmRoles": _derive_realm_roles(usuario),
        "groups": _derive_group_paths(usuario),
    }


def _infer_tipo_usuario(usuario) -> str:
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
    """Monta o dict de payload do usuario para o token-ms a partir de um registro de staging."""
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
    """Calcula o hash SHA-256 de um dict para detectar mudancas entre sincronizacoes."""
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

    # 1. Busca pelo username (CPF/RF/matrícula) — fonte mais confiável
    if new_username:
        try:
            candidates = admin.get_users({"username": new_username, "exact": True})
        except Exception:
            pass

    # 2. Busca por email (se não encontrou pelo username)
    if not candidates and email:
        try:
            candidates = admin.get_users({"email": email, "exact": True})
        except Exception:
            pass

    # 3. Busca por RF como fallback
    if not candidates:
        rf = (getattr(usuario, "rf", None) or "").strip()
        if rf and rf != new_username:
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


def _handle_new_upsert(admin, upsert, usuario, payload) -> tuple[str, str]:
    """Cria ou reconcilia o usuário no KC quando ainda não há target_id no controle."""
    existing_kc_id = _find_existing_kc_user(admin, usuario, payload)
    if existing_kc_id:
        _with_backoff(admin.update_user, existing_kc_id, payload)
        upsert.target_id = existing_kc_id
        return existing_kc_id, "updated"

    try:
        kc_user_id = _with_backoff(admin.create_user, payload, exist_ok=True)
    except Exception as e:
        # 409 pode ocorrer por conflito de email — tenta buscar e atualizar
        response_code = getattr(e, "response_code", None)
        if response_code == 409:
            username = payload.get("username", "")
            try:
                users = admin.get_users({"username": username, "exact": True})
                if users:
                    kc_user_id = users[0]["id"]
                    _with_backoff(admin.update_user, kc_user_id, payload)
                    upsert.target_id = kc_user_id
                    return kc_user_id, "updated"
            except Exception:
                pass
        raise

    upsert.target_id = kc_user_id
    try:
        initial_pwd = _generate_initial_password(usuario)
        _with_backoff(admin.set_user_password, kc_user_id, initial_pwd, temporary=True)
    except Exception as e:
        logger.warning("KC: senha inicial não definida para %s: %s", kc_user_id, e)
    return kc_user_id, "created"


def upsert_user_to_keycloak(
    admin,
    usuario,
    *,
    realm: str = "sme-apps",
    execution=None,
) -> dict[str, Any]:
    """Cria ou atualiza um usuario no Keycloak a partir de um registro de staging e registra em UpsertControl."""
    payload = build_kc_payload(usuario)
    content_hash = compute_content_hash(payload)

    _source_id = (
        "".join(c for c in (usuario.cpf or "") if c.isdigit())
        or (getattr(usuario, "rf", None) or "").strip()
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
        kc_user_id, action = _handle_new_upsert(admin, upsert, usuario, payload)
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


def _sanitize_redirect_uri(raw: str | None) -> str | None:
    """Remove aspas envolventes e retorna None se o valor não parecer uma URI válida."""
    if not raw:
        return None
    cleaned = raw.strip().strip("'\"")
    if not cleaned:
        return None
    # Aceita apenas http://, https:// ou wildcard *
    if not re.match(r"^(https?://|\*)", cleaned):
        logger.warning("url_callback ignorado por não ser URI válida: %r", raw)
        return None
    return cleaned


def _build_client_payload(sistema, client_id: str) -> dict[str, Any]:
    callback = _sanitize_redirect_uri(sistema.url_callback)
    return {
        "clientId": client_id,
        "name": sistema.nome,
        "description": (sistema.descricao or "")[:255] if sistema.descricao else "",
        "enabled": sistema.situacao == 1,
        "protocol": "openid-connect",
        "publicClient": False,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": [callback] if callback else ["*"],
        "attributes": {
            "post.logout.redirect.uris": sistema.url_logout or "+",
            "coresso_sis_id": str(sistema.coresso_sis_id),
        },
    }


def _try_update_via_registration_api(
    sistema, client_id: str, realm: str, existing_uuid: str, payload: dict
) -> tuple[str | None, str | None]:
    """Tenta atualizar o client via Registration API. Retorna (kc_uuid, token) no sucesso, (None, None) na falha."""
    stored_token = getattr(sistema, "kc_registration_access_token", None) or ""
    if not stored_token:
        return None, None
    reg_url = (
        f"{settings.KEYCLOAK_SERVER_URL.rstrip('/')}"
        f"/realms/{realm}/clients-registrations/default/{client_id}"
    )
    try:
        resp = requests.put(
            reg_url,
            json=payload,
            headers={"Authorization": f"Bearer {stored_token}", "Content-Type": "application/json"},
            verify=settings.KEYCLOAK_VERIFY_SSL,
            timeout=30,
        )
    except Exception as e:
        logger.warning("Client Registration API PUT erro para %s: %s", client_id, e)
        return None, None
    if resp.status_code != 200:
        logger.warning(
            "Client Registration API PUT retornou %d para %s — token inválido/expirado",
            resp.status_code, client_id,
        )
        return None, None
    client_data = resp.json()
    return client_data.get("id") or existing_uuid, client_data.get("registrationAccessToken")


def _update_existing_client(
    admin, sistema, client_id: str, realm: str, existing_uuid: str, payload: dict
) -> tuple[str, str | None]:
    """Atualiza client existente no KC. Retorna (kc_uuid, registration_access_token)."""
    kc_uuid, registration_access_token = _try_update_via_registration_api(
        sistema, client_id, realm, existing_uuid, payload
    )
    if kc_uuid is None:
        _with_backoff(admin.update_client, existing_uuid, payload)
        return existing_uuid, None
    return kc_uuid, registration_access_token


def _fetch_registration_token(admin, realm: str, kc_uuid: str, client_id: str) -> str | None:
    """Solicita um novo registration access token para o client. Retorna None em caso de falha."""
    try:
        token_url = f"admin/realms/{realm}/clients/{kc_uuid}/registration-access-token"
        data_raw = admin.connection.raw_post(token_url, data=json.dumps({}))
        token_data = raise_error_from_response(data_raw, KeycloakPostError, expected_codes=[200])
        return token_data.get("registrationAccessToken") if isinstance(token_data, dict) else None
    except Exception as e:
        logger.warning("Não foi possível gerar registration access token para %s: %s", client_id, e)
        return None


def _save_sistema(
    sistema, client_id: str, kc_uuid: str | None, realm: str, registration_access_token: str | None
) -> None:
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


def upsert_kc_client(admin, sistema, realm: str | None = None) -> dict[str, Any]:
    """Cria ou atualiza um client OIDC no Keycloak a partir de um registro StagingSistema."""
    realm = realm or settings.KEYCLOAK_REALM
    sigla = (sistema.sigla or _slugify_for_client(sistema.nome)).strip().lower()
    suffix = (getattr(settings, "KEYCLOAK_CLIENT_SUFFIX", None) or "prod").strip().lower()
    client_id = f"{sigla}-{suffix}" if suffix else sigla

    payload = _build_client_payload(sistema, client_id)
    existing_uuid = _with_backoff(admin.get_client_id, client_id)

    if existing_uuid:
        kc_uuid, registration_access_token = _update_existing_client(
            admin, sistema, client_id, realm, existing_uuid, payload
        )
        action = "updated"
    else:
        kc_uuid = _with_backoff(admin.create_client, payload, skip_exists=True)
        if not kc_uuid:
            kc_uuid = _with_backoff(admin.get_client_id, client_id)
        registration_access_token = None
        action = "created"

    if not registration_access_token and kc_uuid:
        registration_access_token = _fetch_registration_token(admin, realm, kc_uuid, client_id)

    _save_sistema(sistema, client_id, kc_uuid, realm, registration_access_token)

    return {
        "action": action,
        "client_id": client_id,
        "kc_uuid": kc_uuid,
        "realm": realm,
        "registration_access_token": registration_access_token,
    }


def _slugify_for_client(nome: str) -> str:
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "sistema-sem-nome"


def upsert_kc_client_role(admin, perfil) -> dict[str, Any]:
    """Cria ou verifica uma client role do Keycloak para um registro StagingPerfilCoreSSO."""
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
    """Atribui ao usuario todas as client roles aplicaveis do Keycloak com base em seus grupos do CoreSSO."""
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
    """Emite um registro de evento de retroalimentacao para o CoreSSO apos uma operacao no Keycloak."""
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
