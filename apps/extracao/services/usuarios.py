"""Extração de usuários e vínculos do SE1426 e CoreSSO."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime

from django.conf import settings

from apps.extracao.dtos import RegistroIdentidade, VinculoServidor
from apps.extracao.services.base import (
    BaseETLService,
    _paginar_api,
    _sanitizar_cpf,
    _sanitizar_email,
)
from apps.extracao.sql import usuarios_queries
from config import conexoes_db

logger = logging.getLogger("etl_identidade")


class Se1426Service(BaseETLService):
    """Extrai servidores e vínculos do SE1426 por SQL ou API."""

    fonte = "se1426"

    def _extrair_registros(
        self, desde: datetime | None, apenas_ativos: bool
    ) -> Iterator[RegistroIdentidade]:
        if settings.SE1426_DB_SERVIDOR:
            filtro = (
                usuarios_queries.FILTRO_SERVIDORES_ATIVOS_SE1426
                if apenas_ativos
                else ""
            )
            yield from extrair_servidor_serap_se1426(filtro)
        else:
            yield from _extrair_se1426_api(desde=desde)


def extrair_vinculos_se1426(
    data_referencia: str | None = None,
    apenas_ativos: bool = False,
) -> Iterator[RegistroIdentidade]:
    """Lê servidores sem confirmar avanço do watermark."""
    yield from Se1426Service().extrair(data_referencia, apenas_ativos)


def _extrair_vinculos_servidor_se1426() -> dict[str, list[VinculoServidor]]:
    """Extrai os vínculos funcionais vigentes de todos os servidores.

    Roda uma consulta por tipo de vínculo (cargo base, cargo sobreposto
    e função/atividade) trazendo todos os servidores de uma vez, em vez
    de uma consulta por RF — evita repetir a consulta para cada
    servidor durante a extração de identidade.

    Returns:
        Vínculos agrupados por RF, para anexar a cada
        ``RegistroIdentidade`` correspondente durante a extração de
        identidade.
    """
    vinculos_por_rf: dict[str, list[VinculoServidor]] = {}
    conn = conexoes_db.conectar_se1426()
    try:
        cursor = conn.cursor()
        for (
            tipo_vinculo,
            consulta,
        ) in usuarios_queries.VINCULOS_SERVIDOR_SE1426.items():
            cursor.execute(consulta)
            colunas = [d[0] for d in cursor.description]
            while True:
                linhas = cursor.fetchmany(settings.ETL_CHUNK_SIZE)
                if not linhas:
                    break
                for linha in linhas:
                    item = dict(zip(colunas, linha, strict=False))
                    rf = item.get("rf")
                    if not rf:
                        continue
                    vinculos_por_rf.setdefault(str(rf), []).append(
                        VinculoServidor(
                            tipo_vinculo=tipo_vinculo,
                            codigo_vinculo_origem=str(
                                item["codigo_vinculo_origem"]
                            ),
                            cargo_codigo=item.get("cargo_codigo"),
                            cargo_nome=item.get("cargo_nome"),
                            unidade_codigo=item.get("unidade_codigo"),
                            unidade_nome=item.get("unidade_nome"),
                            dre_codigo=item.get("dre_codigo"),
                            situacao=(
                                (item.get("situacao") or "").lower() or None
                            ),
                            vigente=True,
                        )
                    )
    finally:
        conn.close()
    return vinculos_por_rf


def extrair_servidor_serap_se1426(
    filtro_where_apenas_ativos: str,
) -> Iterator[RegistroIdentidade]:
    """Extrai servidores do SE1426 via SQL Server (read-only).

    A view ``v_servidor_sme_serap`` não expõe coluna de data de
    atualização — sem suporte a filtro incremental por enquanto;
    a extração sempre traz a base completa.

    Args:
        filtro_where_apenas_ativos: Cláusula ``WHERE`` para filtrar por
          apenas ativos (ou string vazia).

    Yields:
        RegistroIdentidade para cada servidor encontrado, com os
        vínculos funcionais vigentes (cargo base, sobreposto e
        função/atividade) já anexados em ``vinculos``.
    """
    # v_servidor_sme_serap traz 2 linhas para ~16% dos RFs (Ativo +
    # Inativo, mesmo cadastro repetido só com a situação diferente —
    # confirmado por inspeção direta contra o SE1426; não é efeito do
    # LEFT JOIN abaixo). ROW_NUMBER() prioriza Ativo, para extrair uma
    # única linha por RF — sem isso, cada RF duplicado geraria dois
    # RegistroIdentidade recebendo a mesma lista de vínculos, dobrando
    # staging/payload sem necessidade.
    consulta = usuarios_queries.SERVIDORES_SERAP_SE1426.format(
        filtro_where_apenas_ativos=filtro_where_apenas_ativos
    )

    vinculos_por_rf = _extrair_vinculos_servidor_se1426()

    conn = conexoes_db.conectar_se1426()
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
                rf = item.get("rf")
                yield RegistroIdentidade(
                    fonte="se1426",
                    tipo="servidor",
                    rf=rf,
                    nome=item.get("nome"),
                    cpf=_sanitizar_cpf(item.get("cpf")),
                    email=_sanitizar_email(item.get("email")),
                    situacao=((item.get("situacao") or "").lower() or None),
                    vinculos=(vinculos_por_rf.get(str(rf), []) if rf else []),
                    dados_extras={
                        k: str(v) if v is not None else None
                        for k, v in item.items()
                    },
                )
    finally:
        conn.close()


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
            cpf=_sanitizar_cpf(item.get("cpf") or item.get("cd_cpf_pessoa")),
            email=item.get("email"),
            situacao=(item.get("situacao") or "").lower() or None,
            dados_extras=item,
        )


def buscar_complemento_se1426(rf: str) -> dict | None:
    """Busca dados complementares de um servidor no SE1426.

    Args:
        rf: Registro Funcional do servidor.

    Returns:
        Dict com ``email``, ``cpf``, ``nome`` ou None.
    """
    if not settings.SE1426_DB_SERVIDOR or not rf:
        return None

    consulta = usuarios_queries.COMPLEMENTO_SERVIDOR_SE1426
    try:
        conn = conexoes_db.conectar_se1426()
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
                "email": _sanitizar_email(row[3]) or "",
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


class CoreSSOService(BaseETLService):
    """Extrai identidades do CoreSSO por SQL ou API."""

    fonte = "coresso"

    def _extrair_registros(
        self, desde: datetime | None, apenas_ativos: bool
    ) -> Iterator[RegistroIdentidade]:
        if settings.CORESSO_DB_SERVIDOR:
            yield from _extrair_coresso_sql(desde, apenas_ativos)
        elif settings.CORESSO_API_URL:
            yield from _extrair_coresso_api(desde=desde)
        else:
            logger.warning("CoreSSO: sem configuração de banco ou API.")


def extrair_coresso(
    data_referencia: str | None = None,
    apenas_ativos: bool = False,
) -> Iterator[RegistroIdentidade]:
    """Lê usuários sem confirmar avanço do watermark."""
    yield from CoreSSOService().extrair(data_referencia, apenas_ativos)


_TDO_ID_CPF = "2CEEED03-63EB-E011-9B36-00155D033206"


_TAMANHO_LOTE_IDENTIFICADORES = 200


def _registro_coresso_de_linha(item: dict) -> RegistroIdentidade:
    """Monta um RegistroIdentidade a partir de uma linha da consulta SQL.

    Compartilhado entre a extração incremental (`_extrair_coresso_sql`)
    e a extração pontual por identificadores (usada para reprocessar
    clientes com `token_ms_pendente=True` cujo staging já foi limpo).
    """
    return RegistroIdentidade(
        fonte="coresso",
        tipo="terceiro",
        cpf=_sanitizar_cpf(item.get("cpf")),
        nome=item.get("nome"),
        email=_sanitizar_email(item.get("email")),
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


def _consultar_coresso_sql(filtro_where: str) -> Iterator[dict]:
    """Executa a consulta base do CoreSSO com um filtro WHERE customizado.

    Compartilha a mesma CTE com ``ROW_NUMBER`` (proteção contra CPF
    duplicado em ``PES_PessoaDocumento``) entre a extração incremental
    e a extração pontual por identificadores.

    Args:
        filtro_where: Cláusula ``WHERE`` completa (ou string vazia).

    Yields:
        Dict por linha, com as colunas ``login``, ``cpf``, ``nome``,
        ``email``, ``situacao``, ``dt_atualizacao``.
    """
    consulta = usuarios_queries.USUARIOS_CORESSO.format(
        _TDO_ID_CPF=_TDO_ID_CPF, filtro_where=filtro_where
    )

    conn = conexoes_db.conectar_coresso()
    conn.timeout = settings.CORESSO_DB_TIMEOUT
    try:
        cursor = conn.cursor()
        cursor.execute(consulta)
        colunas = [d[0] for d in cursor.description]
        while True:
            linhas = cursor.fetchmany(settings.ETL_CHUNK_SIZE)
            if not linhas:
                break
            for linha in linhas:
                yield dict(zip(colunas, linha, strict=False))
    finally:
        conn.close()


def _extrair_coresso_sql(
    desde: datetime | None = None, apenas_ativos: bool = False
) -> Iterator[RegistroIdentidade]:
    """Extrai usuários legados do CoreSSO via SQL Server (read-only).

    Args:
        desde: Data mínima de alteração para extração incremental
            (filtra por ``usu_dataAlteracao``); None extrai tudo.
        apenas_ativos: Se True, filtra apenas registros ativos.
            (filtra por ``usu_situacao = 1``)

    Yields:
        RegistroIdentidade para cada usuário encontrado.
    """
    filtro_where = ""

    if desde:
        dt = desde.strftime("%Y-%m-%d")
        filtro_where += (
            usuarios_queries.FILTRO_USUARIOS_ALTERADOS_CORESSO.format(data=dt)
        )

    if apenas_ativos:
        filtro_where += usuarios_queries.FILTRO_USUARIOS_ATIVOS_CORESSO

    for item in _consultar_coresso_sql(filtro_where):
        yield _registro_coresso_de_linha(item)


def _extrair_coresso_por_identificadores(
    identificadores: list[str],
) -> Iterator[RegistroIdentidade]:
    """Reextrai pontualmente usuários do CoreSSO por CPF ou login/RF.

    Usada quando um cliente está pendente de reprocessamento
    (``ControleProvisionamento.token_ms_pendente=True`` ou hash
    desatualizado) mas seu staging já foi removido pela limpeza
    periódica (``task_identidade_limpar_staging``) — busca em LOTE
    (não um SELECT por pessoa) para não sobrecarregar o CoreSSO nem
    o pipeline com uma consulta por cliente.

    Args:
        identificadores: Lista de CPFs (só dígitos) ou logins/RF.

    Yields:
        RegistroIdentidade para cada usuário encontrado — clientes sem
        correspondência no CoreSSO simplesmente não aparecem no
        resultado (não é erro; o chamador decide o que fazer com os
        que sobraram sem retorno).
    """
    identificadores_validos = [i for i in identificadores if i]
    for inicio in range(
        0, len(identificadores_validos), _TAMANHO_LOTE_IDENTIFICADORES
    ):
        sublote = identificadores_validos[
            inicio : inicio + _TAMANHO_LOTE_IDENTIFICADORES
        ]
        # Identificadores vêm de ControleProvisionamento.id_origem (dado
        # interno, nunca input de usuário) — ainda assim, escapa aspas
        # simples para evitar quebrar a query caso algum valor legado
        # contenha caracteres inesperados.
        valores = ", ".join(
            "'" + str(ident).replace("'", "''") + "'" for ident in sublote
        )
        filtro = usuarios_queries.FILTRO_IDENTIFICADORES_CORESSO.format(
            valores=valores
        )
        for item in _consultar_coresso_sql(filtro):
            yield _registro_coresso_de_linha(item)


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
            cpf=_sanitizar_cpf(item.get("cpf")),
            nome=item.get("nome") or item.get("pes_nome"),
            email=_sanitizar_email(item.get("email") or item.get("pes_email")),
            situacao=(
                "ativo"
                if str(item.get("situacao", "")).strip() in ("1", "ativo")
                else "inativo"
            ),
            tipo_acesso="legado-coresso",
            dados_extras=item,
        )


def buscar_grupos_coresso_por_login(login: str) -> list[dict]:
    """Busca os grupos CoreSSO de um usuário pelo login.

    Args:
        login: RF ou CPF do usuário.

    Returns:
        Lista de dicionários com ``gru_id`` e ``nome``.
    """
    if not login or not settings.CORESSO_DB_SERVIDOR:
        return []

    consulta = usuarios_queries.GRUPOS_USUARIO_CORESSO
    try:
        conn = conexoes_db.conectar_coresso()
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

    filtros_extra: list[str] = []
    excluidos = settings.CORESSO_EXCLUDE_SISTEMA_IDS
    if excluidos:
        filtros_extra.append(
            usuarios_queries.FILTRO_EXCLUIR_SISTEMAS_USUARIO_CORESSO.format(
                ids=",".join(str(i) for i in excluidos)
            )
        )
    if sis_id is not None:
        filtros_extra.append(
            usuarios_queries.FILTRO_SISTEMA_USUARIO_CORESSO.format(
                sis_id=int(sis_id)
            )
        )
    if gru_id is not None:
        filtros_extra.append(
            usuarios_queries.FILTRO_GRUPO_USUARIO_CORESSO.format(gru_id=gru_id)
        )
    clausulas = "\n          ".join(filtros_extra)
    consulta = usuarios_queries.VINCULOS_USUARIO_GRUPO_CORESSO.format(
        clausulas=clausulas
    )
    lote_maximo = settings.ETL_LOTE_MAXIMO
    conn = conexoes_db.conectar_coresso()
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

    ident = identificador.strip()
    if "@" in ident:
        filtro = usuarios_queries.FILTRO_EMAIL_CORESSO
    elif len(ident) > 7:
        filtro = usuarios_queries.FILTRO_CPF_CORESSO
    else:
        filtro = usuarios_queries.FILTRO_LOGIN_CORESSO

    consulta = usuarios_queries.DADOS_USUARIO_CORESSO.format(filtro=filtro)
    conn = conexoes_db.conectar_coresso()
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


def buscar_permissoes_usuario_coresso(gru_ids: list[str]) -> list[dict]:
    """Busca as permissões CRUD por módulo para os grupos informados.

    Fonte real: ``SYS_GrupoPermissao``/``SYS_Modulo`` do CoreSSO —
    uma matriz grupo×sistema×módulo com quatro flags booleanas
    (consultar/inserir/alterar/excluir). Chamada em cadeia, com os
    ``gru_id`` já obtidos de ``buscar_dados_usuario_coresso``, e não
    numa única query com ``JOIN`` direto, para evitar a explosão
    cartesiana de repetir os dados de perfil por módulo — um usuário
    com poucos grupos pode ter centenas de módulos permissionados.
    Só retorna módulos com ao menos uma permissão concedida (descarta
    a maioria das combinações grupo×módulo, que vêm todas com as
    quatro flags falsas).

    Args:
        gru_ids: IDs dos grupos CoreSSO do usuário (``gru_id``).

    Returns:
        Lista de dicts com ``sis_id``, ``sis_nome``, ``mod_id``,
        ``mod_nome`` e as quatro flags CRUD.
    """
    if not settings.CORESSO_DB_SERVIDOR or not gru_ids:
        return []

    marcadores = ",".join("?" for _ in gru_ids)
    consulta = usuarios_queries.PERMISSOES_USUARIO_CORESSO.format(
        marcadores=marcadores
    )
    conn = conexoes_db.conectar_coresso()
    try:
        cursor = conn.cursor()
        cursor.execute(consulta, tuple(gru_ids))
        colunas = [d[0] for d in cursor.description]
        return [
            dict(zip(colunas, row, strict=False)) for row in cursor.fetchall()
        ]
    finally:
        conn.close()


def _montar_resultado_usuario(rows: list) -> dict:
    """Monta o dict de resultado a partir das linhas SQL."""
    r0 = rows[0]
    resultado: dict = {
        "login": (r0[0] or "").strip(),
        "cpf": (r0[1] or "").strip(),
        "nome": (r0[2] or "").strip(),
        "email": _sanitizar_email(r0[3]) or "",
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
