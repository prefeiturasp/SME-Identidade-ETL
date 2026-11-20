"""Configurações e abertura de conexões com os bancos do ETL."""

import os
import re
from typing import Any
from urllib.parse import urlparse

from django.conf import settings


def _url_para_bd(url: str | None) -> dict:
    """Converta URL PostgreSQL em dicionário de configuração Django."""
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    parsed = urlparse(str(url))
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "postgres",
        "PASSWORD": parsed.password or "postgres",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": 600,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": 10},
    }


# SYNC_REC_DB — banco de controle operacional do ETL.
DATABASES: dict = {
    "default": _url_para_bd(os.getenv("SYNC_REC_DB_URL")),
}

# ---------------------------------------------------------------------------
# SE1426 (PRODAM) — SQL Server
# ---------------------------------------------------------------------------
SE1426_DB_SERVIDOR: str = os.getenv("SE1426_DB_SERVIDOR", "")
SE1426_DB_NOME: str = os.getenv("SE1426_DB_NOME", "se1426")
SE1426_DB_USUARIO: str = os.getenv("SE1426_DB_USUARIO", "")
SE1426_DB_SENHA: str = os.getenv("SE1426_DB_SENHA", "")
SE1426_DB_TIMEOUT: int = int(os.getenv("SE1426_DB_TIMEOUT", "300"))

# ---------------------------------------------------------------------------
# CoreSSO — SQL Server
# ---------------------------------------------------------------------------
CORESSO_DB_SERVIDOR: str = os.getenv("CORESSO_DB_SERVIDOR", "")
CORESSO_DB_NOME: str = os.getenv("CORESSO_DB_NOME", "CoreSSO")
CORESSO_DB_USUARIO: str = os.getenv("CORESSO_DB_USUARIO", "")
CORESSO_DB_SENHA: str = os.getenv("CORESSO_DB_SENHA", "")
CORESSO_DB_TIMEOUT: int = int(os.getenv("CORESSO_DB_TIMEOUT", "300"))

# ---------------------------------------------------------------------------
# EOL DB — SQL Server
# ---------------------------------------------------------------------------
EOL_DB_STRING_CONEXAO: str = os.getenv("EOL_DB_STRING_CONEXAO", "")

# ---------------------------------------------------------------------------
# EOL DB APOIO — PostgreSQL
# ---------------------------------------------------------------------------
API_EOL_CONNECTION: str = os.getenv(
    "ApiEolConnection", os.getenv("API_EOL_DB_URL", "")  # noqa: SIM112
)


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
# Conexão PostgreSQL do EOL DB APOIO
# ---------------------------------------------------------------------------


def _parametros_conexao_api_eol(conexao: str) -> dict[str, str]:
    """Interpreta URI PostgreSQL ou configuração Npgsql do legado.

    Args:
        conexao: Valor de ApiEolConnection ou API_EOL_DB_URL.

    Returns:
        Parâmetros PostgreSQL, preservando valores delimitados por aspas.

    Raises:
        ValueError: Quando host ou banco não foram informados.
    """
    if conexao.startswith(("postgres://", "postgresql://")):
        return {"dsn": conexao}
    aliases = {
        "host": "host",
        "server": "host",
        "database": "dbname",
        "username": "user",
        "user id": "user",
        "password": "password",
        "port": "port",
        "timeout": "connect_timeout",
        "ssl mode": "sslmode",
    }
    encontrados = re.finditer(
        r"""(?:^|;)\s*([^=;]+)=\s*(?:"((?:""|[^"])*)"|'((?:''|[^'])*)'|([^;]*))""",
        conexao,
    )
    resultado = {}
    for campo in encontrados:
        chave = aliases.get(campo[1].strip().lower())
        if chave:
            valor = next(v for v in campo.groups()[1:] if v is not None)
            resultado[chave] = (
                valor.strip().replace('""', '"').replace("''", "'")
            )
    if not resultado.get("host") or not resultado.get("dbname"):
        raise ValueError(
            "Configure ApiEolConnection ou API_EOL_DB_URL "
            "para o EOL DB APOIO (PostgreSQL)."
        )
    if "sslmode" in resultado:
        resultado["sslmode"] = (
            resultado["sslmode"]
            .lower()
            .replace("verifyfull", "verify-full")
            .replace("verifyca", "verify-ca")
        )
    return resultado


def conectar_se1426() -> Any:
    """Abre uma conexão com o SQL Server do SE1426."""
    import pyodbc

    return pyodbc.connect(
        _string_conexao_se1426(), timeout=settings.SE1426_DB_TIMEOUT
    )


def conectar_coresso() -> Any:
    """Abre uma conexão com o SQL Server do CoreSSO."""
    import pyodbc

    return pyodbc.connect(
        _string_conexao_coresso(), timeout=settings.CORESSO_DB_TIMEOUT
    )


def conectar_eol_db() -> Any:
    """Abre uma conexão com o SQL Server do EOL DB."""
    import pyodbc

    return pyodbc.connect(
        _string_conexao_eol_db(), timeout=settings.SE1426_DB_TIMEOUT
    )


def conectar_eol_db_apoio() -> Any:
    """Abre uma conexão com o PostgreSQL do EOL DB APOIO."""
    import psycopg2

    parametros = _parametros_conexao_api_eol(settings.API_EOL_CONNECTION)
    parametros.setdefault("connect_timeout", "10")
    return psycopg2.connect(**parametros)
