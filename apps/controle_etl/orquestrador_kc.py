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
import uuid
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import close_old_connections, connection

from apps.controle_etl.libs.thread_processor import ThreadPoolProcessor

logger = logging.getLogger("etl_identidade")

# Backoff exponencial: atraso máximo de 60 segundos
_ATRASO_BASE = 1.0
_ATRASO_MAXIMO = 60.0
_MAX_TENTATIVAS = 5

# Status HTTP do Keycloak que justificam retry — falha transitória do
# servidor. 400/404/409 são erro de validação/payload (ex.: e-mail
# malformado) e nunca vão se resolver sozinhos com retry.
_STATUS_KC_RETRIAVEIS = {408, 425, 429, 500, 502, 503, 504}

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


def _e_retriavel_kc(exc: BaseException) -> bool:
    """Indica se um erro do Keycloak justifica uma nova tentativa.

    As exceções ``KeycloakGetError``/``PostError``/``PutError`` expõem
    o status HTTP real em ``response_code`` — sem checar esse valor,
    um 400 de validação (ex.: e-mail malformado, que nunca vai se
    resolver com retry) esperava os mesmos 4 backoffs (~15s) de um
    erro transitório de verdade, multiplicando o tempo total do lote
    quando muitos registros têm o mesmo problema de dado na origem.
    Erros sem ``response_code`` (conexão, timeout) continuam sempre
    retriáveis, pois não há status HTTP para inspecionar.

    Args:
        exc: Exceção capturada na chamada ao Keycloak.

    Returns:
        ``True`` se a chamada deve ser repetida.
    """
    codigo = getattr(exc, "response_code", None)
    if codigo is None:
        return True
    return codigo in _STATUS_KC_RETRIAVEIS


def _com_reintento(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Executa função com backoff exponencial em erros transitórios.

    Args:
        fn: Função a executar.
        *args: Argumentos posicionais.
        **kwargs: Argumentos nomeados.

    Returns:
        Resultado da função.

    Raises:
        Exception: Imediatamente se o erro não for retriável (ex.:
            400 de validação), ou após esgotar todas as tentativas
            em caso de falha transitória.
    """
    tentativa = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except _RETRIAVEIS as exc:
            tentativa += 1
            if not _e_retriavel_kc(exc) or tentativa >= _MAX_TENTATIVAS:
                logger.error(
                    "KC: esgotadas %d tentativas: %s",
                    tentativa,
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


def payload_tem_identificador(payload: dict[str, Any]) -> bool:
    """Indica se o payload do token-ms tem ao menos 1 identificador.

    O token-ms rejeita o LOTE inteiro (400) se qualquer item não tiver
    rf/cpf/matricula — usado tanto pela task de lote quanto pela task
    individual de token-ms para descartar consistentemente o mesmo
    tipo de registro incompleto (dado real do CoreSSO, ex. terceiro
    sem nenhum identificador), sem duplicar a regra.

    Args:
        payload: Payload construído por ``construir_payload_token_ms``.

    Returns:
        ``True`` se o payload tem rf, cpf ou matrícula.
    """
    return bool(
        payload.get("rf") or payload.get("cpf") or payload.get("matricula")
    )


def resolver_kc_user_id_de_usuario(
    admin: Any,
    usuario: Any,
    cache: dict[str, str | None] | None = None,
) -> str | None:
    """Resolve o UUID do usuário no Keycloak a partir do staging.

    Reaproveita a mesma resolução de username usada no upsert
    (``_resolver_username``) e a busca por username exato já usada em
    ``atribuir_client_roles_usuario_kc`` (``_resolver_kc_user_id``),
    para uso por chamadores externos que precisam do ``kc_user_id``
    sem repetir a lógica de qual campo identifica o usuário.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        usuario: Instância de staging.
        cache: Cache opcional de username → kc_user_id, para reuso
            entre múltiplas chamadas na mesma execução.

    Returns:
        UUID do usuário no Keycloak, ou ``None`` se não encontrado.
    """
    return _resolver_kc_user_id(
        admin, _resolver_username(usuario), cache if cache is not None else {}
    )


def _montar_permissoes(gru_ids: list[str]) -> list[dict[str, Any]]:
    """Busca e deduplica as permissões de módulo dos grupos informados.

    Um usuário pode ter mais de um grupo concedendo permissão ao
    mesmo módulo — nesse caso, as flags são combinadas por OR lógico
    (basta um grupo conceder a ação para o usuário tê-la).

    Args:
        gru_ids: IDs dos grupos CoreSSO do usuário.

    Returns:
        Lista de permissões por módulo, deduplicada por
        ``(sistema_id, modulo_id)``.
    """
    from apps.extracao.tasks import (  # noqa: PLC0415
        buscar_permissoes_usuario_coresso,
    )

    permissoes: dict[tuple[Any, Any], dict[str, Any]] = {}
    for linha in buscar_permissoes_usuario_coresso(gru_ids):
        chave = (linha["sis_id"], linha["mod_id"])
        atual = permissoes.setdefault(
            chave,
            {
                "sistema_id": linha["sis_id"],
                "sistema_nome": (linha["sis_nome"] or "").strip(),
                "modulo_id": linha["mod_id"],
                "modulo_nome": (linha["mod_nome"] or "").strip(),
                "consultar": False,
                "inserir": False,
                "alterar": False,
                "excluir": False,
            },
        )
        atual["consultar"] = atual["consultar"] or bool(linha["consultar"])
        atual["inserir"] = atual["inserir"] or bool(linha["inserir"])
        atual["alterar"] = atual["alterar"] or bool(linha["alterar"])
        atual["excluir"] = atual["excluir"] or bool(linha["excluir"])

    return list(permissoes.values())


def montar_payload_perfil(
    dados: dict[str, Any],
    *,
    identificador: str,
    rf: str | None = None,
    nome: str | None = None,
    cpf: str | None = None,
    email: str | None = None,
    dre: str | None = None,
    contrato_externo: bool = False,
) -> dict[str, Any]:
    """Constrói o payload de projeção de perfis a partir de dados do CoreSSO.

    Converte os vínculos usuário↔grupo já buscados (``dados``, no
    formato de ``buscar_dados_usuario_coresso``) em ``perfis``, e
    resolve ``permissoes`` a partir de ``SYS_GrupoPermissao``/
    ``SYS_Modulo`` para os mesmos grupos — sem fazer nenhuma nova
    consulta ao CoreSSO. Usada tanto por
    ``construir_payload_perfil_token_ms`` (pipeline agendado, a
    partir de staging) quanto pelas views HTTP avulsas de
    sincronização/concessão de acesso, que já têm ``dados`` em mãos
    antes de chamar esta função.

    Args:
        dados: Resultado de ``buscar_dados_usuario_coresso``.
        identificador: RF, CPF ou email usado na busca — usado como
            ``login`` de fallback se o CoreSSO não retornar um.
        rf: RF do usuário, se conhecido pelo chamador.
        nome: Nome de fallback, usado se ``dados["nome"]`` for vazio.
        cpf: CPF de fallback, usado se ``dados["cpf"]`` for vazio.
        email: E-mail de fallback, usado se ``dados["email"]`` for
            vazio.
        dre: Código da DRE, se conhecido pelo chamador.
        contrato_externo: Se o usuário é de contrato externo
            (terceiro).

    Returns:
        Payload completo de ``ProjecaoUsuarioSerializer`` do token-ms.
    """
    grupos = [
        grupo
        for sistema in dados["sistemas"].values()
        for grupo in sistema["grupos"]
    ]
    perfis = [
        {"id": str(uuid.uuid4()), "nome": grupo["nome"], "ativo": True}
        for grupo in grupos
    ]
    gru_ids = [grupo["gru_id"] for grupo in grupos]

    return {
        "login": dados["login"] or identificador,
        "rf": rf,
        "nome": dados["nome"] or nome,
        "cpf": dados["cpf"] or cpf,
        "email": dados["email"] or email,
        "situacao": dados["situacao"],
        "dre_codigo": dre,
        "contrato_externo": contrato_externo,
        "perfis": perfis,
        "permissoes": _montar_permissoes(gru_ids),
    }


def construir_payload_perfil_token_ms(usuario: Any) -> dict[str, Any] | None:
    """Constrói o payload de projeção de perfis para o token-ms.

    Wrapper de ``montar_payload_perfil`` para uso no pipeline
    agendado: busca os vínculos usuário↔grupo do CoreSSO
    (``buscar_dados_usuario_coresso``) a partir de um registro de
    staging — a mesma fonte já usada por ``sincronizar_usuario``. A
    query de origem já filtra apenas grupos com ``gru_situacao = 1``,
    então todo vínculo retornado está ativo.

    Args:
        usuario: Instância de staging (mesmo objeto usado em
            ``construir_payload_token_ms``).

    Returns:
        Payload completo de ``ProjecaoUsuarioSerializer`` do token-ms,
        ou ``None`` se o usuário não for encontrado no CoreSSO (ex.:
        usuário de fonte EOL_DB/aluno, sem vínculos de sistema).
    """
    from apps.extracao.tasks import (  # noqa: PLC0415
        buscar_dados_usuario_coresso,
    )

    identificador = (
        getattr(usuario, "rf", None) or usuario.cpf or usuario.email
    )
    if not identificador:
        return None

    dados = buscar_dados_usuario_coresso(identificador)
    if not dados:
        return None

    return montar_payload_perfil(
        dados,
        identificador=identificador,
        rf=getattr(usuario, "rf", None),
        nome=usuario.nome,
        cpf=usuario.cpf,
        email=usuario.email,
        dre=getattr(usuario, "dre", None),
        contrato_externo=_inferir_tipo_usuario(usuario) == "terceiro",
    )


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


_CAMPOS_HASH_EXTRACAO = (
    "nome",
    "cpf",
    "rf",
    "email",
    "situacao",
    "cargo",
    "funcao",
    "lotacao",
    "dre",
    "ue",
    "matricula",
)


def calcular_hash_extracao(usuario: Any) -> str:
    """Calcula o hash do dado de staging usado para decidir reextração.

    Cobre os mesmos campos que compõem os payloads de Keycloak/token-ms
    (``construir_payload_kc``/``construir_payload_token_ms``) — medido
    sobre o staging já resolvido (pós merge/dedup), não sobre o
    ``RegistroIdentidade`` bruto por fonte. Usa ``getattr`` com default
    porque nem todo campo existe em todos os tipos de staging (ex.:
    ``cargo``/``funcao`` só existem em ``UsuarioServidorStaging``).

    Args:
        usuario: Instância de staging do usuário.

    Returns:
        String hexadecimal SHA-256 com 64 caracteres.
    """
    from apps.controle_etl.libs.thread_processor import calcular_hash

    dados = {
        campo: getattr(usuario, campo, None) for campo in _CAMPOS_HASH_EXTRACAO
    }
    return calcular_hash(dados, _CAMPOS_HASH_EXTRACAO)


def resolver_id_origem(usuario: Any) -> str:
    """Resolve o identificador estável do usuário na fonte de origem.

    Prioriza CPF sobre RF — mesma prioridade usada em
    ``_resolver_vencedores`` (apps/staging/tasks.py) para dedup entre
    fontes. Cai para o PK do staging só quando nenhum dos dois existe;
    nesse caso, o registro não tem chave estável entre execuções (o
    staging é recriado a cada rodada), então os 3 hashes nunca vão
    coincidir de uma execução para a próxima.

    Args:
        usuario: Instância de staging do usuário.

    Returns:
        CPF (só dígitos), RF, ou o PK do staging como último recurso.
    """
    return (
        "".join(c for c in (usuario.cpf or "") if c.isdigit())
        or (getattr(usuario, "rf", None) or "").strip()
        or str(usuario.id)
    )


def obter_ou_criar_controle(
    tipo_entidade: str,
    sistema_origem: str,
    id_origem: str,
    realm_destino: str,
    execucao: Any = None,
) -> Any:
    """Busca ou cria o registro de controle de idempotência do usuário.

    Centraliza a chave usada pelos 3 hashes (extração, Keycloak,
    token-ms) — reaproveitada tanto pelo provisionamento Keycloak
    quanto pela task individual de carga no token-ms, que precisam
    localizar exatamente a MESMA linha.

    Args:
        tipo_entidade: Um de ``ControleProvisionamento.TipoEntidade``.
        sistema_origem: Fonte do registro (se1426, coresso, eol_alunos).
        id_origem: CPF/RF/matrícula que identifica o registro na fonte.
        realm_destino: Realm Keycloak de destino.
        execucao: Instância de ExecucaoETL para rastreamento.

    Returns:
        Instância de ``ControleProvisionamento`` (criada se necessário).
    """
    from apps.controle_etl.models import ControleProvisionamento

    controle, _ = ControleProvisionamento.objects.get_or_create(
        tipo_entidade=tipo_entidade,
        sistema_origem=sistema_origem,
        id_origem=id_origem,
        realm_destino=realm_destino,
        defaults={
            "hash_keycloak": "",
            "ultima_execucao": execucao,
        },
    )
    return controle


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
    realm: str = settings.KEYCLOAK_REALM,
    execucao: Any = None,
    forcar_atualizacao: bool = False,
) -> dict[str, Any]:
    """Cria ou atualiza um usuário no Keycloak via upsert idempotente.

    Verifica 2 dos 3 hashes independentes do registro (``hash_extracao``
    e ``hash_keycloak``) antes de decidir se reenvia ao Keycloak — só
    pula quando AMBOS batem com o estado atual do staging. O 3º hash
    (``hash_token_ms``) é calculado aqui também, mesmo quando o
    Keycloak é ignorado, para que o chamador saiba se o token-ms
    precisa ser disparado independentemente do resultado do Keycloak.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        usuario: Instância de staging do usuário.
        realm: Realm Keycloak de destino.
        execucao: Instância de ExecucaoETL para rastreamento.
        forcar_atualizacao: Ignora cache de hash e força
            update no Keycloak mesmo sem mudança de dados.

    Returns:
        Dicionário com ``acao``, ``kc_user_id``, ``hash_keycloak`` e
        ``token_ms_pendente`` (``True`` se o payload do token-ms mudou
        desde a última confirmação, mesmo que o Keycloak seja ignorado).
    """
    payload = construir_payload_kc(usuario)
    roles_realm = payload.pop("realmRoles", []) or []
    grupos = payload.pop("groups", []) or []
    hash_extracao_atual = calcular_hash_extracao(usuario)
    hash_keycloak_atual = calcular_hash_conteudo(payload)
    id_origem = resolver_id_origem(usuario)

    from apps.controle_etl.models import ControleProvisionamento

    controle = obter_ou_criar_controle(
        ControleProvisionamento.TipoEntidade.USUARIO,
        usuario.fonte,
        id_origem,
        realm,
        execucao,
    )

    pular_keycloak = (
        not forcar_atualizacao
        and controle.id_destino
        and controle.hash_extracao == hash_extracao_atual
        and controle.hash_keycloak == hash_keycloak_atual
    )

    if pular_keycloak:
        kc_user_id, acao = controle.id_destino, "ignorado"
    else:
        if not controle.id_destino:
            kc_user_id, acao = _criar_usuario_kc(
                admin, usuario, payload, controle
            )
        else:
            kc_user_id, acao = _atualizar_usuario_kc(admin, payload, controle)

        _atribuir_roles_e_grupos(admin, kc_user_id, roles_realm, grupos)

        controle.hash_extracao = hash_extracao_atual
        controle.hash_keycloak = hash_keycloak_atual
        if execucao is not None:
            controle.ultima_execucao = execucao
        controle.erro_sincronizacao = None
        controle.save()

    payload_token = construir_payload_token_ms(usuario)
    hash_token_atual = calcular_hash_conteudo(payload_token)
    token_ms_pendente = controle.hash_token_ms != hash_token_atual

    return {
        "acao": acao,
        "kc_user_id": kc_user_id,
        "hash_keycloak": hash_keycloak_atual,
        "hash_token_ms_atual": hash_token_atual,
        "token_ms_pendente": token_ms_pendente,
        "id_origem": id_origem,
    }


def provisionar_usuarios_kc_em_paralelo(
    admin: Any,
    usuarios: Iterable[Any],
    *,
    realm: str = settings.KEYCLOAK_REALM,
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
        close_old_connections()
        try:
            return provisionar_usuario_kc(
                admin,
                usuario,
                realm=realm,
                execucao=execucao,
                forcar_atualizacao=forcar_atualizacao,
            )
        except Exception as exc:
            return exc
        finally:
            # Cada worker do ThreadPoolProcessor roda seu lote inteiro
            # numa única thread — fechar a conexão aqui (por usuário, não
            # por lote) evita reter transações/locks abertos se um item
            # do meio do lote falhar antes do fim do lote inteiro.
            connection.close()

    processor = ThreadPoolProcessor(
        max_workers=workers, prefixo_log="provisionamento_kc"
    )
    return processor.processar(list(usuarios), _provisionar)


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
        f"default-roles-{settings.KEYCLOAK_REALM}",
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


_SEM_ROLE = object()  # sentinel: grupo sem perfil correspondente


def _garantir_role_provisionada(admin: Any, perfil: Any, sistema: Any) -> bool:
    """Provisiona a role do perfil no KC se ainda não existir.

    Returns:
        ``True`` se a role já existe ou foi provisionada com sucesso
        (``perfil.kc_role_id`` preenchido); ``False`` em falha.
    """
    if perfil.kc_role_id:
        return True
    try:
        provisionar_role_client_kc(admin, perfil)
    except Exception:
        logger.exception(
            "KC: falha ao provisionar role %s do sistema %s",
            perfil.kc_role_nome,
            sistema.nome,
        )
        return False
    perfil.refresh_from_db(fields=["kc_role_id"])
    return bool(perfil.kc_role_id)


def _atribuir_role_grupo(
    admin: Any, kc_user_id: str, sistema: Any, grupo: dict
) -> str | None | object:
    """Atribui a role de um grupo CoreSSO ao usuário no KC.

    Returns:
        Nome da role atribuída em sucesso; ``None`` em falha ao
        provisionar/atribuir a role; ``_SEM_ROLE`` se o grupo não tem
        perfil correspondente em staging (não conta como erro nem
        sucesso — apenas não há role a atribuir).
    """
    from apps.staging.models import PerfilCoressoStaging  # noqa: PLC0415

    perfil = PerfilCoressoStaging.objects.filter(
        coresso_gru_id=grupo["gru_id"]
    ).first()
    if not perfil:
        return _SEM_ROLE
    if not _garantir_role_provisionada(admin, perfil, sistema):
        return None
    try:
        _com_reintento(
            admin.assign_client_role,
            kc_user_id,
            sistema.kc_client_uuid,
            [{"id": perfil.kc_role_id, "name": perfil.kc_role_nome}],
        )
    except Exception:
        return None
    return perfil.kc_role_nome or ""


def _atribuir_roles_sistema(
    admin: Any, kc_user_id: str, sis_data: dict
) -> dict:
    """Atribui roles de um sistema ao usuário no KC.

    Provisiona sob demanda o client e as roles que ainda não
    existem no Keycloak, para que a sincronização de usuário
    nunca fique bloqueada por um cadastro pendente.
    """
    from apps.staging.models import SistemaStaging  # noqa: PLC0415

    sistema = SistemaStaging.objects.filter(
        coresso_sis_id=sis_data["sis_id"]
    ).first()
    if not sistema:
        return {
            "sistema": sis_data["nome"],
            "status": "sem client no KC",
            "roles": [],
            "erros": 0,
        }

    if not sistema.kc_client_uuid:
        try:
            provisionar_client_kc(admin, sistema)
        except Exception:
            logger.exception(
                "KC: falha ao provisionar client do sistema %s",
                sistema.nome,
            )
            return {
                "sistema": sis_data["nome"],
                "status": "sem client no KC",
                "roles": [],
                "erros": 0,
            }

    roles_ok: list[str] = []
    erros = 0
    for grupo in sis_data.get("grupos", []):
        resultado = _atribuir_role_grupo(admin, kc_user_id, sistema, grupo)
        if resultado is _SEM_ROLE:
            continue
        if resultado is None:
            erros += 1
        else:
            roles_ok.append(resultado)

    return {
        "sistema": sis_data["nome"],
        "client_id": sistema.kc_client_id,
        "roles": roles_ok,
        "erros": erros,
    }


def _upsert_coresso_kc(
    admin: Any, dados_coresso: dict
) -> tuple[str | None, str, str, str]:
    """Monta payload a partir de dados CoreSSO e faz upsert no KC.

    Returns:
        (kc_user_id, acao, username, nome)
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
    return kc_user_id, acao, username, nome


def sincronizar_usuario_kc(
    admin: Any,
    dados_coresso: dict,
    *,
    realm: str = settings.KEYCLOAK_REALM,
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
    kc_user_id, acao, username, nome = _upsert_coresso_kc(admin, dados_coresso)
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


def _criar_role_kc(
    admin: Any,
    sistema: Any,
    sis_id: int,
    role_name: str,
    perfil: Any | None,
) -> Any | None:
    """Cria um client role no Keycloak e registra no staging."""
    from apps.staging.models import PerfilCoressoStaging  # noqa: PLC0415

    payload = {
        "name": role_name,
        "description": "Criado via API conceder-acesso",
        "clientRole": True,
        "attributes": {
            "coresso_sis_id": [str(sis_id)],
        },
    }
    try:
        _com_reintento(
            admin.create_client_role,
            sistema.kc_client_uuid,
            payload,
            skip_exists=True,
        )
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            logger.warning(
                "KC: falha ao criar role %s no client %s: %s",
                role_name,
                sistema.kc_client_id,
                exc,
            )
            return None

    try:
        existente = _com_reintento(
            admin.get_client_role,
            sistema.kc_client_uuid,
            role_name,
        )
    except Exception:
        return None

    role_id = (existente or {}).get("id")
    if not role_id:
        return None

    if not perfil:
        perfil, _ = PerfilCoressoStaging.objects.get_or_create(
            coresso_sis_id=sis_id,
            nome=role_name,
            defaults={
                "coresso_gru_id": f"api-{sis_id}-{role_name}",
                "sistema": sistema,
                "kc_role_nome": role_name,
                "kc_role_id": role_id,
                "situacao_provisionamento": (
                    PerfilCoressoStaging.SituacaoProvisionamento.PROVISIONADO
                ),
            },
        )
        if not perfil.kc_role_id:
            perfil.kc_role_id = role_id
            perfil.kc_role_nome = role_name
            perfil.situacao_provisionamento = (
                PerfilCoressoStaging.SituacaoProvisionamento.PROVISIONADO
            )
            perfil.save(
                update_fields=[
                    "kc_role_id",
                    "kc_role_nome",
                    "situacao_provisionamento",
                    "atualizado_em",
                ]
            )
    else:
        perfil.kc_role_id = role_id
        perfil.kc_role_nome = perfil.kc_role_nome or role_name
        perfil.situacao_provisionamento = (
            PerfilCoressoStaging.SituacaoProvisionamento.PROVISIONADO
        )
        perfil.save(
            update_fields=[
                "kc_role_id",
                "kc_role_nome",
                "situacao_provisionamento",
                "atualizado_em",
            ]
        )

    logger.info(
        "KC: role %s criado no client %s (id=%s)",
        role_name,
        sistema.kc_client_id,
        role_id,
    )
    return perfil


def _conceder_roles_sistema_kc(
    admin: Any,
    kc_user_id: str,
    sis_id: int,
    nomes_roles: list[str],
    *,
    username: str = "",
) -> dict[str, Any]:
    """Resolve e atribui client roles de um sistema a um usuário Keycloak.

    Núcleo reaproveitável de ``conceder_acesso_kc``: dado um
    ``kc_user_id`` já existente (upsert feito por quem chama), busca
    (ou cria, se necessário) os roles do sistema informado e os
    atribui ao usuário. Não faz upsert de usuário — isso é
    responsabilidade de quem chama, para poder ser reaproveitado
    tanto a partir de dados do CoreSSO (``conceder_acesso_kc``)
    quanto de um usuário criado manualmente
    (``criar_usuario_manual``, na view).

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        kc_user_id: ID do usuário já upsertado no Keycloak.
        sis_id: ``coresso_sis_id`` do sistema alvo.
        nomes_roles: Nomes dos perfis/roles a conceder.
        username: Usado apenas para mensagens de log.

    Returns:
        Dict com ``sistema``, ``client_id``, ``roles_atribuidos``,
        ``roles_nao_encontrados`` e ``erros``. Se o sistema não for
        encontrado, retorna apenas ``erro``.
    """
    from apps.staging.models import (  # noqa: PLC0415
        PerfilCoressoStaging,
        SistemaStaging,
    )

    sistema = SistemaStaging.objects.filter(coresso_sis_id=sis_id).first()
    if not sistema or not sistema.kc_client_uuid:
        return {
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
            perfil = _criar_role_kc(
                admin,
                sistema,
                sis_id,
                role_name,
                perfil,
            )
            if not perfil:
                roles_nao_encontrados.append(role_name)
                erros += 1
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

    return {
        "sistema": sistema.nome,
        "client_id": sistema.kc_client_id,
        "roles_atribuidos": roles_ok,
        "roles_nao_encontrados": roles_nao_encontrados,
        "erros": erros,
    }


def conceder_acesso_kc(
    admin: Any,
    dados_coresso: dict,
    sis_id: int,
    nomes_roles: list[str],
    *,
    realm: str = settings.KEYCLOAK_REALM,
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
    kc_user_id, acao, username, nome = _upsert_coresso_kc(admin, dados_coresso)
    if not kc_user_id:
        return {"acao": "erro", "motivo": "sem kc_user_id"}

    resultado_roles = _conceder_roles_sistema_kc(
        admin, kc_user_id, sis_id, nomes_roles, username=username
    )
    if "erro" in resultado_roles:
        return {"acao": acao, "kc_user_id": kc_user_id, **resultado_roles}

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
        **resultado_roles,
    }
