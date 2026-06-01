"""Configuração global para os testes.

Estratégia:
1. Define variáveis de ambiente antes da inicialização do Django.
2. Usa fixtures do pytest-django para sobrescrever settings necessários
   durante os testes.

Nenhuma credencial real é utilizada neste arquivo.
Todos os valores são fictícios e isolados do ambiente de produção.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("CELERY_BROKER_URL", "memory://")
os.environ.setdefault("CELERY_RESULT_BACKEND", "cache+memory://")
os.environ.setdefault(
    "DJANGO_SECRET_KEY",
    "ci-test-secret-key-not-for-production",
)
os.environ.setdefault("DEBUG", "True")
os.environ.setdefault("ALLOWED_HOSTS", "*")
os.environ.setdefault(
    "KEYCLOAK_SERVER_URL",
    "https://keycloak-test.invalid/",
)
os.environ.setdefault("KEYCLOAK_ADMIN_USER", "ci-admin")
os.environ.setdefault(
    "KEYCLOAK_ADMIN_PAWD",
    "ci-admin-not-real",
)
os.environ.setdefault("KEYCLOAK_REALM", "sme-apps")
os.environ.setdefault("KEYCLOAK_VERIFY_SSL", "false")
os.environ.setdefault("SE1426_DB_SERVER", "")
os.environ.setdefault("SE1426_DB_NAME", "se1426_test")
os.environ.setdefault("SE1426_DB_USER", "ci-user")
os.environ.setdefault(
    "SE1426_DB_PASSWORD",
    "ci-not-a-real-password",
)
os.environ.setdefault("SE1426_DB_TIMEOUT", "5")
os.environ.setdefault("CORESSO_DB_SERVER", "")
os.environ.setdefault("CORESSO_DB_NAME", "coresso_test")
os.environ.setdefault("CORESSO_DB_USER", "ci-user")
os.environ.setdefault(
    "CORESSO_DB_PASSWORD",
    "ci-not-a-real-password",
)
os.environ.setdefault("CORESSO_DB_TIMEOUT", "5")
os.environ.setdefault(
    "TOKEN_MS_URL",
    "https://token-ms-test.invalid",
)
os.environ.setdefault(
    "TOKEN_MS_INTERNAL_TOKEN",
    "ci-internal-token",
)
os.environ.setdefault("TOKEN_MS_TIMEOUT", "5")
os.environ.setdefault("TOKEN_MS_BATCH_SIZE", "100")
os.environ.setdefault(
    "SME_INTEGRACAO_BASE_URL",
    "https://sme-integracao-test.invalid",
)
os.environ.setdefault("SME_INTEGRACAO_TIMEOUT", "5")
os.environ.setdefault(
    "RABBITMQ_URL",
    "amqp://ci-user:ci-not-real@localhost.invalid:5672/",
)
os.environ.setdefault(
    "ETL_LOAD_KEYCLOAK_BULK_ENABLED",
    "false",
)

@pytest.fixture(autouse=True)
def override_celery_and_apps(settings):
    """Configura o ambiente de testes para executar tarefas Celery de forma síncrona.

    Esta fixture é aplicada automaticamente a todos os testes e ajusta as
    configurações do Celery para execução imediata das tarefas, propagando
    exceções para facilitar a depuração. Além disso, remove aplicativos
    incompatíveis com esse modo de execução da lista de aplicativos instalados.

    Args:
        settings: Fixture do pytest-django utilizada para sobrescrever
            configurações do Django durante a execução dos testes.
    """
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True

    excluded_apps = {
        "django_celery_beat",
        "django_celery_results",
        "health_check.contrib.celery",
    }

    settings.INSTALLED_APPS = [
        app
        for app in settings.INSTALLED_APPS
        if app not in excluded_apps
    ]
