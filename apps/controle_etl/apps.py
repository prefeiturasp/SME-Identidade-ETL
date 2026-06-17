"""Configuração da app controle_etl."""

from django.apps import AppConfig


class ControleEtlConfig(AppConfig):
    """App de controle operacional do pipeline ETL (SYNC_REC_DB)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.controle_etl"
    label = "controle_etl"
    verbose_name = "Controle ETL"
