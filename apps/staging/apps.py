"""Configuração da app staging."""

from django.apps import AppConfig


class StagingConfig(AppConfig):
    """App de staging intermediário do pipeline ETL."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.staging"
    label = "staging"
    verbose_name = "Staging ETL"
