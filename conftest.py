"""
Configuração global de testes — substitui etl_ms/settings_test.py.

Estratégia:
  1. os.environ são definidos ANTES do Django carregar (pytest-django é lazy).
  2. A fixture `django_test_settings` usa o override de settings do pytest-django
     para sobrescrever INSTALLED_APPS e flags Celery que não são env vars.

Nenhuma credencial real aqui — todos os valores são claramente fictícios
e isolados do ambiente de produção.
"""

import os

import django
import pytest

# ---------------------------------------------------------------------------
# 1. Env vars de infraestrutura — definidas antes do Django inicializar
#    (pytest-django aplica DJANGO_SETTINGS_MODULE apenas no setup do fixture)
# ---------------------------------------------------------------------------
os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault("DJANGO_SECRET_KEY", "ci-test-secret-key-not-for-production")
os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("ALLOWED_HOSTS", "*")

# Serviços externos — endereços fictícios, nunca contactados nos testes unitários
os.environ.setdefault("KEYCLOAK_SERVER_URL", "https://keycloak-test.invalid/")
os.environ.setdefault("KEYCLOAK_ADMIN_USER", "ci-admin")
os.environ.setdefault("KEYCLOAK_ADMIN_PAWD", "ci-admin-not-real")
os.environ.setdefault("KEYCLOAK_REALM", "sme-apps")
os.environ.setdefault("KEYCLOAK_VERIFY_SSL", "false")

os.environ.setdefault("SE1426_DB_SERVER", "")
os.environ.setdefault("SE1426_DB_NAME", "se1426_test")
os.environ.setdefault("SE1426_DB_USER", "ci-user")
os.environ.setdefault("SE1426_DB_PASSWORD", "ci-not-a-real-password")
os.environ.setdefault("SE1426_DB_TIMEOUT", "5")

os.environ.setdefault("CORESSO_DB_SERVER", "")
os.environ.setdefault("CORESSO_DB_NAME", "coresso_test")
os.environ.setdefault("CORESSO_DB_USER", "ci-user")
os.environ.setdefault("CORESSO_DB_PASSWORD", "ci-not-a-real-password")
os.environ.setdefault("CORESSO_DB_TIMEOUT", "5")

os.environ.setdefault("TOKEN_MS_URL", "https://token-ms-test.invalid")
os.environ.setdefault("TOKEN_MS_INTERNAL_TOKEN", "ci-internal-token")
os.environ.setdefault("TOKEN_MS_TIMEOUT", "5")
os.environ.setdefault("TOKEN_MS_BATCH_SIZE", "100")

os.environ.setdefault("SME_INTEGRACAO_BASE_URL", "https://sme-integracao-test.invalid")
os.environ.setdefault("SME_INTEGRACAO_TIMEOUT", "5")

os.environ.setdefault("RABBITMQ_URL", "amqp://ci-user:ci-not-real@localhost.invalid:5672/")

os.environ.setdefault("ETL_LOAD_KEYCLOAK_BULK_ENABLED", "false")


# ---------------------------------------------------------------------------
# 2. Fixture autouse — overrides que não podem ser feitos via env var
#    (INSTALLED_APPS, flags booleanas Celery)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def override_celery_and_apps(settings):
    """
    Override por teste: Celery eager + remove apps incompatíveis com SQLite.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True

    settings.INSTALLED_APPS = [
        app for app in settings.INSTALLED_APPS
        if app not in (
            "django_celery_beat",
            "django_celery_results",
            "health_check.contrib.celery",
        )
    ]
