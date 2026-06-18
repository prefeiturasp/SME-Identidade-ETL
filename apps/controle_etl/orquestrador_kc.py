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
                " — aguardando %.1fs: %s",
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
    cpf = "".join(c for c in (usuario.cpf or "") if c.isdigit())
    if cpf:
        return cpf
    rf = (getattr(usuario, "rf", None) or "").strip()
    if rf:
        return rf
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
) -> dict[str, Any]:
    """Cria ou atualiza um usuário no Keycloak via upsert idempotente.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        usuario: Instância de staging do usuário.
        realm: Realm Keycloak de destino.
        execucao: Instância de ExecucaoETL para rastreamento.

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
        not criado
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
) -> list[dict[str, Any] | Exception]:
    """Provisiona usuários no Keycloak em paralelo via threads.

    Cada chamada a ``provisionar_usuario_kc`` é uma operação de
    rede (Keycloak Admin API) — paralelizar via threads reduz
    drasticamente o tempo total em volumes grandes (centenas de
    milhares de usuários), já que o trabalho é I/O-bound.

    Args:
        admin: Cliente KeycloakAdmin autenticado, compartilhado
            entre as threads (sessão HTTP do python-keycloak).
        usuarios: Iterável de instâncias de staging do usuário.
        realm: Realm Keycloak de destino.
        execucao: Instância de ExecucaoETL para rastreamento.
        max_workers: Número de threads (padrão: configuração interna).

    Returns:
        Lista de resultados na mesma ordem de ``usuarios`` — cada
        item é o dict de ``provisionar_usuario_kc`` ou a Exception
        capturada para aquele usuário específico.
    """
    workers = max_workers or _PROVISIONAMENTO_MAX_WORKERS

    def _provisionar(usuario: Any) -> dict[str, Any] | Exception:
        def _chamada() -> dict[str, Any]:
            return provisionar_usuario_kc(
                admin, usuario, realm=realm, execucao=execucao
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
