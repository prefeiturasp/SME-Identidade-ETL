from django.apps import AppConfig


class ExtractConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "extract"
    verbose_name = "ETL Extract — Extração de Fontes Legadas"
