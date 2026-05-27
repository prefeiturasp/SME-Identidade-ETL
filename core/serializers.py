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
            "load_keycloak",
            "load_token_ms",
            "max_records",
            "max_records_extract",
            "user_types",
            "skip_steps",
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

    def validate_user_types(self, value):
        """Valida o campo user_types."""
        valid = {"all", "servidor", "aluno", "terceiro"}
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if not parts:
            raise serializers.ValidationError("user_types não pode ser vazio.")
        for part in parts:
            if part not in valid:
                raise serializers.ValidationError(
                    f"Valor inválido '{part}'. Use: all, servidor, aluno, terceiro."
                )
        return value

    def validate_skip_steps(self, value):
        """Valida que skip_steps contém apenas nomes de steps conhecidos."""
        valid = {
            "sync_catalogo", "extract_se1426", "extract_eol_db",
            "extract_coresso", "staging", "crossref_dedup",
            "decision", "load_keycloak", "load_token_ms", "audit",
        }
        if not isinstance(value, list):
            raise serializers.ValidationError("skip_steps deve ser uma lista.")
        for step in value:
            if step not in valid:
                raise serializers.ValidationError(
                    f"Step inválido '{step}'. Valores válidos: {sorted(valid)}"
                )
        return value


class ETLExecutionCreateSerializer(serializers.Serializer):
    """Serializer de entrada para criar uma nova execucao de ETL via API."""

    source = serializers.ChoiceField(
        choices=["all", "se1426", "eol_db", "coresso"],
        default="all",
    )
    target_realm = serializers.CharField(default="sme-apps")
    note = serializers.CharField(required=False, allow_blank=True)
    load_keycloak = serializers.BooleanField(
        default=False,
        help_text="Habilita o step 6 (carga no Keycloak). Padrão: false.",
    )
    load_token_ms = serializers.BooleanField(
        default=True,
        help_text="Habilita o step 7 (envio ao Token-MS). Padrão: true.",
    )
    max_records = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="Limita usuários sincronizados no Keycloak (step 6). Útil para testes de carga. Nulo = sem limite.",
    )
    max_records_extract = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="Limita registros extraídos por fonte nos steps 1 e 2. Nulo = sem limite.",
    )
    user_types = serializers.CharField(
        required=False,
        default="all",
        help_text="Tipos a processar: 'all', 'servidor', 'aluno', 'terceiro' ou combinações separadas por vírgula.",
    )
    skip_steps = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text="Steps a pular: sync_catalogo, extract_se1426, extract_eol_db, extract_coresso, staging, crossref_dedup, decision, load_keycloak, load_token_ms, audit.",
    )

    def validate_user_types(self, value):
        """Valida o campo user_types."""
        valid = {"all", "servidor", "aluno", "terceiro"}
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if not parts:
            raise serializers.ValidationError("user_types não pode ser vazio.")
        for part in parts:
            if part not in valid:
                raise serializers.ValidationError(
                    f"Valor inválido '{part}'. Use: all, servidor, aluno, terceiro."
                )
        return value

    def validate_skip_steps(self, value):
        """Valida que skip_steps contém apenas nomes de steps conhecidos."""
        valid = {
            "sync_catalogo", "extract_se1426", "extract_eol_db",
            "extract_coresso", "staging", "crossref_dedup",
            "decision", "load_keycloak", "load_token_ms", "audit",
        }
        if not isinstance(value, list):
            raise serializers.ValidationError("skip_steps deve ser uma lista.")
        for step in value:
            if step not in valid:
                raise serializers.ValidationError(
                    f"Step inválido '{step}'. Valores válidos: {sorted(valid)}"
                )
        return value


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


class RunStepSerializer(serializers.Serializer):
    """Entrada generica para re-executar um step individual de uma execucao existente."""

    force = serializers.BooleanField(
        default=True,
        help_text=(
            "Se True (padrão), apaga o ETLStepLog existente e força re-execução mesmo se já concluído."
        ),
    )


class RunExtractSerializer(serializers.Serializer):
    """Entrada para re-executar um ou mais steps de extração de uma execucao existente."""

    source = serializers.ChoiceField(
        choices=["all", "se1426", "eol_db", "eol_alunos", "coresso"],
        default="all",
        help_text=(
            "Fonte a extrair. 'all' executa todas as 4 fontes. "
            "'se1426' apenas servidores, 'eol_db' EOL (+ alunos), "
            "'eol_alunos' apenas alunos, 'coresso' apenas CoreSSO."
        ),
    )
    force = serializers.BooleanField(
        default=True,
        help_text="Se True (padrão), apaga ETLStepLogs existentes e força re-execução.",
    )


class ReloadKeycloakSerializer(serializers.Serializer):
    """Entrada do endpoint que re-dispara apenas o step 6 (Load Keycloak) de uma execucao existente."""

    max_records = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="Limita o número de usuários carregados. Nulo = sem limite.",
    )
    reset_loaded = serializers.BooleanField(
        default=True,
        help_text=(
            "Se True (padrão), reseta os registros 'loaded' → 'ready' antes de recarregar. "
            "Use False para carregar apenas registros ainda em 'ready'."
        ),
    )


class SyncSelectiveSerializer(serializers.Serializer):
    """Entrada do endpoint de sincronizacao seletiva por CPF, RF ou quantidade."""

    cpfs = serializers.ListField(
        child=serializers.CharField(max_length=14),
        required=False,
        default=list,
        help_text="Lista de CPFs (somente dígitos ou formatado).",
    )
    rfs = serializers.ListField(
        child=serializers.CharField(max_length=20),
        required=False,
        default=list,
        help_text="Lista de RFs (Registro Funcional).",
    )
    limit = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=500,
        help_text="Quantidade de registros READY da última execução a sincronizar.",
    )
    realm = serializers.CharField(default="sme-apps")
    load_keycloak = serializers.BooleanField(
        default=False,
        help_text="Envia para o Keycloak. Padrão: false.",
    )
    push_token_ms = serializers.BooleanField(
        default=True,
        help_text="Envia ao Token-MS. Padrão: true.",
    )

    def validate(self, attrs):
        if not attrs.get("cpfs") and not attrs.get("rfs") and not attrs.get("limit"):
            raise serializers.ValidationError(
                "Informe ao menos um de: 'cpfs', 'rfs' ou 'limit'."
            )
        return attrs


class SyncSelectiveResultSerializer(serializers.Serializer):
    """Status individual de um registro na sincronizacao seletiva."""

    identifier = serializers.CharField(help_text="CPF ou RF usado na busca")
    identifier_type = serializers.CharField(help_text="'cpf' ou 'rf'")
    nome = serializers.CharField(allow_null=True)
    source = serializers.CharField(allow_null=True)
    found_in_staging = serializers.BooleanField()
    exists_coresso = serializers.BooleanField(help_text="Encontrado com source=coresso no staging")
    kc_action = serializers.CharField(allow_null=True, help_text="created | updated | skipped | null")
    kc_user_id = serializers.CharField(allow_null=True)
    status = serializers.CharField(help_text="success | skipped | error | not_found")
    error = serializers.CharField(allow_null=True)


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
