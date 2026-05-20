"""Configuracao do app Django para o modulo de staging."""
from django.apps import AppConfig


class StagingConfig(AppConfig):
    """Configuracao do app de staging do ETL — dados intermediarios transformados."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "staging"
    verbose_name = "ETL Staging — Dados Intermediários"
