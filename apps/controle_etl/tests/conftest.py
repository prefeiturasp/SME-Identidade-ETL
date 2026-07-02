"""Fixtures pytest para a app controle_etl."""

from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("SYNC_REC_DB_URL", "sqlite:///:memory:")
os.environ.setdefault("STAGING_DB_URL", "sqlite:///:memory:")
os.environ.setdefault("URL_KEYDB", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "1")


@pytest.fixture(autouse=True)
def celery_eager(settings: Any) -> None:
    """Executa tarefas Celery de forma síncrona nos testes."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
