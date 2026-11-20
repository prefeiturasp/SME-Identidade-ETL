"""Extração da fonte eol."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime

from django.conf import settings

from apps.extracao.dtos import RegistroIdentidade
from apps.extracao.services.base import (
    BaseETLService,
    _sanitizar_cpf,
)
from config import conexoes_db

logger = logging.getLogger("etl_identidade")


class AlunosService(BaseETLService):
    """Extrai alunos do EOL DB por SQL."""

    fonte = "eol_alunos"

    def _extrair_registros(
        self, desde: datetime | None, apenas_ativos: bool
    ) -> Iterator[RegistroIdentidade]:
        if (
            not settings.EOL_DB_STRING_CONEXAO
            and not settings.SE1426_DB_SERVIDOR
        ):
            logger.warning("EOL_DB: sem configuração de conexão.")
            return
        filtro = (
            "WHERE mat.st_matricula IN (1, 5, 6, 10, 13)"
            if apenas_ativos
            else ""
        )
        yield from _extrair_eol_alunos_sql(filtro_where=filtro)


def extrair_eol_alunos(
    data_referencia: str | None = None,
    apenas_ativos: bool = False,
) -> Iterator[RegistroIdentidade]:
    """Lê alunos sem confirmar avanço do watermark."""
    yield from AlunosService().extrair(data_referencia, apenas_ativos)


def _extrair_eol_alunos_sql(filtro_where: str) -> Iterator[RegistroIdentidade]:
    """Extrai alunos do EOL_DB via SQL Server (read-only).

    As tabelas aluno/v_matricula_cotic/matricula_turma_escola não
    expõem uma coluna de data de atualização utilizável para filtro
    incremental — sem suporte a watermark por enquanto; a extração
    sempre traz a base completa.

    Args:
        filtro_where: Cláusula ``WHERE`` completa (ou string vazia).

    Yields:
        RegistroIdentidade para cada aluno encontrado.
    """
    consulta = f"""
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
        {filtro_where}
    """

    conn = conexoes_db.conectar_eol_db()
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
                    cpf=_sanitizar_cpf(item.get("cpf")),
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
