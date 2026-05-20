"""Configuracao do app Django para o modulo de extract."""
from django.apps import AppConfig


class ExtractConfig(AppConfig):
    """Configuracao do app de extract do ETL — extracao das fontes legadas."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "extract"
    verbose_name = "ETL Extract — Extração de Fontes Legadas"
