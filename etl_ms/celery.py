import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "etl_ms.settings")

app = Celery("etl_ms")
app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Aplicacao Celery do ETL-MS."""
    print(f"Request: {self.request!r}")
