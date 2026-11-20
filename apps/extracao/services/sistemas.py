"""Extração de sistemas e configurações de recuperação de senha."""

from __future__ import annotations

import logging

from django.conf import settings

from apps.extracao.sql import sistemas_queries
from config import conexoes_db

logger = logging.getLogger("etl_identidade")


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

    from apps.staging.models import SistemaStaging

    excluidos = settings.CORESSO_EXCLUDE_SISTEMA_IDS
    filtro_exclusao = (
        sistemas_queries.FILTRO_EXCLUIR_SISTEMAS_CORESSO.format(
            ids=",".join(str(i) for i in excluidos)
        )
        if excluidos
        else ""
    )
    consulta = sistemas_queries.SISTEMAS_CORESSO.format(
        filtro_exclusao=filtro_exclusao
    )
    conn = conexoes_db.conectar_coresso()
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


def extrair_sistemas_recuperacao() -> list[dict]:
    """Lê apenas os metadados de recuperação, sem copiar solicitações."""
    conexao = conexoes_db.conectar_eol_db_apoio()
    try:
        conexao.set_session(readonly=True)
        with conexao.cursor() as cursor:
            cursor.execute(
                sistemas_queries.CONFIGURACOES_RECUPERACAO_SENHA_EOL_APOIO
            )
            return [
                {
                    "codigo_sistema": codigo,
                    "nome_sistema": nome,
                    "pagina_recuperacao_senha": pagina,
                }
                for codigo, nome, pagina in cursor.fetchall()
            ]
    finally:
        conexao.close()
