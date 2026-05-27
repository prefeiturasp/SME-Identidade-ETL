import os

import structlog
from celery.schedules import crontab
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-insecure-change-in-production-etl-ms-key",
)

DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "corsheaders",
    "django_celery_beat",
    "django_celery_results",
    "health_check",
    "health_check.db",
    "health_check.cache",
    "health_check.contrib.celery",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "core.apps.CoreConfig",
    "staging.apps.StagingConfig",
    "extract.apps.ExtractConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "etl_ms.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "etl_ms.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL",
        default="sqlite:///:memory:",
        conn_max_age=600,
        conn_health_checks=True,
    ),
}

DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Token de autenticação interna do ETL-MS (header: X-Internal-Token)
ETL_INTERNAL_TOKEN = os.environ.get("ETL_INTERNAL_TOKEN", "dev-etl-token")

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DATETIME_FORMAT": "%Y-%m-%dT%H:%M:%S%z",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "core.authentication.InternalTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}


SPECTACULAR_SETTINGS = {
    "TITLE": "ETL-MS API — SME Identidade",
    "DESCRIPTION": (
        "Microsserviço ETL: extração (SE1426, EOL_DB, CORESSO),"
        " transformação, carga no Keycloak e PostgreSQL.\n\n"
        "**Autenticação:** todas as rotas exigem o header `X-Internal-Token`.\n"
        "Clique em **Authorize** (🔒) e informe o token antes de executar qualquer endpoint."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}


_cors_raw = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
CORS_ALLOW_ALL_ORIGINS = DEBUG or "*" in _cors_raw
CORS_ALLOWED_ORIGINS = [] if CORS_ALLOW_ALL_ORIGINS else _cors_raw


# Broker: KeyDB (drop-in Redis replacement) — em produção injeta CELERY_BROKER_URL=redis://keydb:6379/2
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/2")
CELERY_RESULT_BACKEND = "django-db"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "America/Sao_Paulo"
CELERY_ENABLE_UTC = True

CELERY_TASK_ROUTES = {
    "extract.tasks.extract_se1426": {"queue": "etl_extract"},
    "extract.tasks.extract_eol_db": {"queue": "etl_extract"},
    "extract.tasks.extract_eol_alunos": {"queue": "etl_extract"},
    "extract.tasks.extract_coresso": {"queue": "etl_extract"},
    "staging.tasks.transform_staging": {"queue": "etl_transform"},
    "staging.tasks.crossref_dedup": {"queue": "etl_transform"},
    "core.tasks.load_keycloak": {"queue": "etl_load"},
    "core.tasks.load_token_ms": {"queue": "etl_load"},
    "core.tasks.audit_etl": {"queue": "celery"},
}

# Limites de memória por worker para evitar SIGKILL por OOM killer
# max_memory_per_child: recicla o processo quando ultrapassar 400 MB (em KB)
# max_tasks_per_child: recicla após 50 tarefas, liberando memória acumulada (leak gradual)
CELERY_WORKER_MAX_MEMORY_PER_CHILD = int(
    os.environ.get("CELERY_WORKER_MAX_MEMORY_PER_CHILD", "409600")  # 400 MB em KB
)
CELERY_WORKER_MAX_TASKS_PER_CHILD = int(
    os.environ.get("CELERY_WORKER_MAX_TASKS_PER_CHILD", "50")
)

# Controle de reentrega em caso de SIGKILL (OOM killer / crash do worker)
# acks_late: mantido True para não perder mensagens em crash do broker.
# reject_on_worker_lost: DESABILITADO (False) — quando o worker é morto pelo OOM killer
#   ou pelo kernel (SIGKILL), a task NÃO é recolocada na fila automaticamente.
#   O operador deve reiniciar manualmente via POST /api/etl/executions/ com nova execução,
#   garantindo que não haja reexecuções automáticas não supervisionadas.
# prefetch_multiplier=1: cada worker segura no máximo 1 mensagem por vez.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = False
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# Agendamento automático desabilitado por padrão — ativar via ETL_BEAT_SCHEDULE_ENABLED=true
# Toda carga deve ser iniciada manualmente via POST /api/etl/executions/
_BEAT_ENABLED = os.environ.get("ETL_BEAT_SCHEDULE_ENABLED", "false").lower() == "true"
_BEAT_REALM = os.environ.get("KEYCLOAK_REALM", "sme-apps")

CELERY_BEAT_SCHEDULE = {
    "etl-daily-all-sources": {
        "task": "core.tasks.trigger_scheduled_etl",
        "schedule": crontab(hour=2, minute=0),
        "kwargs": {"source": "all", "realm": _BEAT_REALM},
        "options": {"queue": "celery"},
    },
} if _BEAT_ENABLED else {}


RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://identidade:identidade@localhost:5672/sme")


KEYCLOAK_SERVER_URL = os.environ.get("KEYCLOAK_SERVER_URL", "http://localhost:8080/")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "sme-apps")
KEYCLOAK_CLIENT_ID = os.environ.get("KEYCLOAK_CLIENT_ID", "api-middleware")
KEYCLOAK_CLIENT_SECRET = os.environ.get("KEYCLOAK_CLIENT_SECRET", "")
KEYCLOAK_ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN_USER", "admin")
KEYCLOAK_ADMIN_PAWD = os.environ.get("KEYCLOAK_ADMIN_PAWD", "admin")
KEYCLOAK_CLIENT_SUFFIX = os.environ.get("KEYCLOAK_CLIENT_SUFFIX", "prod")
KEYCLOAK_VERIFY_SSL = os.environ.get("KEYCLOAK_VERIFY_SSL", "true").lower() == "true"

# Número de threads concorrentes para o step 6 (Load Keycloak).
# 20 = padrão. Reduz carga de 87k usuários de ~5h para ~15min.
# Aumentar para 50 para ~6min. Cada thread usa seu próprio KeycloakAdmin client.
ETL_KC_CONCURRENCY = int(os.environ.get("ETL_KC_CONCURRENCY", "20"))

# Tamanho do lote por batch no modo concorrente (default 500).
# Batches maiores reduzem overhead de thread pool mas aumentam uso de memória.
ETL_KC_BATCH_SIZE = int(os.environ.get("ETL_KC_BATCH_SIZE", "500"))


TOKEN_MS_URL = os.environ.get("TOKEN_MS_URL", "http://token-ms:8000")
TOKEN_MS_TOKEN = os.environ.get("TOKEN_MS_TOKEN", "")
TOKEN_MS_INTERNAL_TOKEN = os.environ.get("TOKEN_MS_INTERNAL_TOKEN", "")
TOKEN_MS_TIMEOUT = int(os.environ.get("TOKEN_MS_TIMEOUT", "60"))
TOKEN_MS_BATCH_SIZE = int(os.environ.get("TOKEN_MS_BATCH_SIZE", "500"))

# Tamanho do lote para extração e insert no staging.
# Valores maiores reduzem roundtrips ao SQL Server e ao PostgreSQL.
# Ajustar via ETL_EXTRACT_BATCH_SIZE se o worker sofrer pressão de memória.
ETL_EXTRACT_BATCH_SIZE = int(os.environ.get("ETL_EXTRACT_BATCH_SIZE", "50000"))


NIFI_API_URL = os.environ.get("NIFI_API_URL", "http://localhost:8443/nifi-api")


SE1426_API_URL = os.environ.get(
    "SE1426_API_URL", "https://hom-api.sme.prefeitura.sp.gov.br/api/v1"
)
SE1426_API_TOKEN = os.environ.get("SE1426_API_TOKEN", "")
SE1426_API_TIMEOUT = int(os.environ.get("SE1426_API_TIMEOUT", "60"))


SE1426_DB_SERVER = os.environ.get("SE1426_DB_SERVER", "")
SE1426_DB_NAME = os.environ.get("SE1426_DB_NAME", "se1426")
SE1426_DB_USER = os.environ.get("SE1426_DB_USER", "")
SE1426_DB_PASSWORD = os.environ.get("SE1426_DB_PASSWORD", "")
SE1426_DB_TIMEOUT = int(os.environ.get("SE1426_DB_TIMEOUT", "300"))

EOL_DB_CONNECTION_STRING = os.environ.get("EOL_DB_CONNECTION_STRING", "")
CORESSO_API_URL = os.environ.get("CORESSO_API_URL", "")
CORESSO_API_TOKEN = os.environ.get("CORESSO_API_TOKEN", "")


CORESSO_DB_SERVER = os.environ.get("CORESSO_DB_SERVER", "")
CORESSO_DB_NAME = os.environ.get("CORESSO_DB_NAME", "CoreSSO")
CORESSO_DB_USER = os.environ.get("CORESSO_DB_USER", "")
CORESSO_DB_PASSWORD = os.environ.get("CORESSO_DB_PASSWORD", "")
CORESSO_DB_TIMEOUT = int(os.environ.get("CORESSO_DB_TIMEOUT", "300"))

# IDs de sistemas do CoreSSO a EXCLUIR da extração de usuários (SYS_Sistema.sis_id).
# Usuários cujo ÚNICO acesso seja a estes sistemas não serão extraídos.
# Exemplo: CORESSO_EXCLUDE_SISTEMA_IDS=174,200
_coresso_exclude_raw = os.environ.get("CORESSO_EXCLUDE_SISTEMA_IDS", "")
CORESSO_EXCLUDE_SISTEMA_IDS: list[int] = (
    [int(x.strip()) for x in _coresso_exclude_raw.split(",") if x.strip().isdigit()]
    if _coresso_exclude_raw.strip()
    else []
)


SME_INTEGRACAO_BASE_URL = os.environ.get(
    "SME_INTEGRACAO_BASE_URL",
    "https://hom-smeintegracaoapi.sme.prefeitura.sp.gov.br",
)
SME_INTEGRACAO_API_KEY = os.environ.get("SME_INTEGRACAO_API_KEY", "")
SME_INTEGRACAO_LOGIN = os.environ.get("SME_INTEGRACAO_LOGIN", "")
SME_INTEGRACAO_PASSWORD = os.environ.get("SME_INTEGRACAO_PASSWORD", "")
SME_INTEGRACAO_TIMEOUT = int(os.environ.get("SME_INTEGRACAO_TIMEOUT", "30"))


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer()
            if DEBUG
            else structlog.processors.JSONRenderer(),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.environ.get("LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "extract": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "staging": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        "core": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
    },
}
