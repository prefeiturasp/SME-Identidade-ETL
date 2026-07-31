"""Configurações Django do SME-Identidade-ETL."""

import os
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

NOME_APLICACAO = os.getenv("NOME_APLICACAO", "SME-Identidade-ETL")
AMBIENTE_APLICACAO = os.getenv("AMBIENTE_APLICACAO", "local")
NIVEL_LOG = os.getenv("NIVEL_LOG", "INFO")

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "dev-inseguro-apenas-desenvolvimento",
)
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.controle_etl",
    "apps.extracao",
    "apps.staging",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


def _url_para_bd(url: str | None) -> dict:
    """Converta URL PostgreSQL em dicionário de configuração Django."""
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    parsed = urllib.parse.urlparse(str(url))
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


DATABASES = {
    # SYNC_REC_DB — único banco do ETL: controle operacional
    # (execuções, watermark, checkpoints, retries, lineage)
    "default": _url_para_bd(os.getenv("SYNC_REC_DB_URL")),
}

SILENCED_SYSTEM_CHECKS: list[str] = []

AUTH_PASSWORD_VALIDATORS: list[dict] = []

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# REST Framework
# ---------------------------------------------------------------------------
API_KEY = os.getenv("API_KEY", "dev-key-default")
API_KEY_HEADER = os.getenv("API_KEY_HEADER", "X-API-Key")

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.controle_etl.autenticacao.AutenticacaoApiKey",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "SME-Identidade-ETL API",
    "DESCRIPTION": "API de controle do pipeline ETL de identidades SME-SP",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": API_KEY_HEADER,
            }
        }
    },
    "SECURITY": [{"ApiKeyAuth": []}],
}

# ---------------------------------------------------------------------------
# Celery / KeyDB
# ---------------------------------------------------------------------------
URL_KEYDB = os.getenv("URL_KEYDB", "redis://localhost:6379/0")
CELERY_BROKER_URL = URL_KEYDB
CELERY_RESULT_BACKEND = URL_KEYDB
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "America/Sao_Paulo"
CELERY_ENABLE_UTC = True
CELERY_TASK_ALWAYS_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "0") == "1"
# Necessário para chord/group funcionarem em modo eager (sem worker)
CELERY_TASK_STORE_EAGER_RESULT = CELERY_TASK_ALWAYS_EAGER

# Recicla o processo filho a cada N tasks — sem isso, um worker que
# processa uma extração de milhões de registros seguida de uma task
# de transformação/dedup no mesmo processo acumula fragmentação de
# heap (CPython + driver pyodbc) até estourar o limite de memória do
# pod (SIGKILL/OOM), mesmo com as tasks individuais em streaming.
CELERY_WORKER_MAX_TASKS_PER_CHILD = int(
    os.getenv("CELERY_WORKER_MAX_TASKS_PER_CHILD", "20")
)

CELERY_TASK_ROUTES = {
    # Extração paralela das fontes
    "task_identidade_extrair_se1426": {"queue": "etl_extracao"},
    "task_identidade_extrair_coresso": {"queue": "etl_extracao"},
    "task_identidade_extrair_eol_alunos": {"queue": "etl_extracao"},
    # Resolução de identidade (merge + dedup + decisão)
    "task_identidade_resolver_identidade": {"queue": "etl_transformacao"},
    # Provisionamento no Keycloak — fila própria, desacoplada do
    # token-ms (que roda em paralelo, sem esperar este lote terminar)
    "task_provisionar_identidade_keycloak": {"queue": "etl_carga_keycloak"},
    # Carga no token-ms — task de lote (disparada pelo sucesso do
    # Keycloak, agrupando até _TAMANHO_LOTE_PROVISIONAMENTO usuários
    # por chamada), mais a individual (mantida como fallback manual)
    # e a de lote legado (órfã, sem checagem de hash), todas na mesma fila
    "task_carregar_lote_atributos_token": {"queue": "etl_carga_token_ms"},
    "task_carregar_atributo_token_individual": {"queue": "etl_carga_token_ms"},
    "task_carregar_atributos_token": {"queue": "etl_carga_token_ms"},
    # Controle operacional e limpeza
    "task_sync_rec_etl": {"queue": "celery"},
    "task_identidade_limpar_staging": {"queue": "celery"},
    # Orquestrador do pipeline completo
    "task_identidade_executar_pipeline": {"queue": "celery"},
}

# ---------------------------------------------------------------------------
# Keycloak
# ---------------------------------------------------------------------------
KEYCLOAK_URL_SERVIDOR = os.getenv(
    "KEYCLOAK_URL_SERVIDOR", "https://localhost:8080/"
)
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "COTIC")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "api-middleware")
KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "")
KEYCLOAK_USUARIO_ADMIN = os.getenv("KEYCLOAK_USUARIO_ADMIN", "admin")
KEYCLOAK_SENHA_ADMIN = os.getenv("KEYCLOAK_SENHA_ADMIN", "admin")
KEYCLOAK_VERIFICAR_SSL = (
    os.getenv("KEYCLOAK_VERIFICAR_SSL", "true").lower() == "true"
)
KEYCLOAK_SUFIXO_CLIENT = os.getenv("KEYCLOAK_SUFIXO_CLIENT", "prod")

# ---------------------------------------------------------------------------
# token-ms
# ---------------------------------------------------------------------------
# TOKEN_MS_URL deve incluir o prefixo de serviço do token-ms
# (ex.: "https://host/identidade-token") — cliente_token_ms.py
# concatena apenas "/api/v1/etl/push-batch" a este valor.
TOKEN_MS_URL = os.getenv(
    "TOKEN_MS_URL", "https://token-ms:8000/identidade-token"
)
TOKEN_MS_API_KEY = os.getenv("TOKEN_MS_API_KEY", "")
TOKEN_MS_API_KEY_HEADER = os.getenv("TOKEN_MS_API_KEY_HEADER", "X-API-Key")
TOKEN_MS_TIMEOUT = int(os.getenv("TOKEN_MS_TIMEOUT", "60"))
TOKEN_MS_TAMANHO_LOTE = int(os.getenv("TOKEN_MS_TAMANHO_LOTE", "500"))

# ---------------------------------------------------------------------------
# ETL — controles operacionais
# ---------------------------------------------------------------------------
ETL_CARGA_KEYCLOAK_BULK_HABILITADO = (
    os.getenv("ETL_CARGA_KEYCLOAK_BULK_HABILITADO", "false").lower() == "true"
)

# ---------------------------------------------------------------------------
# SE1426 (PRODAM) — SQL Server
# ---------------------------------------------------------------------------
SE1426_DB_SERVIDOR = os.getenv("SE1426_DB_SERVIDOR", "")
SE1426_DB_NOME = os.getenv("SE1426_DB_NOME", "se1426")
SE1426_DB_USUARIO = os.getenv("SE1426_DB_USUARIO", "")
SE1426_DB_SENHA = os.getenv("SE1426_DB_SENHA", "")
SE1426_DB_TIMEOUT = int(os.getenv("SE1426_DB_TIMEOUT", "300"))

SE1426_API_URL = os.getenv(
    "SE1426_API_URL", "https://hom-api.sme.prefeitura.sp.gov.br/api/v1"
)
SE1426_API_TOKEN = os.getenv("SE1426_API_TOKEN", "")
SE1426_API_TIMEOUT = int(os.getenv("SE1426_API_TIMEOUT", "60"))

# ---------------------------------------------------------------------------
# EOL_DB — SQL Server
# ---------------------------------------------------------------------------
EOL_DB_STRING_CONEXAO = os.getenv("EOL_DB_STRING_CONEXAO", "")

# ---------------------------------------------------------------------------
# Controle de volume de extração — aplicado a SE1426, CoreSSO e EOL_DB
# ---------------------------------------------------------------------------
# Tamanho do lote lido por vez do cursor/página (fetchmany / page_size)
ETL_CHUNK_SIZE = int(os.getenv("ETL_CHUNK_SIZE", "500"))
# Teto de registros extraídos por execução, por fonte (0 = sem limite;
# use um valor baixo para testar o pipeline com volume reduzido)
ETL_LOTE_MAXIMO = int(os.getenv("ETL_LOTE_MAXIMO", "0"))

# ---------------------------------------------------------------------------
# ThreadPoolProcessor — paralelização por lotes (apps/controle_etl/libs)
# ---------------------------------------------------------------------------
THREAD_POOL_MAX_WORKERS = int(os.getenv("THREAD_POOL_MAX_WORKERS", "4"))
THREAD_POOL_CHUNK_TIMEOUT = int(os.getenv("THREAD_POOL_CHUNK_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# CoreSSO — SQL Server
# ---------------------------------------------------------------------------
CORESSO_DB_SERVIDOR = os.getenv("CORESSO_DB_SERVIDOR", "")
CORESSO_DB_NOME = os.getenv("CORESSO_DB_NOME", "CoreSSO")
CORESSO_DB_USUARIO = os.getenv("CORESSO_DB_USUARIO", "")
CORESSO_DB_SENHA = os.getenv("CORESSO_DB_SENHA", "")
CORESSO_DB_TIMEOUT = int(os.getenv("CORESSO_DB_TIMEOUT", "300"))
CORESSO_API_URL = os.getenv("CORESSO_API_URL", "")
CORESSO_API_TOKEN = os.getenv("CORESSO_API_TOKEN", "")

# IDs de SYS_Sistema excluídos da extração de sistemas/perfis (CSV de ids)
CORESSO_EXCLUDE_SISTEMA_IDS = [
    int(item)
    for item in os.getenv("CORESSO_EXCLUDE_SISTEMA_IDS", "").split(",")
    if item.strip()
]

# ---------------------------------------------------------------------------
# Elastic APM (opcional)
# ---------------------------------------------------------------------------
ELASTIC_APM = {
    "SERVICE_NAME": os.getenv("ELASTIC_APM_NOME_SERVICO", NOME_APLICACAO),
    "SECRET_TOKEN": os.getenv("ELASTIC_APM_TOKEN_SECRETO", ""),
    "SERVER_URL": os.getenv(
        "ELASTIC_APM_URL_SERVIDOR", "http://localhost:8200"
    ),
    "ENVIRONMENT": os.getenv("ELASTIC_APM_AMBIENTE", AMBIENTE_APLICACAO),
    "ENABLED": os.getenv("ELASTIC_APM_HABILITADO", "0") == "1",
}

# ---------------------------------------------------------------------------
# Logging (python-json-logger — padrão Ateliê)
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
            "rename_fields": {
                "asctime": "timestamp",
                "levelname": "nivel",
                "name": "logger",
            },
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "etl_identidade": {
            "handlers": ["console"],
            "level": NIVEL_LOG,
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": NIVEL_LOG,
    },
}
