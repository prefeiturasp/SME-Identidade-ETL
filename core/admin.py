"""Registros do Django admin para os modelos do core do ETL."""
from django.contrib import admin

from .models import ETLExecution, ETLStepLog, UpsertControl


@admin.register(ETLExecution)
class ETLExecutionAdmin(admin.ModelAdmin):
    """Painel admin das execucoes do pipeline ETL."""

    list_display = [
        "id",
        "status",
        "trigger_type",
        "source",
        "target_realm",
        "total_extracted",
        "total_loaded",
        "total_errors",
        "created_at",
    ]
    list_filter = ["status", "trigger_type", "source"]
    search_fields = ["id", "executed_by"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(ETLStepLog)
class ETLStepLogAdmin(admin.ModelAdmin):
    """Painel admin dos logs de etapas individuais do ETL."""

    list_display = [
        "id",
        "execution",
        "step_name",
        "step_order",
        "status",
        "records_in",
        "records_out",
        "records_error",
        "started_at",
    ]
    list_filter = ["step_name", "status"]
    raw_id_fields = ["execution"]


@admin.register(UpsertControl)
class UpsertControlAdmin(admin.ModelAdmin):
    """Painel admin dos registros de controle de upsert que rastreiam o estado de sincronizacao com o Keycloak."""

    list_display = [
        "id",
        "entity_type",
        "source_system",
        "source_id",
        "target_id",
        "version",
        "is_active",
        "last_synced_at",
    ]
    list_filter = ["entity_type", "source_system", "is_active"]
    search_fields = ["source_id", "target_id"]
