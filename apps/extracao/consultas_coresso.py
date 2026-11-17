
"""Consultas ao CoreSSO para integrações do identidade."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import closing, contextmanager

import pyodbc
from django.conf import settings

from apps.extracao.tasks import _string_conexao_coresso

_SQL_DADOS_USUARIO = """
    SELECT
        pessoa.pes_nome AS Nome,
        documento.psd_numero AS CPF,
        usr.usu_email AS Email,
        usr.usu_login AS CodigoRf
    FROM sys_usuario usr
    INNER JOIN PES_Pessoa pessoa
        ON usr.pes_id = pessoa.pes_id
    INNER JOIN PES_PessoaDocumento documento
        ON pessoa.pes_id = documento.pes_id
    WHERE usr.usu_login = ?
      AND usr.usu_situacao <> 3
"""


class ConsultaCoreSSOError(Exception):
    """Representa uma falha em consultas ao CoreSSO."""


@contextmanager
def _abrir_cursor() -> Generator[pyodbc.Cursor, None, None]:
    """Abre um cursor para consulta ao CoreSSO.

    Yields:
        Cursor conectado ao CoreSSO.

    Raises:
        ConsultaCoreSSOError: Se a conexão ou operação SQL falhar.
    """
    if not settings.CORESSO_DB_SERVIDOR:
        raise ConsultaCoreSSOError(
            "CoreSSO não configurado."
        )

    try:
        with closing(
            pyodbc.connect(
                _string_conexao_coresso(),
                timeout=settings.CORESSO_DB_TIMEOUT,
            )
        ) as conexao:
            conexao.timeout = settings.CORESSO_DB_TIMEOUT

            with closing(conexao.cursor()) as cursor:
                yield cursor

    except pyodbc.Error as exc:
        raise ConsultaCoreSSOError(
            "Falha ao consultar o CoreSSO."
        ) from exc

def _consultar(
    sql: str,
    parametros: tuple[object, ...],
) -> dict[str, object] | None:
    """Executa uma consulta SQL e obtém o primeiro registro.

    Args:
        sql: Instrução SQL parametrizada.
        parametros: Valores dos parâmetros da consulta.

    Returns:
        Registro encontrado ou None.

    Raises:
        ConsultaCoreSSOError: Se a consulta falhar.
    """
    with _abrir_cursor() as cursor:
        cursor.execute(sql, parametros)
        registro = cursor.fetchone()

        if registro is None:
            return None

        colunas = [
            coluna[0]
            for coluna in cursor.description
        ]

        return dict(zip(colunas, registro, strict=True))


def _texto_ou_nulo(valor: object) -> str | None:
    """Formata um valor em texto.

    Args:
        valor: Valor a ser convertido.

    Returns:
        Texto correspondente ou None.
    """
    return None if valor is None else str(valor)


def selecionar_dados_usuario_por_login(
    login: str,
) -> dict[str, str | None] | None:
    """Consulta os dados do usuário no CoreSSO pelo login.

    Args:
        login: Login do usuário.

    Returns:
        Dados do usuário ou None quando não encontrado.

    Raises:
        ConsultaCoreSSOError: Se a consulta falhar.
    """
    registro = _consultar(
        _SQL_DADOS_USUARIO,
        (login,),
    )

    if registro is None:
        return None

    return {
        "nome": _texto_ou_nulo(registro["Nome"]),
        "cpf": _texto_ou_nulo(registro["CPF"]),
        "email": _texto_ou_nulo(registro["Email"]),
        "codigo_rf": _texto_ou_nulo(registro["CodigoRf"]),
    }
