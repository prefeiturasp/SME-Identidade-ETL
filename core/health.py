"""Views de health check para validar conectividade com as fontes externas do ETL."""
import logging
import time

import httpx
from django.conf import settings
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import (
    CoreSSOHealthSerializer,
    ETLExternalHealthResponseSerializer,
    SMEIntegracaoHealthSerializer,
)

logger = logging.getLogger(__name__)


def _check_coresso_db() -> dict:
    server_value = settings.CORESSO_DB_SERVER or ""
    server_host = server_value.split("\\")[0].strip()

    result = {
        "source": "CoreSSO (SQL Server)",
        "server": server_value,
        "server_host": server_host,
        "database": settings.CORESSO_DB_NAME,
        "status": "unknown",
    }

    if not server_host:
        result["status"] = "not_configured"
        result["detail"] = "CORESSO_DB_SERVER não configurado"
        return result

    try:
        import pyodbc

        conn_str = (
            f"DRIVER={{FreeTDS}};"
            f"SERVER={server_host};"
            f"DATABASE={settings.CORESSO_DB_NAME};"
            f"UID={settings.CORESSO_DB_USER};"
            f"PWD={settings.CORESSO_DB_PASSWORD};"
            f"TDS_Version=7.4;"
            f"Port=1433;"
            f"Connection Timeout={settings.CORESSO_DB_TIMEOUT};"
        )

        t0 = time.monotonic()
        conn = pyodbc.connect(conn_str, timeout=settings.CORESSO_DB_TIMEOUT)
        cursor = conn.cursor()

        cursor.execute("SELECT @@VERSION AS version, DB_NAME() AS db_name, SUSER_NAME() AS login_user")
        row = cursor.fetchone()
        elapsed_ms = round((time.monotonic() - t0) * 1000)

        cursor.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'"
        )
        table_count = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        result["status"] = "healthy"
        result["response_time_ms"] = elapsed_ms
        result["sql_server_version"] = row.version.split("\n")[0].strip() if row.version else None
        result["connected_db"] = row.db_name
        result["connected_user"] = row.login_user
        result["table_count"] = table_count

    except ImportError:
        result["status"] = "error"
        result["detail"] = "pyodbc não instalado"
    except Exception as e:
        result["status"] = "unhealthy"
        result["detail"] = str(e)
        logger.warning("CoreSSO health check failed: %s", e)

    return result

def _build_base_result() -> dict:
    return {
        "source": "SME Integração API",
        "base_url": settings.SME_INTEGRACAO_BASE_URL,
        "status": "unknown",
    }

def _authenticate(client: httpx.Client, result: dict) -> tuple:
    if not (
        settings.SME_INTEGRACAO_LOGIN
        and settings.SME_INTEGRACAO_PASSWORD
    ):
        return None, False, None

    try:
        response = client.post(
            "/api/v1/autenticacao",
            json={
                "login": settings.SME_INTEGRACAO_LOGIN,
                "senha": settings.SME_INTEGRACAO_PASSWORD,
            },
        )

        auth_ok = response.status_code == 200

        if not auth_ok:
            return False, False, None

        token = response.json().get("token", "")

        return (
            True,
            bool(token),
            len(token) if token else 0,
        )

    except Exception as exc:
        result["auth_error"] = str(exc)
        return False, False, None

def _check_data_access(client: httpx.Client):
    if not settings.SME_INTEGRACAO_API_KEY:
        return None

    try:
        response = client.get(
            "/api/cargos",
            headers={
                "x-api-eol-key": settings.SME_INTEGRACAO_API_KEY
            },
        )
        return response.status_code == 200

    except Exception:
        return False

def _authentication_status(auth_ok):
    if auth_ok is True:
        return "ok"

    if auth_ok is False:
        return "failed"

    return "not_tested"

def _data_access_status(data_ok):
    if data_ok is True:
        return "ok"

    if data_ok is False:
        return "failed"

    return "not_tested"

def _check_sme_integracao() -> dict:
    result = _build_base_result()

    if not settings.SME_INTEGRACAO_BASE_URL:
        result["status"] = "not_configured"
        result["detail"] = (
            "SME_INTEGRACAO_BASE_URL não configurado"
        )
        return result

    try:
        t0 = time.monotonic()

        with httpx.Client(
            base_url=settings.SME_INTEGRACAO_BASE_URL,
            timeout=settings.SME_INTEGRACAO_TIMEOUT,
            verify=True,
        ) as client:

            swagger_response = client.get(
                "/swagger/v1/swagger.json"
            )

            swagger_ok = (
                swagger_response.status_code == 200
            )

            (
                auth_ok,
                auth_token_present,
                auth_token_length,
            ) = _authenticate(client, result)

            data_ok = _check_data_access(client)

        elapsed_ms = round(
            (time.monotonic() - t0) * 1000
        )

        result.update({
            "response_time_ms": elapsed_ms,
            "swagger_available": swagger_ok,
            "authentication": _authentication_status(auth_ok),
            "data_access": _data_access_status(data_ok),
            "status": (
                "healthy"
                if swagger_ok
                else "unhealthy"
            ),
        })

        if auth_ok is True:
            result["auth_token_present"] = auth_token_present
            result["auth_token_length"] = auth_token_length

    except httpx.ConnectError as exc:
        result["status"] = "unhealthy"
        result["detail"] = f"Conexão recusada: {exc}"

    except Exception as exc:
        result["status"] = "unhealthy"
        result["detail"] = str(exc)

        logger.warning(
            "SME Integração health check failed: %s",
            exc,
        )

    return result

def _check_sme_integracao_auth_only() -> dict:
    result = {
        "source": "SME Integração API",
        "base_url": settings.SME_INTEGRACAO_BASE_URL,
        "status": "unknown",
        "authentication": "not_tested",
    }

    if not settings.SME_INTEGRACAO_BASE_URL:
        result["status"] = "not_configured"
        result["detail"] = "SME_INTEGRACAO_BASE_URL não configurado"
        return result

    if not settings.SME_INTEGRACAO_LOGIN or not settings.SME_INTEGRACAO_PASSWORD:
        result["status"] = "not_configured"
        result["detail"] = "SME_INTEGRACAO_LOGIN/SME_INTEGRACAO_PASSWORD não configurados"
        return result

    try:
        t0 = time.monotonic()
        with httpx.Client(
            base_url=settings.SME_INTEGRACAO_BASE_URL,
            timeout=settings.SME_INTEGRACAO_TIMEOUT,
            verify=True,
        ) as client:
            resp_auth = client.post(
                "/api/v1/autenticacao",
                json={
                    "login": settings.SME_INTEGRACAO_LOGIN,
                    "senha": settings.SME_INTEGRACAO_PASSWORD,
                },
            )

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        result["response_time_ms"] = elapsed_ms
        result["http_status"] = resp_auth.status_code

        if resp_auth.status_code == 200:
            payload = resp_auth.json()
            token = payload.get("token", "")
            result["authentication"] = "ok"
            result["status"] = "healthy"
            result["auth_token_present"] = bool(token)
            result["auth_token_length"] = len(token) if token else 0
        else:
            result["authentication"] = "failed"
            result["status"] = "unhealthy"

    except Exception as e:
        result["status"] = "unhealthy"
        result["authentication"] = "failed"
        result["detail"] = str(e)

    return result


@extend_schema(
    tags=["ETL Health"],
    summary="Health check de fontes externas",
    description=(
        "Valida conectividade e disponibilidade das fontes externas do ETL"
        " (CoreSSO e SME Integração API)."
    ),
    responses={
        200: OpenApiResponse(
            response=ETLExternalHealthResponseSerializer,
            description="Todas as fontes saudáveis",
        ),
        503: OpenApiResponse(
            response=ETLExternalHealthResponseSerializer,
            description="Uma ou mais fontes indisponíveis",
        ),
    },
    examples=[
        OpenApiExample(
            "Resposta saudável",
            value={
                "status": "healthy",
                "sources": {
                    "coresso_db": {
                        "source": "CoreSSO (SQL Server)",
                        "server": "10.49.19.159\\SQLSERVERHOMOLOG",
                        "server_host": "xx.xx.xx.xxx",
                        "database": "CoreSSO",
                        "status": "healthy",
                        "response_time_ms": 180,
                        "sql_server_version": "Microsoft SQL Server 2008 R2",
                        "connected_db": "CoreSSO",
                        "connected_user": "UserGestaoAvaliacao",
                        "table_count": 83,
                    },
                    "sme_integracao_api": {
                        "source": "SME Integração API",
                        "base_url": "https://hom-smeintegracaoapi.sme.prefeitura.sp.gov.br",
                        "status": "healthy",
                        "response_time_ms": 260,
                        "swagger_available": True,
                        "authentication": "failed",
                        "data_access": "ok",
                    },
                },
            },
            response_only=True,
            status_codes=["200"],
        )
    ],
)
@api_view(["GET"])
def health_sources(request):
    """Retorna o status combinado de health de todas as fontes externas do ETL (CoreSSO e SME Integracao)."""
    coresso = _check_coresso_db()
    sme = _check_sme_integracao()

    all_healthy = all(
        s["status"] == "healthy" for s in [coresso, sme]
    )

    return Response(
        {
            "status": "healthy" if all_healthy else "degraded",
            "sources": {
                "coresso_db": coresso,
                "sme_integracao_api": sme,
            },
        },
        status=200 if all_healthy else 503,
    )


@extend_schema(
    tags=["ETL Health"],
    summary="Health check CoreSSO",
    description="Valida conectividade read-only com o banco CoreSSO (SQL Server).",
    responses={
        200: OpenApiResponse(response=CoreSSOHealthSerializer, description="CoreSSO disponível"),
        503: OpenApiResponse(response=CoreSSOHealthSerializer, description="CoreSSO indisponível"),
    },
)
@api_view(["GET"])
def health_coresso(request):
    """Health check isolado do CoreSSO (SQL Server)."""
    result = _check_coresso_db()
    status_code = 200 if result["status"] == "healthy" else 503
    return Response(result, status=status_code)


@extend_schema(
    tags=["ETL Health"],
    summary="Health check SME Integração API",
    description="Valida disponibilidade da SME Integração API (Swagger, autenticação e endpoint de dados).",
    responses={
        200: OpenApiResponse(response=SMEIntegracaoHealthSerializer, description="API disponível"),
        503: OpenApiResponse(response=SMEIntegracaoHealthSerializer, description="API indisponível"),
    },
)
@api_view(["GET"])
def health_sme_integracao(request):
    """Health check isolado da SME Integração API."""
    result = _check_sme_integracao()
    status_code = 200 if result["status"] == "healthy" else 503
    return Response(result, status=status_code)


@extend_schema(
    tags=["ETL Health"],
    summary="Health check autenticação SME Integração",
    description=(
        "Valida somente o endpoint de autenticação da SME Integração API"
        " sem expor token no payload de resposta."
    ),
    responses={
        200: OpenApiResponse(response=SMEIntegracaoHealthSerializer, description="Autenticação disponível"),
        503: OpenApiResponse(response=SMEIntegracaoHealthSerializer, description="Falha na autenticação"),
    },
)
@api_view(["GET"])
def health_sme_integracao_auth(request):
    """Health check isolado de autenticação da SME Integração API."""
    result = _check_sme_integracao_auth_only()
    status_code = 200 if result["status"] == "healthy" else 503
    return Response(result, status=status_code)
