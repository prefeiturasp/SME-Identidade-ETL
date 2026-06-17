"""Configuração da app extracao."""

from django.apps import AppConfig


class ExtracaoConfig(AppConfig):
    """App de extração das fontes SE1426 e CoreSSO."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.extracao"
    label = "extracao"
    verbose_name = "Extração ETL"
