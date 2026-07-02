"""Tasks de extração das fontes  SE1426, CoreSSO e EOL_DB.

Extrai dados para memória (dataclasses RegistroIdentidade) e devolve
a lista para o chamador. A persistência no staging (apps.staging)
é responsabilidade de quem consome o retorno — ver
``apps.staging.tasks.persistir_extracao_staging``.

Controle incremental por watermark é persistido no SYNC_REC_DB
via MarcaDaguaExtracao.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("etl_identidade")

_TAMANHO_LOTE = 500


# ---------------------------------------------------------------------------
# Dataclass de identidade em memória
# ---------------------------------------------------------------------------


@dataclass
class RegistroIdentidade:
    """Representa um registro de identidade extraído de uma fonte legada.

    Trafega em memória pelo pipeline sem ser persistido.
    """

    fonte: str  # se1426 | coresso | eol_alunos
    tipo: str  # servidor | aluno | terceiro
    rf: str | None = None
    cpf: str | None = None
    nome: str | None = None
    email: str | None = None
    situacao: str | None = None
    cargo: str | None = None
    funcao: str | None = None
    lotacao: str | None = None
    lotacao_nome: str | None = None
    dre: str | None = None
    ue: str | None = None
    matricula: str | None = None
    cod_escola: str | None = None
    turma: str | None = None
    tipo_acesso: str | None = None
    dados_extras: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers de conexão SQL Server
# ---------------------------------------------------------------------------


def _string_conexao_se1426() -> str:
    """Monta a string de conexão FreeTDS para SE1426.

    Returns:
        String de conexão no formato ODBC/FreeTDS.
    """
    return (
        f"DRIVER={{FreeTDS}};"
        f"SERVER={settings.SE1426_DB_SERVIDOR};"
        f"PORT=1433;"
        f"DATABASE={settings.SE1426_DB_NOME};"
        f"UID={settings.SE1426_DB_USUARIO};"
        f"PWD={settings.SE1426_DB_SENHA};"
        f"TDS_Version=7.4;ClientCharset=UTF-8;"
    )


def _string_conexao_coresso() -> str:
    """Monta a string de conexão FreeTDS para CoreSSO.

    Returns:
        String de conexão no formato ODBC/FreeTDS.
    """
    return (
        f"DRIVER={{FreeTDS}};"
        f"SERVER={settings.CORESSO_DB_SERVIDOR};"
        f"PORT=1433;"
        f"DATABASE={settings.CORESSO_DB_NOME};"
        f"UID={settings.CORESSO_DB_USUARIO};"
        f"PWD={settings.CORESSO_DB_SENHA};"
        f"TDS_Version=7.4;ClientCharset=UTF-8;"
    )


def _string_conexao_eol_db() -> str:
    """Monta a string de conexão FreeTDS para EOL_DB a partir da URL.

    EOL_DB roda no mesmo servidor SQL Server do SE1426. A URL em
    ``EOL_DB_STRING_CONEXAO`` segue o formato
    ``mssql+pyodbc://usuario:senha@host:porta/banco?...`` — aqui ela
    é parseada e convertida para a string de conexão FreeTDS usada
    pelo pyodbc (mesmo driver das demais fontes SQL Server).

    Returns:
        String de conexão no formato ODBC/FreeTDS.
    """
    from urllib.parse import unquote, urlparse  # noqa: PLC0415

    url = urlparse(settings.EOL_DB_STRING_CONEXAO)
    return (
        f"DRIVER={{FreeTDS}};"
        f"SERVER={url.hostname};"
        f"PORT={url.port or 1433};"
        f"DATABASE={url.path.lstrip('/')};"
        f"UID={url.username};"
        f"PWD={unquote(url.password or '')};"
        f"TDS_Version=7.4;ClientCharset=UTF-8;"
    )


# ---------------------------------------------------------------------------
# Controle de watermark (persistido no SYNC_REC_DB)
# ---------------------------------------------------------------------------


def _obter_watermark(fonte: str) -> datetime | None:
    """Retorna o último timestamp processado para a fonte.

    Args:
        fonte: Identificador da fonte (se1426, coresso, eol_alunos).

    Returns:
        datetime ou None se nunca processado.
    """
    from apps.controle_etl.models import MarcaDaguaExtracao

    marca = MarcaDaguaExtracao.objects.filter(fonte=fonte).first()
    return marca.ultimo_processado_em if marca else None


def _atualizar_watermark(
    fonte: str,
    ultimo_processado_em: datetime,
    total_processado: int,
    ultima_pagina: int = 0,
) -> None:
    """Atualiza o watermark após extração bem-sucedida.

    Args:
        fonte: Identificador da fonte.
        ultimo_processado_em: Timestamp do último registro processado.
        total_processado: Total de registros processados na execução.
        ultima_pagina: Número da última página processada.
    """
    from apps.controle_etl.models import MarcaDaguaExtracao

    marca, _ = MarcaDaguaExtracao.objects.get_or_create(fonte=fonte)
    marca.ultimo_processado_em = ultimo_processado_em
    marca.ultima_pagina = ultima_pagina
    marca.total_processado = (marca.total_processado or 0) + total_processado
    marca.save(
        update_fields=[
            "ultimo_processado_em",
            "ultima_pagina",
            "total_processado",
            "atualizado_em",
        ]
    )


def _iterar_com_watermark(
    fonte: str, fonte_iter: Iterator[RegistroIdentidade]
) -> Iterator[RegistroIdentidade]:
    """Repassa os registros do iterador, atualizando o watermark ao fim.

    Mantém a extração em streaming (sem acumular tudo em memória)
    enquanto preserva o controle incremental: o watermark só é
    atualizado depois que o iterador é totalmente consumido pelo
    chamador, contabilizando o total de registros repassados.

    Respeita ``settings.ETL_LOTE_MAXIMO`` (0 = sem limite) como teto
    de registros entregues nesta execução — interrompe o consumo de
    ``fonte_iter`` assim que o teto é atingido, útil para testar o
    pipeline com volume reduzido.

    Args:
        fonte: Identificador da fonte (se1426, coresso, eol_alunos).
        fonte_iter: Iterador de RegistroIdentidade da extração.

    Yields:
        Cada RegistroIdentidade recebido de ``fonte_iter``.
    """
    lote_maximo = settings.ETL_LOTE_MAXIMO
    total = 0
    for registro in fonte_iter:
        if lote_maximo and total >= lote_maximo:
            logger.info(
                "%s: limite ETL_LOTE_MAXIMO=%d atingido"
                + " — interrompendo extração",
                fonte,
                lote_maximo,
            )
            break
        total += 1
        yield registro
    if total:
        _atualizar_watermark(
            fonte,
            timezone.now(),
            total,
            ultima_pagina=total // _TAMANHO_LOTE,
        )


# ---------------------------------------------------------------------------
# Extração SE1426
# ---------------------------------------------------------------------------


def _extrair_se1426_sql() -> Iterator[RegistroIdentidade]:
    """Extrai servidores do SE1426 via SQL Server (read-only).

    A view ``v_servidor_sme_serap`` não expõe coluna de data de
    atualização — sem suporte a filtro incremental por enquanto;
    a extração sempre traz a base completa.

    Yields:
        RegistroIdentidade para cada servidor encontrado.
    """
    import pyodbc

    consulta = """
        SELECT
            s.cd_registro_funcional  AS rf,
            s.nm_pessoa              AS nome,
            s.cd_cpf_pessoa          AS cpf,
            s.situacao               AS situacao,
            e.dc_dispositivo         AS email
        FROM v_servidor_sme_serap s
        LEFT JOIN v_servidor_cotic sc
            ON sc.cd_registro_funcional = s.cd_registro_funcional
        LEFT JOIN v_servidor_email_cotic e
            ON e.cd_servidor = sc.cd_servidor
            AND e.dt_fim IS NULL
    """

    conn = pyodbc.connect(
        _string_conexao_se1426(), timeout=settings.SE1426_DB_TIMEOUT
    )
    try:
        cursor = conn.cursor()
        cursor.execute(consulta)
        colunas = [d[0] for d in cursor.description]
        while True:
            linhas = cursor.fetchmany(settings.ETL_CHUNK_SIZE)
            if not linhas:
                break
            for linha in linhas:
                item = dict(zip(colunas, linha, strict=False))
                yield RegistroIdentidade(
                    fonte="se1426",
                    tipo="servidor",
                    rf=item.get("rf"),
                    nome=item.get("nome"),
                    cpf=item.get("cpf"),
                    email=item.get("email"),
                    situacao=((item.get("situacao") or "").lower() or None),
                    dados_extras={
                        k: str(v) if v is not None else None
                        for k, v in item.items()
                    },
                )
    finally:
        conn.close()


def _paginar_api(
    url: str,
    cabecalhos: dict,
    timeout: int,
    desde: datetime | None,
    rotulo: str,
) -> Iterator[dict]:
    import httpx

    pagina = 1
    while True:
        params: dict = {
            "page": pagina,
            "page_size": settings.ETL_CHUNK_SIZE,
        }
        if desde:
            params["data_alteracao_apos"] = desde.strftime("%Y-%m-%d")

        try:
            with httpx.Client(timeout=timeout) as cliente:
                resposta = cliente.get(url, headers=cabecalhos, params=params)
            resposta.raise_for_status()
        except Exception as exc:
            logger.error("%s API: erro na página %d: %s", rotulo, pagina, exc)
            break

        dados = resposta.json()
        itens = dados.get("results") or (
            dados if isinstance(dados, list) else []
        )
        if not itens:
            break

        yield from itens

        if not dados.get("next"):
            break
        pagina += 1


def _extrair_se1426_api(
    desde: datetime | None = None,
) -> Iterator[RegistroIdentidade]:
    """Extrai servidores do SE1426 via API REST (fallback).

    Yields:
        RegistroIdentidade para cada servidor encontrado.
    """
    url_base = settings.SE1426_API_URL.rstrip("/")
    cabecalhos = {"Authorization": f"Bearer {settings.SE1426_API_TOKEN}"}

    for item in _paginar_api(
        f"{url_base}/servidores/",
        cabecalhos,
        settings.SE1426_API_TIMEOUT,
        desde,
        "SE1426",
    ):
        yield RegistroIdentidade(
            fonte="se1426",
            tipo="servidor",
            rf=item.get("rf") or item.get("cd_registro_funcional"),
            nome=item.get("nome") or item.get("nm_pessoa"),
            cpf=item.get("cpf") or item.get("cd_cpf_pessoa"),
            email=item.get("email"),
            situacao=(item.get("situacao") or "").lower() or None,
            dados_extras=item,
        )


def extrair_se1426(
    data_referencia: str | None = None,
) -> Iterator[RegistroIdentidade]:
    """Extrai dados institucionais da fonte SE1426 em streaming.

    Usa SQL direto quando configurado, com fallback para API REST.
    Aplica watermark para extração incremental (delta d-1). Os
    registros são entregues conforme extraídos, sem acumular a
    fonte inteira em memória — essencial para volumes grandes
    (centenas de milhares de registros).

    Args:
        data_referencia: Data ISO para replay
            (sobrepõe o watermark persistido).

    Yields:
        RegistroIdentidade conforme extraído da fonte.
    """
    desde: datetime | None = None
    if data_referencia:
        desde = datetime.fromisoformat(data_referencia)
    else:
        desde = _obter_watermark("se1426")

    logger.info("extrair_se1426 — desde=%s", desde)

    if settings.SE1426_DB_SERVIDOR:
        fonte_iter = _extrair_se1426_sql()
    else:
        fonte_iter = _extrair_se1426_api(desde=desde)

    yield from _iterar_com_watermark("se1426", fonte_iter)


# ---------------------------------------------------------------------------
# Extração CoreSSO
# ---------------------------------------------------------------------------


def _extrair_coresso_sql(
    desde: datetime | None = None,
) -> Iterator[RegistroIdentidade]:
    """Extrai usuários legados do CoreSSO via SQL Server (read-only).

    Args:
        desde: Data mínima de alteração para extração incremental
            (filtra por ``usu_dataAlteracao``); None extrai tudo.

    Yields:
        RegistroIdentidade para cada usuário encontrado.
    """
    import pyodbc

    # tdo_id fixo de SYS_TipoDocumentacao para o tipo "CPF" (sigla CPF).
    tdo_id_cpf = "2CEEED03-63EB-E011-9B36-00155D033206"

    filtro_data = ""
    if desde:
        dt = desde.strftime("%Y-%m-%d")
        filtro_data = f"WHERE u.usu_dataAlteracao >= '{dt}'"

    consulta = f"""
        SELECT
            u.usu_login          AS login,
            doc.psd_numero        AS cpf,
            pes.pes_nome          AS nome,
            u.usu_email           AS email,
            u.usu_situacao        AS situacao,
            u.usu_dataAlteracao   AS dt_atualizacao
        FROM SYS_Usuario u
        INNER JOIN PES_Pessoa pes ON pes.pes_id = u.pes_id
        LEFT JOIN PES_PessoaDocumento doc
            ON doc.pes_id = pes.pes_id
            AND doc.tdo_id = '{tdo_id_cpf}'
        {filtro_data}
    """

    conn = pyodbc.connect(
        _string_conexao_coresso(),
        timeout=settings.CORESSO_DB_TIMEOUT,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(consulta)
        colunas = [d[0] for d in cursor.description]
        while True:
            linhas = cursor.fetchmany(settings.ETL_CHUNK_SIZE)
            if not linhas:
                break
            for linha in linhas:
                item = dict(zip(colunas, linha, strict=False))
                yield RegistroIdentidade(
                    fonte="coresso",
                    tipo="terceiro",
                    cpf=(item.get("cpf") or "").strip() or None,
                    nome=item.get("nome"),
                    email=item.get("email"),
                    situacao=(
                        "ativo"
                        if str(item.get("situacao", "")).strip() == "1"
                        else "inativo"
                    ),
                    tipo_acesso="legado-coresso",
                    dados_extras={
                        k: str(v) if v is not None else None
                        for k, v in item.items()
                        if k != "dt_atualizacao"
                    },
                )
    finally:
        conn.close()


def _extrair_coresso_api(
    desde: datetime | None = None,
) -> Iterator[RegistroIdentidade]:
    """Extrai usuários do CoreSSO via API REST (fallback).

    Yields:
        RegistroIdentidade para cada usuário encontrado.
    """
    url_base = settings.CORESSO_API_URL.rstrip("/")
    cabecalhos = {"Authorization": f"Bearer {settings.CORESSO_API_TOKEN}"}

    for item in _paginar_api(
        f"{url_base}/usuarios/",
        cabecalhos,
        settings.CORESSO_DB_TIMEOUT,
        desde,
        "CoreSSO",
    ):
        yield RegistroIdentidade(
            fonte="coresso",
            tipo="terceiro",
            cpf=(item.get("cpf") or "").strip() or None,
            nome=item.get("nome") or item.get("pes_nome"),
            email=item.get("email") or item.get("pes_email"),
            situacao=(
                "ativo"
                if str(item.get("situacao", "")).strip() in ("1", "ativo")
                else "inativo"
            ),
            tipo_acesso="legado-coresso",
            dados_extras=item,
        )


def extrair_coresso(
    data_referencia: str | None = None,
) -> Iterator[RegistroIdentidade]:
    """Extrai usuários e perfis legados da fonte CoreSSO em streaming.

    Usa SQL direto quando configurado, com fallback para API REST.
    Os registros são entregues conforme extraídos, sem acumular a
    fonte inteira em memória.

    Args:
        data_referencia: Data ISO para replay.

    Yields:
        RegistroIdentidade conforme extraído da fonte.
    """
    desde: datetime | None = None
    if data_referencia:
        desde = datetime.fromisoformat(data_referencia)
    else:
        desde = _obter_watermark("coresso")

    logger.info("extrair_coresso — desde=%s", desde)

    if settings.CORESSO_DB_SERVIDOR:
        fonte_iter = _extrair_coresso_sql(desde=desde)
    elif settings.CORESSO_API_URL:
        fonte_iter = _extrair_coresso_api(desde=desde)
    else:
        logger.warning("CoreSSO: sem configuração de banco ou API.")
        return

    yield from _iterar_com_watermark("coresso", fonte_iter)


# ---------------------------------------------------------------------------
# Extração EOL_DB — Alunos
# ---------------------------------------------------------------------------


def _extrair_eol_alunos_sql() -> Iterator[RegistroIdentidade]:
    """Extrai alunos do EOL_DB via SQL Server (read-only).

    As tabelas aluno/v_matricula_cotic/matricula_turma_escola não
    expõem uma coluna de data de atualização utilizável para filtro
    incremental — sem suporte a watermark por enquanto; a extração
    sempre traz a base completa.

    Yields:
        RegistroIdentidade para cada aluno encontrado.
    """
    import pyodbc

    consulta = """
        SELECT
            a.cd_aluno                                  AS matricula,
            a.nm_aluno                                  AS nome,
            a.cd_cpf_aluno                               AS cpf,
            mat.st_matricula                             AS situacao,
            CAST(mt.cd_turma_escola AS VARCHAR(20))      AS cod_escola,
            CAST(mat.cd_serie_ensino AS VARCHAR(20))     AS turma,
            CAST(ue.cd_unidade_educacao AS VARCHAR(20))  AS cod_ue,
            CAST(ue.cd_unidade_administrativa_referencia
                AS VARCHAR(20))                          AS cod_dre
        FROM aluno a
        INNER JOIN v_matricula_cotic mat ON mat.cd_aluno = a.cd_aluno
        INNER JOIN matricula_turma_escola mt
            ON mt.cd_matricula = mat.cd_matricula
        INNER JOIN v_cadastro_unidade_educacao ue
            ON ue.cd_unidade_educacao = mat.cd_escola
    """

    conn = pyodbc.connect(
        _string_conexao_eol_db(), timeout=settings.SE1426_DB_TIMEOUT
    )
    try:
        cursor = conn.cursor()
        cursor.execute(consulta)
        colunas = [d[0] for d in cursor.description]
        while True:
            linhas = cursor.fetchmany(settings.ETL_CHUNK_SIZE)
            if not linhas:
                break
            for linha in linhas:
                item = dict(zip(colunas, linha, strict=False))
                yield RegistroIdentidade(
                    fonte="eol_alunos",
                    tipo="aluno",
                    matricula=item.get("matricula"),
                    nome=item.get("nome"),
                    cpf=str(item.get("cpf") or "").strip() or None,
                    situacao=(item.get("situacao") or "").lower() or None,
                    cod_escola=item.get("cod_ue"),
                    turma=item.get("turma"),
                    dre=item.get("cod_dre"),
                    ue=item.get("cod_ue"),
                    dados_extras={
                        k: str(v) if v is not None else None
                        for k, v in item.items()
                    },
                )
    finally:
        conn.close()


def extrair_eol_alunos(
    data_referencia: str | None = None,
) -> Iterator[RegistroIdentidade]:
    """Extrai alunos da fonte EOL_DB em streaming.

    Os registros são entregues conforme extraídos, sem acumular a
    fonte inteira em memória.

    Args:
        data_referencia: Data ISO para replay.

    Yields:
        RegistroIdentidade conforme extraído da fonte.
    """
    desde: datetime | None = None
    if data_referencia:
        desde = datetime.fromisoformat(data_referencia)
    else:
        desde = _obter_watermark("eol_alunos")

    logger.info("extrair_eol_alunos — desde=%s", desde)

    if not settings.EOL_DB_STRING_CONEXAO and not settings.SE1426_DB_SERVIDOR:
        logger.warning("EOL_DB: sem configuração de conexão — skipping.")
        return

    yield from _iterar_com_watermark("eol_alunos", _extrair_eol_alunos_sql())


# ---------------------------------------------------------------------------
# Extração de sistemas e perfis CoreSSO (para provisionamento de clients)
# ---------------------------------------------------------------------------


def buscar_grupos_coresso_por_login(login: str) -> list[dict]:
    """Busca os grupos CoreSSO de um usuário pelo login.

    Args:
        login: RF ou CPF do usuário.

    Returns:
        Lista de dicionários com ``gru_id`` e ``nome``.
    """
    if not login or not settings.CORESSO_DB_SERVIDOR:
        return []

    import pyodbc

    consulta = """
        SELECT
            g.gru_id    AS gru_id,
            g.gru_nome  AS nome
        FROM SYS_Grupo g
        INNER JOIN SYS_UsuarioGrupo ug ON ug.gru_id = g.gru_id
        INNER JOIN SYS_Usuario u ON u.usu_id = ug.usu_id
        WHERE u.usu_login = ?
          AND g.gru_situacao = 1
    """
    try:
        conn = pyodbc.connect(
            _string_conexao_coresso(),
            timeout=settings.CORESSO_DB_TIMEOUT,
        )
        try:
            cursor = conn.cursor()
            cursor.execute(consulta, (login,))
            return [
                {"gru_id": row[0], "nome": row[1]} for row in cursor.fetchall()
            ]
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "Falha ao buscar grupos CoreSSO para '%s': %s", login, exc
        )
        return []


def extrair_sistemas_coresso() -> int:
    """Extrai sistemas (SYS_Sistema) do CoreSSO.

    Persiste no SYNC_REC_DB apenas os metadados de provisionamento
    (client IDs Keycloak), não dados de negócio.

    Returns:
        Total de sistemas processados.
    """
    if not settings.CORESSO_DB_SERVIDOR:
        logger.warning("CoreSSO SQL não configurado — skipping sistemas.")
        return 0

    import pyodbc

    from apps.staging.models import SistemaStaging

    excluidos = settings.CORESSO_EXCLUDE_SISTEMA_IDS
    filtro_exclusao = (
        f"WHERE sis_id NOT IN ({','.join(str(i) for i in excluidos)})"
        if excluidos
        else ""
    )
    consulta = f"""
        SELECT
            sis_id, sis_nome, sis_descricao,
            sis_tipoAutenticacao, sis_urlIntegracao,
            sis_caminhoLogout, sis_situacao
        FROM SYS_Sistema {filtro_exclusao} ORDER BY sis_id
    """
    conn = pyodbc.connect(
        _string_conexao_coresso(),
        timeout=settings.CORESSO_DB_TIMEOUT,
    )
    total = 0
    try:
        cursor = conn.cursor()
        cursor.execute(consulta)
        colunas = [d[0] for d in cursor.description]
        for linha in cursor.fetchall():
            item = dict(zip(colunas, linha, strict=False))
            SistemaStaging.objects.update_or_create(
                coresso_sis_id=item["sis_id"],
                defaults={
                    "nome": item.get("sis_nome") or "",
                    "url_callback": item.get("sis_urlIntegracao"),
                    "url_logout": item.get("sis_caminhoLogout"),
                    "situacao": item.get("sis_situacao", 1),
                },
            )
            total += 1
    finally:
        conn.close()

    logger.info("extrair_sistemas_coresso — %d sistemas", total)
    return total


def extrair_perfis_coresso() -> int:
    """Extrai perfis/grupos (SYS_Grupo) do CoreSSO.

    Persiste no SYNC_REC_DB apenas os metadados de mapeamento
    para roles Keycloak.

    Returns:
        Total de perfis processados.
    """
    if not settings.CORESSO_DB_SERVIDOR:
        logger.warning("CoreSSO SQL não configurado — skipping perfis.")
        return 0

    import pyodbc

    from apps.staging.models import PerfilCoressoStaging, SistemaStaging

    excluidos = settings.CORESSO_EXCLUDE_SISTEMA_IDS
    filtro_exclusao = (
        f"WHERE sis_id NOT IN ({','.join(str(i) for i in excluidos)})"
        if excluidos
        else ""
    )
    consulta = f"""
        SELECT gru_id, gru_nome, sis_id, vis_id, gru_situacao
        FROM SYS_Grupo {filtro_exclusao} ORDER BY sis_id, gru_id
    """
    conn = pyodbc.connect(
        _string_conexao_coresso(),
        timeout=settings.CORESSO_DB_TIMEOUT,
    )
    total = 0
    try:
        cursor = conn.cursor()
        cursor.execute(consulta)
        colunas = [d[0] for d in cursor.description]
        for linha in cursor.fetchall():
            item = dict(zip(colunas, linha, strict=False))
            sistema = SistemaStaging.objects.filter(
                coresso_sis_id=item["sis_id"]
            ).first()
            PerfilCoressoStaging.objects.update_or_create(
                coresso_gru_id=str(item["gru_id"]),
                defaults={
                    "nome": item.get("gru_nome") or "",
                    "coresso_sis_id": item["sis_id"],
                    "sistema": sistema,
                    "kc_role_nome": _slugificar_role(
                        item.get("gru_nome") or ""
                    ),
                    "situacao": item.get("gru_situacao", 1),
                },
            )
            total += 1
    finally:
        conn.close()

    logger.info("extrair_perfis_coresso — %d perfis", total)
    return total


def extrair_vinculos_usuario_grupo_coresso(
    *,
    sis_id: int | None = None,
    gru_id: str | None = None,
) -> Iterator[dict]:
    """Extrai vínculos usuário↔grupo ativos do CoreSSO.

    Args:
        sis_id: Filtra por sistema (``SYS_Grupo.sis_id``).
            None = todos os sistemas.
        gru_id: Filtra por grupo (``SYS_Grupo.gru_id``).
            None = todos os grupos.

    Yields:
        Dicionário com ``login``, ``cpf``, ``gru_id``,
        ``gru_nome`` e ``sis_id``.
    """
    if not settings.CORESSO_DB_SERVIDOR:
        logger.warning("CoreSSO SQL não configurado" + " — skipping vínculos.")
        return

    import pyodbc

    filtros_extra: list[str] = []
    excluidos = settings.CORESSO_EXCLUDE_SISTEMA_IDS
    if excluidos:
        filtros_extra.append(
            "AND g.sis_id NOT IN" + f" ({','.join(str(i) for i in excluidos)})"
        )
    if sis_id is not None:
        filtros_extra.append(f"AND g.sis_id = {int(sis_id)}")
    if gru_id is not None:
        filtros_extra.append(f"AND g.gru_id = '{gru_id}'")
    clausulas = "\n          ".join(filtros_extra)
    consulta = f"""
        SELECT
            u.usu_login     AS login,
            doc.psd_numero  AS cpf,
            pes.pes_nome    AS nome,
            u.usu_email     AS email,
            g.gru_id        AS gru_id,
            g.gru_nome      AS gru_nome,
            g.sis_id        AS sis_id
        FROM SYS_Usuario u
        INNER JOIN PES_Pessoa pes
            ON pes.pes_id = u.pes_id
        LEFT JOIN PES_PessoaDocumento doc
            ON doc.pes_id = pes.pes_id
            AND doc.tdo_id = (
                SELECT TOP 1 tdo_id
                FROM SYS_TipoDocumentacao
                WHERE tdo_nome LIKE '%CPF%'
            )
        INNER JOIN SYS_UsuarioGrupo ug
            ON ug.usu_id = u.usu_id
        INNER JOIN SYS_Grupo g
            ON g.gru_id = ug.gru_id
        WHERE g.gru_situacao = 1
          AND u.usu_situacao = 1
          {clausulas}
        ORDER BY u.usu_login, g.sis_id
    """
    lote_maximo = settings.ETL_LOTE_MAXIMO
    conn = pyodbc.connect(
        _string_conexao_coresso(),
        timeout=settings.CORESSO_DB_TIMEOUT,
    )
    total = 0
    try:
        cursor = conn.cursor()
        cursor.execute(consulta)
        colunas = [d[0] for d in cursor.description]
        while True:
            linhas = cursor.fetchmany(settings.ETL_CHUNK_SIZE)
            if not linhas:
                break
            for linha in linhas:
                if lote_maximo and total >= lote_maximo:
                    logger.info(
                        "vinculos: limite" + " ETL_LOTE_MAXIMO=%d atingido",
                        lote_maximo,
                    )
                    return
                total += 1
                yield dict(zip(colunas, linha, strict=False))
    finally:
        conn.close()


def buscar_dados_usuario_coresso(
    identificador: str,
) -> dict | None:
    """Busca dados completos de um usuário no CoreSSO.

    Busca por login (RF), CPF ou email. Retorna dados
    pessoais e todos os vínculos sistema↔grupo.

    Args:
        identificador: RF, CPF ou email do usuário.

    Returns:
        Dict com dados do usuário ou None se não encontrado.
    """
    if not settings.CORESSO_DB_SERVIDOR:
        return None

    import pyodbc

    ident = identificador.strip()
    if "@" in ident:
        filtro = "u.usu_email = ?"
    elif len(ident) > 7:
        filtro = "doc.psd_numero = ?"
    else:
        filtro = "u.usu_login = ?"

    consulta = f"""
        SELECT
            u.usu_login     AS login,
            doc.psd_numero  AS cpf,
            pes.pes_nome    AS nome,
            u.usu_email     AS email,
            u.usu_situacao  AS situacao,
            g.gru_id        AS gru_id,
            g.gru_nome      AS gru_nome,
            g.sis_id        AS sis_id,
            s.sis_nome      AS sis_nome
        FROM SYS_Usuario u
        INNER JOIN PES_Pessoa pes
            ON pes.pes_id = u.pes_id
        LEFT JOIN PES_PessoaDocumento doc
            ON doc.pes_id = pes.pes_id
            AND doc.tdo_id = (
                SELECT TOP 1 tdo_id
                FROM SYS_TipoDocumentacao
                WHERE tdo_nome LIKE '%CPF%'
            )
        LEFT JOIN SYS_UsuarioGrupo ug
            ON ug.usu_id = u.usu_id
        LEFT JOIN SYS_Grupo g
            ON g.gru_id = ug.gru_id
            AND g.gru_situacao = 1
        LEFT JOIN SYS_Sistema s
            ON s.sis_id = g.sis_id
        WHERE {filtro}
        ORDER BY s.sis_nome, g.gru_nome
    """
    conn = pyodbc.connect(
        _string_conexao_coresso(),
        timeout=settings.CORESSO_DB_TIMEOUT,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(consulta, (ident,))
        rows = cursor.fetchall()
        if not rows:
            return None
        resultado = _montar_resultado_usuario(rows)
    finally:
        conn.close()

    if not resultado.get("email") or not resultado.get("cpf"):
        complemento = buscar_complemento_se1426(resultado.get("login", ""))
        if complemento:
            if not resultado.get("email"):
                resultado["email"] = complemento.get("email", "")
            if not resultado.get("cpf"):
                resultado["cpf"] = complemento.get("cpf", "")

    return resultado


def _montar_resultado_usuario(rows: list) -> dict:
    """Monta o dict de resultado a partir das linhas SQL."""
    r0 = rows[0]
    resultado: dict = {
        "login": (r0[0] or "").strip(),
        "cpf": (r0[1] or "").strip(),
        "nome": (r0[2] or "").strip(),
        "email": (r0[3] or "").strip(),
        "situacao": "ativo" if r0[4] == 1 else "inativo",
        "sistemas": {},
    }
    for r in rows:
        sis_id = r[7]
        if sis_id is None:
            continue
        if sis_id not in resultado["sistemas"]:
            resultado["sistemas"][sis_id] = {
                "sis_id": sis_id,
                "nome": (r[8] or "").strip(),
                "grupos": [],
            }
        resultado["sistemas"][sis_id]["grupos"].append(
            {"gru_id": str(r[5]), "nome": (r[6] or "").strip()}
        )
    return resultado


def buscar_complemento_se1426(rf: str) -> dict | None:
    """Busca dados complementares de um servidor no SE1426.

    Args:
        rf: Registro Funcional do servidor.

    Returns:
        Dict com ``email``, ``cpf``, ``nome`` ou None.
    """
    if not settings.SE1426_DB_SERVIDOR or not rf:
        return None

    import pyodbc

    consulta = """
        SELECT
            s.cd_registro_funcional  AS rf,
            s.nm_pessoa              AS nome,
            s.cd_cpf_pessoa          AS cpf,
            e.dc_dispositivo         AS email
        FROM v_servidor_sme_serap s
        LEFT JOIN v_servidor_cotic sc
            ON sc.cd_registro_funcional
               = s.cd_registro_funcional
        LEFT JOIN v_servidor_email_cotic e
            ON e.cd_servidor = sc.cd_servidor
            AND e.dt_fim IS NULL
        WHERE s.cd_registro_funcional = ?
    """
    try:
        conn = pyodbc.connect(
            _string_conexao_se1426(),
            timeout=settings.SE1426_DB_TIMEOUT,
        )
        try:
            cursor = conn.cursor()
            cursor.execute(consulta, (rf.strip(),))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "rf": (row[0] or "").strip(),
                "nome": (row[1] or "").strip(),
                "cpf": str(row[2] or "").strip(),
                "email": (row[3] or "").strip(),
            }
        finally:
            conn.close()
    except Exception as exc:
        logger.warning(
            "SE1426: falha ao buscar RF '%s': %s",
            rf,
            exc,
        )
        return None


def _slugificar_role(nome: str) -> str:
    import re
    import unicodedata

    s = (
        unicodedata.normalize("NFKD", nome or "")
        .encode("ascii", "ignore")
        .decode()
    )
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_")
    return s or "perfil_sem_nome"
