"""Configuração Celery do SME-Identidade-ETL."""

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("identidade_etl")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Beat agenda o pipeline completo diariamente às 2h00
# O orquestrador (task_identidade_executar_pipeline) despacha extração
# paralela de todas as fontes configuradas + cadeia completa de etapas.
app.conf.beat_schedule = {
    "etl-diario-todas-fontes": {
        "task": "task_identidade_executar_pipeline",
        "schedule": crontab(hour=2, minute=0),
        "kwargs": {"fonte": "todos"},
        "options": {"queue": "celery"},
    },
}


@app.task(bind=True, ignore_result=True)
def tarefa_debug(self) -> None:  # type: ignore[override]
    """Tarefa de debug para verificar o worker Celery."""
    print(f"Requisição: {self.request!r}")


# Finaliza o app e resolve o backend imediatamente: sem isso, chamar uma
# task com chord/group fora de um worker (ex. via management command,
# em modo CELERY_TASK_ALWAYS_EAGER) pode ver o backend como DisabledBackend
# antes da primeira resolução lazy de app.backend acontecer.
app.finalize()
_ = app.backend
