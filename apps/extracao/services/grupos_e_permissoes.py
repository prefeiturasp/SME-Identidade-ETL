"""Extração de grupos, perfis, associações de usuários e permissões."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from django.conf import settings

from apps.extracao.sql import grupos_e_permissoes_queries as queries
from config import conexoes_db

logger = logging.getLogger("etl_identidade")


def buscar_grupos_coresso_por_login(login: str) -> list[dict]:
    """Busca os grupos CoreSSO de um usuário pelo login.

    Args:
        login: RF ou CPF do usuário.

    Returns:
        Lista de dicionários com ``gru_id`` e ``nome``.
    """
    if not login or not settings.CORESSO_DB_SERVIDOR:
        return []

    consulta = queries.GRUPOS_USUARIO_CORESSO
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
            queries.FILTRO_EXCLUIR_SISTEMAS_USUARIO_CORESSO.format(
                ids=",".join(str(i) for i in excluidos)
            )
        )
    if sis_id is not None:
        filtros_extra.append(
            queries.FILTRO_SISTEMA_USUARIO_CORESSO.format(sis_id=int(sis_id))
        )
    if gru_id is not None:
        filtros_extra.append(
            queries.FILTRO_GRUPO_USUARIO_CORESSO.format(gru_id=gru_id)
        )
    clausulas = "\n          ".join(filtros_extra)
    consulta = queries.VINCULOS_USUARIO_GRUPO_CORESSO.format(
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
    consulta = queries.PERMISSOES_USUARIO_CORESSO.format(marcadores=marcadores)
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

    from apps.staging.models import PerfilCoressoStaging, SistemaStaging

    excluidos = settings.CORESSO_EXCLUDE_SISTEMA_IDS
    filtro_exclusao = (
        queries.FILTRO_EXCLUIR_SISTEMAS_CORESSO.format(
            ids=",".join(str(i) for i in excluidos)
        )
        if excluidos
        else ""
    )
    consulta = queries.PERFIS_CORESSO.format(filtro_exclusao=filtro_exclusao)
    conn = conexoes_db.conectar_coresso()
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
