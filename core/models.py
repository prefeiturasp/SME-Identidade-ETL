import uuid

from django.db import models
from django.utils import timezone


class ETLExecution(models.Model):

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        RUNNING = "running", "Em execução"
        SUCCESS = "success", "Sucesso"
        PARTIAL = "partial", "Parcial (com erros)"
        FAILED = "failed", "Falhou"
        CANCELLED = "cancelled", "Cancelado"

    class TriggerType(models.TextChoices):
        SCHEDULED = "scheduled", "Agendado (Beat)"
        MANUAL = "manual", "Manual (API)"
        NIFI = "nifi", "Trigger NiFi"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    trigger_type = models.CharField(
        max_length=20,
        choices=TriggerType.choices,
        default=TriggerType.SCHEDULED,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    total_extracted = models.IntegerField(default=0, help_text="Total de registros extraídos")
    total_transformed = models.IntegerField(default=0, help_text="Total após transformação")
    total_loaded = models.IntegerField(default=0, help_text="Total carregados no destino")
    total_errors = models.IntegerField(default=0, help_text="Total de erros")
    total_skipped = models.IntegerField(default=0, help_text="Total ignorados (dedup)")

    source = models.CharField(
        max_length=50,
        default="all",
        help_text="Fonte: all, se1426, eol_db, coresso",
    )
    target_realm = models.CharField(
        max_length=100,
        default="sme-apps",
        help_text="Realm Keycloak de destino",
    )

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    celery_task_id = models.CharField(max_length=255, blank=True, null=True)

    note = models.TextField(blank=True, null=True, help_text="Observações da execução")
    executed_by = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Usuário ou sistema que disparou",
    )

    class Meta:
        db_table = "etl_execution"
        ordering = ["-created_at"]
        verbose_name = "Execução ETL"
        verbose_name_plural = "Execuções ETL"
        indexes = [
            models.Index(fields=["-created_at"], name="idx_exec_created"),
            models.Index(fields=["status"], name="idx_exec_status"),
            models.Index(fields=["source"], name="idx_exec_source"),
        ]

    def __str__(self):
        return f"ETL-{self.id.hex[:8]} [{self.status}] {self.source}"

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def mark_running(self):
        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at", "updated_at"])

    def mark_finished(self, status: str = "success"):
        self.status = status
        self.finished_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "finished_at",
                "total_extracted",
                "total_transformed",
                "total_loaded",
                "total_errors",
                "total_skipped",
                "updated_at",
            ]
        )


class ETLStepLog(models.Model):

    class StepName(models.TextChoices):
        EXTRACT_SE1426 = "extract_se1426", "1. Extract SE1426"
        EXTRACT_EOL_DB = "extract_eol_db", "1b. Extract EOL_DB"
        EXTRACT_CORESSO = "extract_coresso", "2. Extract CORESSO"
        STAGING = "staging", "3. Staging Insert"
        CROSSREF_DEDUP = "crossref_dedup", "4. Crossref / Dedup"
        DECISION = "decision", "5. Decision (Create/Update)"
        LOAD_KEYCLOAK = "load_keycloak", "6. Load Keycloak"
        LOAD_TOKEN_MS = "load_token_ms", "7. Load token-ms"
        AUDIT = "audit", "8. Audit"

    class StepStatus(models.TextChoices):
        RUNNING = "running", "Em execução"
        SUCCESS = "success", "Sucesso"
        FAILED = "failed", "Falhou"
        SKIPPED = "skipped", "Ignorado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    execution = models.ForeignKey(
        ETLExecution,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    step_name = models.CharField(max_length=30, choices=StepName.choices)
    step_order = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=20,
        choices=StepStatus.choices,
        default=StepStatus.RUNNING,
    )

    records_in = models.IntegerField(default=0)
    records_out = models.IntegerField(default=0)
    records_error = models.IntegerField(default=0)

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    error_detail = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "etl_step_log"
        ordering = ["execution", "step_order"]
        verbose_name = "Log de Etapa ETL"
        verbose_name_plural = "Logs de Etapas ETL"
        unique_together = [("execution", "step_name")]

    def __str__(self):
        return f"{self.execution_id.hex[:8]} → {self.step_name} [{self.status}]"


class UpsertControl(models.Model):

    class EntityType(models.TextChoices):
        USER = "user", "Usuário"
        GROUP = "group", "Grupo"
        ROLE = "role", "Role"
        CLIENT = "client", "Client"

    id = models.BigAutoField(primary_key=True)

    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    source_system = models.CharField(
        max_length=50,
        help_text="Sistema de origem: se1426, eol_db, coresso",
    )
    source_id = models.CharField(
        max_length=255,
        help_text="ID do registro na fonte (RF, CPF, matrícula, etc.)",
    )
    target_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="ID no destino (Keycloak user ID, etc.)",
    )
    target_realm = models.CharField(max_length=100, default="sme-apps")

    content_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 dos campos significativos",
    )

    version = models.PositiveIntegerField(default=1)
    last_synced_at = models.DateTimeField(auto_now=True)
    last_execution = models.ForeignKey(
        ETLExecution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="upsert_records",
    )

    is_active = models.BooleanField(default=True)
    sync_error = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "etl_upsert_control"
        verbose_name = "Controle de Upsert"
        verbose_name_plural = "Controles de Upsert"
        unique_together = [("entity_type", "source_system", "source_id", "target_realm")]
        indexes = [
            models.Index(fields=["entity_type", "source_system"], name="idx_upsert_entity_source"),
            models.Index(fields=["source_id"], name="idx_upsert_source_id"),
            models.Index(fields=["target_id"], name="idx_upsert_target_id"),
            models.Index(fields=["content_hash"], name="idx_upsert_hash"),
        ]

    def __str__(self):
        return f"{self.entity_type}:{self.source_system}:{self.source_id} v{self.version}"
