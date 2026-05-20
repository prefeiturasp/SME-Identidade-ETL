"""Serializers DRF para rastreamento das execucoes do ETL e respostas de health check."""
from rest_framework import serializers

from .models import ETLExecution, ETLStepLog, UpsertControl


class ETLStepLogSerializer(serializers.ModelSerializer):
    """Serializer somente-leitura para entradas de log de etapa do ETL."""

    class Meta:
        """Metadados do serializer ETLStepLogSerializer."""

        model = ETLStepLog
        fields = [
            "id",
            "step_name",
            "step_order",
            "status",
            "records_in",
            "records_out",
            "records_error",
            "started_at",
            "finished_at",
            "error_detail",
            "metadata",
        ]
        read_only_fields = fields


class ETLExecutionSerializer(serializers.ModelSerializer):
    """Serializer completo para uma execucao de ETL, incluindo logs de etapas aninhados."""

    steps = ETLStepLogSerializer(many=True, read_only=True)
    duration_seconds = serializers.FloatField(read_only=True)

    class Meta:
        """Metadados do serializer ETLExecutionSerializer."""

        model = ETLExecution
        fields = [
            "id",
            "trigger_type",
            "status",
            "source",
            "target_realm",
            "total_extracted",
            "total_transformed",
            "total_loaded",
            "total_errors",
            "total_skipped",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
            "celery_task_id",
            "note",
            "executed_by",
            "duration_seconds",
            "steps",
        ]
        read_only_fields = [
            "id",
            "status",
            "total_extracted",
            "total_transformed",
            "total_loaded",
            "total_errors",
            "total_skipped",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
            "celery_task_id",
            "duration_seconds",
            "steps",
        ]


class ETLExecutionCreateSerializer(serializers.Serializer):
    """Serializer de entrada para criar uma nova execucao de ETL via API."""

    source = serializers.ChoiceField(
        choices=["all", "se1426", "eol_db", "coresso"],
        default="all",
    )
    target_realm = serializers.CharField(default="sme-apps")
    note = serializers.CharField(required=False, allow_blank=True)


class ETLExecutionListSerializer(serializers.ModelSerializer):
    """Serializer enxuto para listagem de execucoes de ETL."""

    class Meta:
        """Metadados do serializer ETLExecutionListSerializer."""

        model = ETLExecution
        fields = [
            "id",
            "trigger_type",
            "status",
            "source",
            "target_realm",
            "total_extracted",
            "total_loaded",
            "total_errors",
            "created_at",
            "started_at",
            "finished_at",
        ]
        read_only_fields = fields


class UpsertControlSerializer(serializers.ModelSerializer):
    """Serializer somente-leitura para registros de controle de upsert."""

    class Meta:
        """Metadados do serializer UpsertControlSerializer."""

        model = UpsertControl
        fields = [
            "id",
            "entity_type",
            "source_system",
            "source_id",
            "target_id",
            "target_realm",
            "content_hash",
            "version",
            "is_active",
            "sync_error",
            "last_synced_at",
            "created_at",
        ]
        read_only_fields = fields


class CoreSSOHealthSerializer(serializers.Serializer):
    """Schema da resposta de health check do CoreSSO / banco SQL Server."""

    source = serializers.CharField()
    server = serializers.CharField(allow_blank=True)
    server_host = serializers.CharField(allow_blank=True, required=False)
    database = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    response_time_ms = serializers.IntegerField(required=False)
    sql_server_version = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    connected_db = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    connected_user = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    table_count = serializers.IntegerField(required=False)
    detail = serializers.CharField(required=False)


class SMEIntegracaoHealthSerializer(serializers.Serializer):
    """Schema da resposta de health check da API SME Integracao."""

    source = serializers.CharField()
    base_url = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    response_time_ms = serializers.IntegerField(required=False)
    http_status = serializers.IntegerField(required=False)
    swagger_available = serializers.BooleanField(required=False)
    authentication = serializers.CharField(required=False)
    auth_token_present = serializers.BooleanField(required=False)
    auth_token_length = serializers.IntegerField(required=False)
    data_access = serializers.CharField(required=False)
    auth_error = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)


class ETLExternalSourcesSerializer(serializers.Serializer):
    """Dados de health agregados de todas as fontes externas."""

    coresso_db = CoreSSOHealthSerializer()
    sme_integracao_api = SMEIntegracaoHealthSerializer()


class ETLExternalHealthResponseSerializer(serializers.Serializer):
    """Schema da resposta principal do endpoint de health das fontes externas."""

    status = serializers.CharField()
    sources = ETLExternalSourcesSerializer()
