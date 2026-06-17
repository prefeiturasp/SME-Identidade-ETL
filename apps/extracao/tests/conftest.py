"""Fixtures pytest para a app extracao."""

import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("SYNC_REC_DB_URL", "sqlite:///:memory:")
os.environ.setdefault("URL_KEYDB", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")


@pytest.fixture(autouse=True)
def celery_eager(settings):
    """Configura Celery para execução síncrona nos testes."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
