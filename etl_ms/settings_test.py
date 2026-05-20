from .settings import *  # noqa: F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

ETL_LOAD_KEYCLOAK_BULK_ENABLED = False

KEYCLOAK_SERVER_URL = "http://keycloak-test:8080/auth/"
KEYCLOAK_ADMIN_USER = "admin"
KEYCLOAK_ADMIN_PASSWORD = "admin"
KEYCLOAK_REALM = "sme-apps"
KEYCLOAK_VERIFY_SSL = False

SE1426_DB_SERVER = "localhost"
SE1426_DB_NAME = "se1426"
SE1426_DB_USER = "user"
SE1426_DB_PASSWORD = "pass"
SE1426_DB_TIMEOUT = 5

CORESSO_DB_SERVER = ""
CORESSO_DB_NAME = "coresso"
CORESSO_DB_USER = "user"
CORESSO_DB_PASSWORD = "pass"
CORESSO_DB_TIMEOUT = 5

TOKEN_MS_URL = "http://token-ms-test:8000"
TOKEN_MS_TOKEN = "test-token"
TOKEN_MS_INTERNAL_TOKEN = "test-internal-token"
TOKEN_MS_TIMEOUT = 5
TOKEN_MS_BATCH_SIZE = 100

SME_INTEGRACAO_URL = "http://sme-integracao-test:8000"
SME_INTEGRACAO_TOKEN = "test-token"
SME_INTEGRACAO_TIMEOUT = 5

RABBITMQ_URL = "amqp://guest:guest@localhost:5672/"

INSTALLED_APPS = [app for app in INSTALLED_APPS  # noqa: F405
                  if app not in ("django_celery_beat", "django_celery_results",
                                 "health_check.contrib.celery")]
