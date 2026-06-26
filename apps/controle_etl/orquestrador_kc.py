"""Orquestrador de provisionamento no Keycloak via Admin API.

Encapsula toda a comunicação com o Keycloak para criação,
atualização e sincronização de usuários, grupos, roles e clients.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from django.conf import settings
from django.db import close_old_connections, connection

logger = logging.getLogger("etl_identidade")

# Backoff exponencial: atraso máximo de 60 segundos
_ATRASO_BASE = 1.0
_ATRASO_MAXIMO = 60.0
_MAX_TENTATIVAS = 5

# Provisionamento Keycloak: chamadas HTTP por usuário em paralelo
_PROVISIONAMENTO_MAX_WORKERS = 8


def _excecoes_retriaveis() -> tuple[type[BaseException], ...]:
    """Retorna as exceções elegíveis para reintento no Keycloak."""
    base: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)
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
            *base,
        )
    except ImportError:
        return base


_RETRIAVEIS = _excecoes_retriaveis()


def _com_reintento(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Executa função com backoff exponencial em erros transitórios.

    Args:
        fn: Função a executar.
        *args: Argumentos posicionais.
        **kwargs: Argumentos nomeados.

    Returns:
        Resultado da função.

    Raises:
        Exception: Após esgotar todas as tentativas.
    """
    tentativa = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except _RETRIAVEIS as exc:
            tentativa += 1
            if tentativa >= _MAX_TENTATIVAS:
                logger.error(
                    "KC: esgotadas %d tentativas: %s",
                    _MAX_TENTATIVAS,
                    exc,
                )
                raise
            atraso = min(_ATRASO_BASE * (2 ** (tentativa - 1)), _ATRASO_MAXIMO)
            logger.warning(
                "KC: erro transitório (tentativa %d/%d)"
                + " — aguardando %.1fs: %s",
                tentativa,
                _MAX_TENTATIVAS,
                atraso,
                exc,
            )
            time.sleep(atraso)


def obter_admin_keycloak(realm: str | None = None) -> Any:
    """Retorna um cliente Keycloak Admin autenticado.

    Args:
        realm: Realm de destino (padrão: KEYCLOAK_REALM do settings).

    Returns:
        Instância de KeycloakAdmin conectada.
    """
    from keycloak import KeycloakAdmin

    return KeycloakAdmin(
        server_url=settings.KEYCLOAK_URL_SERVIDOR,
        username=settings.KEYCLOAK_USUARIO_ADMIN,
        password=settings.KEYCLOAK_SENHA_ADMIN,
        realm_name=realm or settings.KEYCLOAK_REALM,
        user_realm_name="master",
        verify=settings.KEYCLOAK_VERIFICAR_SSL,
    )


# Mapeamento cargo → role Keycloak
_CARGO_PARA_ROLE: dict[str, str] = {
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

_FUNCAO_PARA_ROLE: dict[str, str] = {
    "DIRETOR DE ESCOLA": "Diretor",
    "ASSISTENTE DE DIRECAO": "AssistenteDiretor",
    "COORDENADOR PEDAGOGICO": "CoordenadorPedagogico",
    "PAAI": "PAAI",
    "POA": "POA",
    "POEI": "POEI",
    "POSL": "POSL",
}


def _derivar_roles_realm(usuario: Any) -> list[str]:
    roles: set[str] = set()
    cargo = getattr(usuario, "cargo", None)
    funcao = getattr(usuario, "funcao", None)
    if cargo and cargo.strip().upper() in _CARGO_PARA_ROLE:
        roles.add(_CARGO_PARA_ROLE[cargo.strip().upper()])
    if funcao and funcao.strip().upper() in _FUNCAO_PARA_ROLE:
        roles.add(_FUNCAO_PARA_ROLE[funcao.strip().upper()])
    return sorted(roles)


def _derivar_grupos(usuario: Any) -> list[str]:
    caminhos: list[str] = []
    dre = getattr(usuario, "dre", None)
    ue = getattr(usuario, "ue", None)
    lotacao = getattr(usuario, "lotacao", None)
    if dre and ue:
        caminhos.append(f"/SME/DRE-{dre}/UE-{ue}")
    elif dre:
        caminhos.append(f"/SME/DRE-{dre}")
    elif lotacao:
        caminhos.append(f"/SME/LOTACAO-{lotacao}")
    return caminhos


def _resolver_username(usuario: Any) -> str:
    rf = (getattr(usuario, "rf", None) or "").strip()
    if rf:
        return rf
    cpf = "".join(c for c in (usuario.cpf or "") if c.isdigit())
    if cpf:
        return cpf
    matricula = (getattr(usuario, "matricula", None) or "").strip()
    if matricula:
        return matricula
    return f"{usuario.fonte}-{usuario.id}"


def _inferir_tipo_usuario(usuario: Any) -> str:
    from apps.staging.models import (
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    if isinstance(usuario, UsuarioServidorStaging):
        return "servidor"
    if isinstance(usuario, UsuarioAlunoStaging):
        return "aluno"
    if isinstance(usuario, UsuarioTerceiroStaging):
        return getattr(usuario, "tipo_acesso", None) or "terceiro"
    return "outro"


def construir_payload_kc(usuario: Any) -> dict[str, Any]:
    """Constrói o payload de upsert do usuário para o Keycloak.

    Args:
        usuario: Instância de staging (servidor, aluno ou terceiro).

    Returns:
        Dicionário compatível com a API de usuários do Keycloak.
    """
    nome = (usuario.nome or "").strip()
    partes = nome.split()
    primeiro_nome = partes[0] if partes else ""
    sobrenome = " ".join(partes[1:]) if len(partes) > 1 else ""

    return {
        "username": _resolver_username(usuario),
        "email": (usuario.email or "").strip(),
        "firstName": primeiro_nome,
        "lastName": sobrenome,
        "enabled": (usuario.situacao or "").lower() != "inativo",
        "emailVerified": False,
        "attributes": {
            "cpf": [(usuario.cpf or "").strip()],
            "rf": [(getattr(usuario, "rf", None) or "").strip()],
            "matricula": [(getattr(usuario, "matricula", None) or "").strip()],
            "fonte": [usuario.fonte],
            "tipo_usuario": [_inferir_tipo_usuario(usuario)],
        },
        "realmRoles": _derivar_roles_realm(usuario),
        "groups": _derivar_grupos(usuario),
    }


def construir_payload_token_ms(usuario: Any) -> dict[str, Any]:
    """Constrói o payload de publicação de atributos para o token-ms.

    Args:
        usuario: Instância de staging.

    Returns:
        Dicionário com atributos complementares do usuário.
    """
    return {
        "rf": getattr(usuario, "rf", None),
        "cpf": usuario.cpf,
        "nome": usuario.nome,
        "email": usuario.email,
        "tipo_usuario": _inferir_tipo_usuario(usuario),
        "cargo": getattr(usuario, "cargo", None),
        "funcao": getattr(usuario, "funcao", None),
        "unidade": (
            getattr(usuario, "lotacao_nome", None)
            or getattr(usuario, "lotacao", None)
        ),
        "unidade_codigo": getattr(usuario, "lotacao", None),
        "dre": getattr(usuario, "dre", None),
        "ue": getattr(usuario, "ue", None),
        "matricula": getattr(usuario, "matricula", None),
        "cod_escola": getattr(usuario, "cod_escola", None),
        "turma": getattr(usuario, "turma", None),
        "tipo_acesso": getattr(usuario, "tipo_acesso", None),
        "situacao": usuario.situacao,
        "fonte": usuario.fonte,
        "id_execucao": str(usuario.id_execucao),
    }


def calcular_hash_conteudo(payload: dict) -> str:
    """Calcula SHA-256 do payload para detecção de mudanças.

    Args:
        payload: Dicionário a serializar e hashar.

    Returns:
        String hexadecimal SHA-256 com 64 caracteres.
    """
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _atribuir_roles_e_grupos(
    admin: Any,
    kc_user_id: str,
    roles_realm: list[str],
    grupos: list[str],
) -> None:
    for nome_role in roles_realm:
        try:
            role = _com_reintento(admin.get_realm_role, nome_role)
            _com_reintento(admin.assign_realm_roles, kc_user_id, [role])
        except Exception as exc:
            logger.info(
                "KC: role '%s' indisponível (ignorada): %s",
                nome_role,
                exc,
            )
    for caminho_grupo in grupos:
        try:
            grupo = _com_reintento(admin.get_group_by_path, caminho_grupo)
            if grupo and grupo.get("id"):
                _com_reintento(admin.group_user_add, kc_user_id, grupo["id"])
        except Exception as exc:
            logger.info(
                "KC: grupo '%s' indisponível (ignorado): %s",
                caminho_grupo,
                exc,
            )


def _localizar_usuario_kc(
    admin: Any, usuario: Any, payload: dict
) -> str | None:
    username_novo = payload.get("username", "")
    email = (payload.get("email") or "").strip()
    candidatos: list[dict] = []
    if email:
        with contextlib.suppress(Exception):
            candidatos = admin.get_users({"email": email, "exact": True})
    if not candidatos:
        rf = (getattr(usuario, "rf", None) or "").strip()
        if rf:
            with contextlib.suppress(Exception):
                candidatos = admin.get_users({"username": rf, "exact": True})
    if not candidatos:
        return None
    existente = candidatos[0]
    if existente.get("username") == username_novo:
        return str(existente["id"])
    try:
        admin.delete_user(existente["id"])
    except Exception as exc:
        logger.warning(
            "KC: falha ao remover usuário legado %s: %s",
            existente["id"],
            exc,
        )
    return None


def _criar_usuario_kc(
    admin: Any, usuario: Any, payload: dict, controle: Any
) -> tuple[str, str]:
    kc_existente = _localizar_usuario_kc(admin, usuario, payload)
    if kc_existente:
        _com_reintento(admin.update_user, kc_existente, payload)
        controle.id_destino = kc_existente
        return kc_existente, "atualizado"

    kc_user_id = _com_reintento(admin.create_user, payload, exist_ok=True)
    controle.id_destino = kc_user_id
    try:
        senha_inicial = _resolver_username(usuario)
        _com_reintento(
            admin.set_user_password,
            kc_user_id,
            senha_inicial,
            temporary=True,
        )
    except Exception as exc:
        logger.warning(
            "KC: senha inicial não definida para %s: %s",
            kc_user_id,
            exc,
        )
    return kc_user_id, "criado"


def _atualizar_usuario_kc(
    admin: Any, payload: dict, controle: Any
) -> tuple[str, str]:
    _com_reintento(admin.update_user, controle.id_destino, payload)
    controle.versao = (controle.versao or 1) + 1
    return controle.id_destino, "atualizado"


def provisionar_usuario_kc(
    admin: Any,
    usuario: Any,
    *,
    realm: str = "sme-apps",
    execucao: Any = None,
    forcar_atualizacao: bool = False,
) -> dict[str, Any]:
    """Cria ou atualiza um usuário no Keycloak via upsert idempotente.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        usuario: Instância de staging do usuário.
        realm: Realm Keycloak de destino.
        execucao: Instância de ExecucaoETL para rastreamento.
        forcar_atualizacao: Ignora cache de hash e força
            update no Keycloak mesmo sem mudança de dados.

    Returns:
        Dicionário com ``acao``, ``kc_user_id`` e ``hash_conteudo``.
    """
    from apps.controle_etl.models import ControleProvisionamento

    payload = construir_payload_kc(usuario)
    roles_realm = payload.pop("realmRoles", []) or []
    grupos = payload.pop("groups", []) or []
    hash_conteudo = calcular_hash_conteudo(payload)

    id_origem = (
        "".join(c for c in (usuario.cpf or "") if c.isdigit())
        or (getattr(usuario, "rf", None) or "").strip()
        or str(usuario.id)
    )

    controle, criado = ControleProvisionamento.objects.get_or_create(
        tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
        sistema_origem=usuario.fonte,
        id_origem=id_origem,
        realm_destino=realm,
        defaults={
            "hash_conteudo": hash_conteudo,
            "ultima_execucao": execucao,
        },
    )

    if (
        not forcar_atualizacao
        and not criado
        and controle.id_destino
        and controle.hash_conteudo == hash_conteudo
    ):
        return {
            "acao": "ignorado",
            "kc_user_id": controle.id_destino,
            "hash_conteudo": hash_conteudo,
        }

    if criado or not controle.id_destino:
        kc_user_id, acao = _criar_usuario_kc(admin, usuario, payload, controle)
    else:
        kc_user_id, acao = _atualizar_usuario_kc(admin, payload, controle)

    _atribuir_roles_e_grupos(admin, kc_user_id, roles_realm, grupos)

    controle.hash_conteudo = hash_conteudo
    if execucao is not None:
        controle.ultima_execucao = execucao
    controle.erro_sincronizacao = None
    controle.save()

    return {
        "acao": acao,
        "kc_user_id": kc_user_id,
        "hash_conteudo": hash_conteudo,
    }


def _executar_com_conexao_fresca[T](fn: Callable[[], T]) -> T:
    """Executa ``fn`` fechando a conexão de banco da thread ao final.

    Cada worker do pool reaproveita threads do sistema operacional,
    que mantêm sua própria conexão de banco (thread-local). Fechar
    explicitamente ao final evita conexões ociosas acumuladas no
    banco quando o pool processa muitos lotes.
    """
    close_old_connections()
    try:
        return fn()
    finally:
        connection.close()


def provisionar_usuarios_kc_em_paralelo(
    admin: Any,
    usuarios: Iterable[Any],
    *,
    realm: str = "sme-apps",
    execucao: Any = None,
    max_workers: int | None = None,
    forcar_atualizacao: bool = False,
) -> list[dict[str, Any] | Exception]:
    """Provisiona usuários no Keycloak em paralelo via threads.

    Args:
        admin: Cliente KeycloakAdmin autenticado, compartilhado
            entre as threads (sessão HTTP do python-keycloak).
        usuarios: Iterável de instâncias de staging do usuário.
        realm: Realm Keycloak de destino.
        execucao: Instância de ExecucaoETL para rastreamento.
        max_workers: Número de threads.
        forcar_atualizacao: Força update mesmo sem mudança.

    Returns:
        Lista de resultados na mesma ordem de ``usuarios``.
    """
    workers = max_workers or _PROVISIONAMENTO_MAX_WORKERS

    def _provisionar(usuario: Any) -> dict[str, Any] | Exception:
        def _chamada() -> dict[str, Any]:
            return provisionar_usuario_kc(
                admin,
                usuario,
                realm=realm,
                execucao=execucao,
                forcar_atualizacao=forcar_atualizacao,
            )

        try:
            return _executar_com_conexao_fresca(_chamada)
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(_provisionar, usuarios))


def _slugificar_client_id(nome: str) -> str:
    import re
    import unicodedata

    s = (
        unicodedata.normalize("NFKD", nome or "")
        .encode("ascii", "ignore")
        .decode()
    )
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "sistema-sem-nome"


def provisionar_client_kc(
    admin: Any, sistema: Any, realm: str | None = None
) -> dict[str, Any]:
    """Cria ou atualiza um client Keycloak a partir de um SistemaStaging.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        sistema: Instância de SistemaStaging.
        realm: Realm Keycloak de destino.

    Returns:
        Dicionário com ``acao``, ``client_id``, ``kc_uuid`` e ``realm``.
    """
    realm = realm or settings.KEYCLOAK_REALM
    sigla = (
        (sistema.sigla or _slugificar_client_id(sistema.nome)).strip().lower()
    )
    sufixo = (
        (getattr(settings, "KEYCLOAK_SUFIXO_CLIENT", None) or "prod")
        .strip()
        .lower()
    )
    client_id = f"{sigla}-{sufixo}" if sufixo else sigla

    payload = {
        "clientId": client_id,
        "name": sistema.nome,
        "enabled": sistema.situacao == 1,
        "protocol": "openid-connect",
        "publicClient": False,
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "redirectUris": (
            [sistema.url_callback] if sistema.url_callback else ["*"]
        ),
        "attributes": {
            "post.logout.redirect.uris": sistema.url_logout or "+",
            "coresso_sis_id": str(sistema.coresso_sis_id),
        },
    }

    uuid_existente = _com_reintento(admin.get_client_id, client_id)

    if uuid_existente:
        _com_reintento(admin.update_client, uuid_existente, payload)
        acao = "atualizado"
        kc_uuid = uuid_existente
    else:
        kc_uuid = _com_reintento(
            admin.create_client, payload, skip_exists=True
        )
        if not kc_uuid:
            kc_uuid = _com_reintento(admin.get_client_id, client_id)
        acao = "criado"

    from apps.staging.models import SistemaStaging

    sistema.kc_client_id = client_id
    sistema.kc_client_uuid = kc_uuid
    sistema.kc_realm = realm
    sistema.situacao_provisionamento = (
        SistemaStaging.SituacaoProvisionamento.PROVISIONADO
    )
    sistema.save(
        update_fields=[
            "kc_client_id",
            "kc_client_uuid",
            "kc_realm",
            "situacao_provisionamento",
            "atualizado_em",
        ]
    )
    return {"acao": acao, "client_id": client_id, "kc_uuid": kc_uuid}


def provisionar_role_client_kc(admin: Any, perfil: Any) -> dict[str, Any]:
    """Cria ou atualiza client role Keycloak a partir de PerfilCoressoStaging.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        perfil: Instância de PerfilCoressoStaging.

    Returns:
        Dicionário com ``acao``, ``client_uuid`` e ``role_nome``.
    """
    from apps.staging.models import PerfilCoressoStaging

    sistema = perfil.sistema
    if sistema is None or not sistema.kc_client_uuid:
        perfil.situacao_provisionamento = (
            PerfilCoressoStaging.SituacaoProvisionamento.ERRO
        )
        perfil.detalhe_erro = (
            "Sistema sem kc_client_uuid" + " (provisione os sistemas primeiro)"
        )
        perfil.save(
            update_fields=[
                "situacao_provisionamento",
                "detalhe_erro",
                "atualizado_em",
            ]
        )
        return {"acao": "ignorado", "motivo": "sistema_sem_client"}

    nome_role = perfil.kc_role_nome
    payload_role = {
        "name": nome_role,
        "description": (
            f"{perfil.nome} (CoreSSO gru_id={perfil.coresso_gru_id})"
        ),
        "clientRole": True,
        "attributes": {
            "coresso_gru_id": [perfil.coresso_gru_id],
            "coresso_sis_id": [str(perfil.coresso_sis_id)],
        },
    }

    try:
        admin.create_client_role(
            sistema.kc_client_uuid, payload_role, skip_exists=True
        )
        acao = "criado"
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            raise
        acao = "atualizado"

    try:
        existente = admin.get_client_role(sistema.kc_client_uuid, nome_role)
    except Exception:
        existente = None

    role_id = (existente or {}).get("id")
    perfil.kc_role_id = role_id
    perfil.situacao_provisionamento = (
        PerfilCoressoStaging.SituacaoProvisionamento.PROVISIONADO
    )
    perfil.detalhe_erro = None
    perfil.save(
        update_fields=[
            "kc_role_id",
            "situacao_provisionamento",
            "detalhe_erro",
            "atualizado_em",
        ]
    )
    return {
        "acao": acao,
        "client_uuid": sistema.kc_client_uuid,
        "role_nome": nome_role,
        "role_id": role_id,
    }


def _resolver_role_info(
    gru_id: str,
    cache: dict[str, tuple[str, dict] | None],
) -> tuple[str, dict] | None:
    """Resolve client_uuid e role payload a partir do staging."""
    if gru_id not in cache:
        from apps.staging.models import PerfilCoressoStaging

        perfil = (
            PerfilCoressoStaging.objects.select_related("sistema")
            .filter(coresso_gru_id=gru_id)
            .first()
        )
        if (
            perfil
            and perfil.kc_role_id
            and perfil.sistema
            and perfil.sistema.kc_client_uuid
        ):
            cache[gru_id] = (
                perfil.sistema.kc_client_uuid,
                {
                    "id": perfil.kc_role_id,
                    "name": perfil.kc_role_nome,
                },
            )
        else:
            cache[gru_id] = None
    return cache[gru_id]


def _resolver_kc_user_id(
    admin: Any,
    username: str,
    cache: dict[str, str | None],
) -> str | None:
    """Resolve o UUID do usuário no Keycloak."""
    if username not in cache:
        try:
            usuarios_kc = _com_reintento(
                admin.get_users,
                {"username": username, "exact": True},
            )
            cache[username] = (
                str(usuarios_kc[0]["id"]) if usuarios_kc else None
            )
        except Exception:
            cache[username] = None
    return cache[username]


def atribuir_client_roles_usuario_kc(
    admin: Any,
    vinculos: Iterable[dict],
) -> dict[str, int]:
    """Atribui client roles aos usuários no Keycloak.

    Opera em streaming sobre os vínculos extraídos do CoreSSO,
    usando caches locais para evitar lookups repetidos na API
    do Keycloak.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        vinculos: Iterável de dicts com ``login``, ``cpf``,
            ``gru_id``, ``gru_nome`` e ``sis_id``.

    Returns:
        Dicionário com ``atribuidos``, ``ignorados`` e ``erros``.
    """
    cache_role: dict[str, tuple[str, dict] | None] = {}
    cache_usuario: dict[str, str | None] = {}
    atribuidos = ignorados = erros = 0

    for vinculo in vinculos:
        gru_id = str(vinculo["gru_id"])
        login = vinculo.get("login") or ""
        cpf = (vinculo.get("cpf") or "").strip()

        role_info = _resolver_role_info(gru_id, cache_role)
        if role_info is None:
            ignorados += 1
            continue

        client_uuid, role_payload = role_info
        username = login or "".join(c for c in cpf if c.isdigit())
        if not username:
            ignorados += 1
            continue

        kc_user_id = _resolver_kc_user_id(admin, username, cache_usuario)
        if kc_user_id is None:
            ignorados += 1
            continue

        try:
            _com_reintento(
                admin.assign_client_role,
                kc_user_id,
                client_uuid,
                [role_payload],
            )
            atribuidos += 1
        except Exception as exc:
            logger.warning(
                "KC: falha ao atribuir role %s ao user %s:" + " %s",
                role_payload.get("name"),
                username,
                exc,
            )
            erros += 1

    logger.info(
        "atribuir_client_roles — atribuidos=%d" + " ignorados=%d erros=%d",
        atribuidos,
        ignorados,
        erros,
    )
    return {
        "atribuidos": atribuidos,
        "ignorados": ignorados,
        "erros": erros,
    }


def _resolver_conflito_email(
    admin: Any,
    payload: dict,
    login: str,
) -> dict:
    """Resolve conflito de email duplicado no KC.

    Busca o dono do email: se os atributos rf/cpf coincidem
    com o usuário atual, limpa o email do duplicado e
    mantém o payload original. Caso contrário, remove o
    email do payload.
    """
    email = (payload.get("email") or "").strip()
    if not email:
        return payload

    try:
        donos = _com_reintento(
            admin.get_users, {"email": email, "exact": True}
        )
    except Exception:
        donos = []

    if not donos:
        return {k: v for k, v in payload.items() if k != "email"}

    dono = donos[0]
    attrs_dono = dono.get("attributes", {})
    rf_dono = (attrs_dono.get("rf", [""])[0] or "").strip()
    cpf_dono = (attrs_dono.get("cpf", [""])[0] or "").strip()

    attrs_novo = payload.get("attributes", {})
    rf_novo = (attrs_novo.get("rf", [""])[0] or "").strip()
    cpf_novo = (attrs_novo.get("cpf", [""])[0] or "").strip()

    mesmo_usuario = (rf_dono and rf_dono == rf_novo) or (
        cpf_dono and cpf_dono == cpf_novo
    )

    if mesmo_usuario:
        logger.info(
            "KC: email %s pertence ao mesmo usuário"
            + " (%s) — limpando duplicado",
            email,
            dono.get("username"),
        )
        _com_reintento(
            admin.update_user,
            str(dono["id"]),
            {"email": ""},
        )
        return payload

    logger.info(
        "KC: email %s pertence a outro usuário (%s)"
        + " — removendo do payload de %s",
        email,
        dono.get("username"),
        login,
    )
    return {k: v for k, v in payload.items() if k != "email"}


def _montar_queries_busca(login: str, cpf: str, email: str) -> list[dict]:
    """Monta lista de queries KC para localizar contas."""
    queries: list[dict] = []
    if login:
        queries.append({"username": login, "exact": True})
    if cpf:
        queries.append({"username": cpf, "exact": True})
    if login:
        queries.append({"q": f"rf:{login}"})
    if cpf:
        queries.append({"q": f"cpf:{cpf}"})
    if email and "@" in email:
        queries.append({"email": email, "exact": True})
    return queries


def _buscar_todas_contas_kc(
    admin: Any, login: str, cpf: str, email: str
) -> list[dict]:
    """Busca todas as contas KC associadas ao usuário.

    Prioridade: RF (login) → CPF → email.
    Retorna lista de dicts KC sem duplicatas (por id).
    """
    vistos: set[str] = set()
    contas: list[dict] = []
    for query in _montar_queries_busca(login, cpf, email):
        try:
            encontrados = _com_reintento(admin.get_users, query)
        except Exception:
            continue
        for u in encontrados or []:
            uid = str(u["id"])
            if uid not in vistos:
                vistos.add(uid)
                contas.append(u)
    return contas


def _migrar_client_roles_kc(
    admin: Any,
    kc_id_origem: str,
    kc_id_destino: str,
) -> None:
    """Migra client roles de uma conta KC para outra."""
    from apps.staging.models import SistemaStaging  # noqa: PLC0415

    for s in SistemaStaging.objects.filter(
        kc_client_uuid__isnull=False,
    ).exclude(kc_client_uuid=""):
        try:
            croles = _com_reintento(
                admin.get_client_roles_of_user,
                kc_id_origem,
                s.kc_client_uuid,
            )
            if croles:
                _com_reintento(
                    admin.assign_client_role,
                    kc_id_destino,
                    s.kc_client_uuid,
                    croles,
                )
        except Exception as exc:
            logger.warning(
                "KC merge: erro ao migrar roles" + " do client %s: %s",
                s.kc_client_id,
                exc,
            )


def _migrar_realm_roles_kc(
    admin: Any,
    kc_id_origem: str,
    kc_id_destino: str,
) -> None:
    """Migra realm roles de uma conta KC para outra."""
    default_names = {
        "default-roles-sme-apps",
        "offline_access",
        "uma_authorization",
    }
    try:
        realm_roles = _com_reintento(
            admin.get_realm_roles_of_user,
            kc_id_origem,
        )
        roles_migrar = [
            r
            for r in (realm_roles or [])
            if r.get("name") not in default_names
        ]
        if roles_migrar:
            _com_reintento(
                admin.assign_realm_roles,
                kc_id_destino,
                roles_migrar,
            )
    except Exception as exc:
        logger.warning(
            "KC merge: erro ao migrar realm roles: %s",
            exc,
        )


def _merge_contas_kc(
    admin: Any,
    principal: dict,
    duplicadas: list[dict],
) -> list[str]:
    """Migra roles das contas duplicadas e as remove.

    Retorna lista de usernames removidos.
    """
    kc_id_principal = str(principal["id"])
    removidos: list[str] = []

    for dup in duplicadas:
        kc_id_dup = str(dup["id"])
        logger.info(
            "KC merge: migrando roles de %s (%s)" + " para %s (%s)",
            dup.get("username"),
            kc_id_dup,
            principal.get("username"),
            kc_id_principal,
        )
        _migrar_client_roles_kc(admin, kc_id_dup, kc_id_principal)
        _migrar_realm_roles_kc(admin, kc_id_dup, kc_id_principal)

        try:
            _com_reintento(admin.delete_user, kc_id_dup)
            removidos.append(dup.get("username", kc_id_dup))
            logger.info(
                "KC merge: conta duplicada %s removida",
                dup.get("username"),
            )
        except Exception as exc:
            logger.warning(
                "KC merge: falha ao remover duplicada" + " %s: %s",
                dup.get("username"),
                exc,
            )

    return removidos


def _upsert_usuario_kc(
    admin: Any, payload: dict, login: str, username: str
) -> tuple[str | None, str]:
    """Cria ou atualiza usuário no KC. Retorna (id, acao).

    Busca todas as contas associadas (por RF, CPF, email).
    Se encontrar duplicatas, faz merge de roles para a
    conta principal e remove as duplicadas.
    """
    cpf = (payload.get("attributes", {}).get("cpf") or [""])[0]
    email = (payload.get("email") or "").strip()

    contas = _buscar_todas_contas_kc(admin, login, cpf, email)

    if contas:
        principal = contas[0]
        kc_id = str(principal["id"])

        if len(contas) > 1:
            removidos = _merge_contas_kc(admin, principal, contas[1:])
            if removidos:
                logger.info(
                    "KC: merge concluído — removidos: %s",
                    ", ".join(removidos),
                )

        payload_atualizado = {**payload, "username": username}
        try:
            _com_reintento(admin.update_user, kc_id, payload_atualizado)
        except Exception:
            payload_atualizado = _resolver_conflito_email(
                admin, payload_atualizado, login
            )
            _com_reintento(admin.update_user, kc_id, payload_atualizado)
        return kc_id, "atualizado"

    try:
        kc_id = _com_reintento(admin.create_user, payload, exist_ok=True)
    except Exception:
        payload = _resolver_conflito_email(admin, payload, login)
        kc_id = _com_reintento(admin.create_user, payload, exist_ok=True)
    if not kc_id:
        kc_users = _com_reintento(
            admin.get_users,
            {"username": username, "exact": True},
        )
        kc_id = str(kc_users[0]["id"]) if kc_users else ""
    if kc_id:
        _com_reintento(
            admin.set_user_password,
            kc_id,
            username,
            temporary=True,
        )
    return kc_id, "criado"


def _atribuir_roles_sistema(
    admin: Any, kc_user_id: str, sis_data: dict
) -> dict:
    """Atribui roles de um sistema ao usuário no KC."""
    from apps.staging.models import PerfilCoressoStaging, SistemaStaging

    sis_id = sis_data["sis_id"]
    sistema = SistemaStaging.objects.filter(coresso_sis_id=sis_id).first()
    if not sistema or not sistema.kc_client_uuid:
        return {
            "sistema": sis_data["nome"],
            "status": "sem client no KC",
            "roles": [],
            "erros": 0,
        }

    roles_ok: list[str] = []
    erros = 0
    for grupo in sis_data.get("grupos", []):
        perfil = PerfilCoressoStaging.objects.filter(
            coresso_gru_id=grupo["gru_id"]
        ).first()
        if not perfil or not perfil.kc_role_id:
            continue
        try:
            _com_reintento(
                admin.assign_client_role,
                kc_user_id,
                sistema.kc_client_uuid,
                [
                    {
                        "id": perfil.kc_role_id,
                        "name": perfil.kc_role_nome,
                    }
                ],
            )
            roles_ok.append(perfil.kc_role_nome or "")
        except Exception:
            erros += 1

    return {
        "sistema": sis_data["nome"],
        "client_id": sistema.kc_client_id,
        "roles": roles_ok,
        "erros": erros,
    }


def sincronizar_usuario_kc(
    admin: Any,
    dados_coresso: dict,
    *,
    realm: str = "sme-apps",
) -> dict[str, Any]:
    """Sincroniza um usuário no Keycloak com todos os seus roles.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        dados_coresso: Resultado de
            ``buscar_dados_usuario_coresso``.
        realm: Realm de destino.

    Returns:
        Dict com ``acao``, ``kc_user_id``, ``roles_atribuidos``
        e ``sistemas``.
    """
    login = dados_coresso["login"]
    cpf = dados_coresso.get("cpf", "").strip()
    nome = dados_coresso.get("nome", "").strip()
    email = dados_coresso.get("email", "").strip()
    partes = nome.split()

    username = login if login else cpf
    payload = {
        "username": username,
        "email": email,
        "firstName": partes[0] if partes else "",
        "lastName": (" ".join(partes[1:]) if len(partes) > 1 else ""),
        "enabled": dados_coresso["situacao"] == "ativo",
        "emailVerified": False,
        "attributes": {
            "cpf": [cpf],
            "rf": [login],
            "fonte": ["coresso"],
        },
    }

    kc_user_id, acao = _upsert_usuario_kc(admin, payload, login, username)
    if not kc_user_id:
        return {"acao": "erro", "motivo": "sem kc_user_id"}

    total_roles = total_erros = 0
    sistemas_resultado: list[dict] = []
    for sis_data in dados_coresso.get("sistemas", {}).values():
        r = _atribuir_roles_sistema(admin, kc_user_id, sis_data)
        total_roles += len(r.get("roles", []))
        total_erros += r.get("erros", 0)
        sistemas_resultado.append(r)

    base = settings.KEYCLOAK_URL_SERVIDOR.rstrip("/")
    return {
        "acao": acao,
        "kc_user_id": kc_user_id,
        "kc_url": (
            f"{base}/admin/master/console/"
            + f"#/{realm}/users/{kc_user_id}/settings"
        ),
        "username": username,
        "nome": nome,
        "roles_atribuidos": total_roles,
        "roles_erros": total_erros,
        "sistemas": sistemas_resultado,
    }


def conceder_acesso_kc(
    admin: Any,
    dados_coresso: dict,
    sis_id: int,
    nomes_roles: list[str],
    *,
    realm: str = "sme-apps",
) -> dict[str, Any]:
    """Concede acesso a um sistema e roles no Keycloak.

    Cria/atualiza o usuário e atribui client roles
    específicos, independentemente dos vínculos no CoreSSO.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        dados_coresso: Resultado de
            ``buscar_dados_usuario_coresso``.
        sis_id: ``coresso_sis_id`` do sistema alvo.
        nomes_roles: Nomes dos perfis/roles a conceder.
        realm: Realm de destino.

    Returns:
        Dict com ``acao``, ``kc_user_id``, ``sistema``,
        ``roles_atribuidos``, ``roles_nao_encontrados``
        e ``erros``.
    """
    from apps.staging.models import (  # noqa: PLC0415
        PerfilCoressoStaging,
        SistemaStaging,
    )

    login = dados_coresso["login"]
    cpf = dados_coresso.get("cpf", "").strip()
    nome = dados_coresso.get("nome", "").strip()
    email = dados_coresso.get("email", "").strip()
    partes = nome.split()

    username = login if login else cpf
    payload = {
        "username": username,
        "email": email,
        "firstName": partes[0] if partes else "",
        "lastName": (" ".join(partes[1:]) if len(partes) > 1 else ""),
        "enabled": dados_coresso["situacao"] == "ativo",
        "emailVerified": False,
        "attributes": {
            "cpf": [cpf],
            "rf": [login],
            "fonte": ["coresso"],
        },
    }

    kc_user_id, acao = _upsert_usuario_kc(admin, payload, login, username)
    if not kc_user_id:
        return {"acao": "erro", "motivo": "sem kc_user_id"}

    sistema = SistemaStaging.objects.filter(coresso_sis_id=sis_id).first()
    if not sistema or not sistema.kc_client_uuid:
        return {
            "acao": acao,
            "kc_user_id": kc_user_id,
            "erro": (
                f"Sistema sis_id={sis_id} não encontrado"
                " ou sem client no Keycloak."
            ),
        }

    roles_ok: list[str] = []
    roles_nao_encontrados: list[str] = []
    erros = 0

    for role_name in nomes_roles:
        perfil = PerfilCoressoStaging.objects.filter(
            coresso_sis_id=sis_id,
            nome__iexact=role_name,
        ).first()
        if not perfil:
            perfil = PerfilCoressoStaging.objects.filter(
                coresso_sis_id=sis_id,
                kc_role_nome__iexact=role_name,
            ).first()
        if not perfil or not perfil.kc_role_id:
            roles_nao_encontrados.append(role_name)
            continue
        try:
            _com_reintento(
                admin.assign_client_role,
                kc_user_id,
                sistema.kc_client_uuid,
                [
                    {
                        "id": perfil.kc_role_id,
                        "name": perfil.kc_role_nome,
                    }
                ],
            )
            roles_ok.append(perfil.kc_role_nome or role_name)
        except Exception as exc:
            logger.warning(
                "KC: falha ao conceder role %s" + " ao user %s: %s",
                role_name,
                username,
                exc,
            )
            erros += 1

    base = settings.KEYCLOAK_URL_SERVIDOR.rstrip("/")
    return {
        "acao": acao,
        "kc_user_id": kc_user_id,
        "kc_url": (
            f"{base}/admin/master/console/"
            + f"#/{realm}/users/{kc_user_id}/settings"
        ),
        "username": username,
        "nome": nome,
        "sistema": sistema.nome,
        "client_id": sistema.kc_client_id,
        "roles_atribuidos": roles_ok,
        "roles_nao_encontrados": roles_nao_encontrados,
        "erros": erros,
    }
