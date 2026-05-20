"""Configuracao do app Django para o modulo core do ETL."""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configuracao do app do core do ETL — gerenciamento de execucoes e carga no Keycloak."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "ETL Core — Execução e Carga"
