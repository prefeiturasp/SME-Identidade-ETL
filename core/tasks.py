"""Tasks Celery para orquestrar o pipeline completo do ETL, da extracao a carga no Keycloak."""
import logging

from celery import chain, chord, shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="core.tasks.trigger_scheduled_etl")
def trigger_scheduled_etl(source: str = "all", realm: str = "sme-apps"):
    """Cria uma nova execucao de ETL agendada e a despacha para o Celery."""
    from .models import ETLExecution

    execution = ETLExecution.objects.create(
        trigger_type=ETLExecution.TriggerType.SCHEDULED,
        source=source,
        target_realm=realm,
        executed_by="celery-beat",
    )
    run_etl_pipeline.delay(str(execution.id))
    logger.info("Scheduled ETL triggered — execution_id=%s source=%s realm=%s", execution.id, source, realm)
    return str(execution.id)


@shared_task(bind=True, name="core.tasks.run_etl_pipeline")
def run_etl_pipeline(self, execution_id: str):
    """Orquestra o pipeline completo do ETL para uma dada execucao via chord/chain do Celery."""
    from .models import ETLExecution

    execution = ETLExecution.objects.get(id=execution_id)
    execution.mark_running()

    logger.info(
        "ETL Pipeline [%s] started — source=%s, realm=%s",
        execution_id, execution.source, execution.target_realm,
    )

    try:
        from extract.tasks import extract_coresso, extract_eol_alunos, extract_eol_db, extract_se1426
        from staging.tasks import crossref_dedup, transform_staging

        extract_tasks = []
        source = execution.source

        if source in ("all", "se1426"):
            extract_tasks.append(extract_se1426.s(execution_id))
        if source in ("all", "eol_db"):
            extract_tasks.append(extract_eol_db.s(execution_id))
        if source in ("all", "eol_alunos", "eol_db"):
            extract_tasks.append(extract_eol_alunos.s(execution_id))
        if source in ("all", "coresso"):
            extract_tasks.append(extract_coresso.s(execution_id))

        if not extract_tasks:
            execution.mark_finished("failed")
            logger.error("No extract tasks for source=%s", source)
            return

        chord(extract_tasks)(
            chain(
                transform_staging.si(execution_id),
                crossref_dedup.si(execution_id),
                decide_target.si(execution_id),
                load_keycloak.si(execution_id),
                load_token_ms.si(execution_id),
                audit_etl.si(execution_id),
            )
        )

        logger.info("ETL Pipeline [%s] dispatched to Celery", execution_id)

    except Exception as e:
        logger.exception("ETL Pipeline [%s] error: %s", execution_id, e)
        execution.total_errors += 1
        execution.mark_finished("failed")


@shared_task(bind=True, name="core.tasks.decide_target")
def decide_target(self, execution_id: str):
    """Decide se cada registro de staging em status READY deve ser criado ou atualizado no Keycloak."""
    from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

    from .keycloak_client import build_kc_payload, build_token_ms_payload
    from .models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)
    step = ETLStepLog.objects.create(
        execution=execution,
        step_name=ETLStepLog.StepName.DECISION,
        step_order=5,
    )
    logger.info("[%s] Step 5: roteamento", execution_id)

    try:
        total = 0
        for ModelClass in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
            qs = ModelClass.objects.filter(execution_id=execution_id, status="ready")
            to_update: list = []
            for u in qs.iterator(chunk_size=1000):
                raw = u.raw_data or {}
                raw["route"] = {
                    "keycloak": build_kc_payload(u),
                    "token_ms": build_token_ms_payload(u),
                }
                u.raw_data = raw
                to_update.append(u)
                total += 1
                if len(to_update) >= 500:
                    ModelClass.objects.bulk_update(to_update, ["raw_data"], batch_size=500)
                    to_update = []
            if to_update:
                ModelClass.objects.bulk_update(to_update, ["raw_data"], batch_size=500)

        step.records_in = total
        step.records_out = total
        step.status = ETLStepLog.StepStatus.SUCCESS
        step.finished_at = timezone.now()
        step.save()
        logger.info("[%s] Step 5 concluido: %d registros roteados", execution_id, total)
    except Exception as e:
        logger.exception("[%s] Step 5 FAILED: %s", execution_id, e)
        step.status = ETLStepLog.StepStatus.FAILED
        step.error_detail = str(e)
        step.finished_at = timezone.now()
        step.save()
        raise


@shared_task(bind=True, name="core.tasks.load_keycloak", max_retries=3)
def load_keycloak(self, execution_id: str):
    """Faz upsert em lote dos usuarios em READY para o Keycloak e registra os resultados em UpsertControl."""
    from django.conf import settings

    from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

    from .keycloak_client import get_admin_client, upsert_user_to_keycloak
    from .models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)
    step = ETLStepLog.objects.create(
        execution=execution,
        step_name=ETLStepLog.StepName.LOAD_KEYCLOAK,
        step_order=6,
    )

    if not settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED:
        logger.warning(
            "[%s] Step 6: Load Keycloak desabilitado por política"
            " (ETL_LOAD_KEYCLOAK_BULK_ENABLED=false)", execution_id,
        )
        step.status = ETLStepLog.StepStatus.SKIPPED
        step.finished_at = timezone.now()
        step.metadata = {"reason": "ETL_LOAD_KEYCLOAK_BULK_ENABLED=false"}
        step.save()
        return

    logger.info("[%s] Step 6: Load Keycloak — realm=%s", execution_id, execution.target_realm)

    try:
        admin = get_admin_client(realm=execution.target_realm)

        loaded = 0
        errors = 0
        skipped = 0
        total = 0

        for ModelClass in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
            usuarios = ModelClass.objects.filter(execution_id=execution_id, status="ready")
            total += usuarios.count()
            for usuario in usuarios.iterator(chunk_size=200):
                try:
                    result = upsert_user_to_keycloak(
                        admin, usuario,
                        realm=execution.target_realm,
                        execution=execution,
                    )
                    if result["action"] == "skipped":
                        usuario.status = "skipped"
                        skipped += 1
                    else:
                        usuario.status = "loaded"
                        loaded += 1
                    usuario.save(update_fields=["status"])
                except Exception as e:
                    logger.warning("Load KC error for %s: %s", getattr(usuario, 'rf', None) or usuario.cpf, e)
                    usuario.status = "error"
                    usuario.error_detail = str(e)[:1000]
                    usuario.save(update_fields=["status", "error_detail"])
                    errors += 1

        step.records_in = total
        step.records_out = loaded
        step.records_error = errors
        step.metadata = {"skipped": skipped}
        step.status = (
            ETLStepLog.StepStatus.SUCCESS if errors == 0
            else ETLStepLog.StepStatus.FAILED
        )
        step.finished_at = timezone.now()
        step.save()

        execution.total_loaded = loaded
        execution.total_skipped = (execution.total_skipped or 0) + skipped
        execution.save(update_fields=["total_loaded", "total_skipped", "updated_at"])

        logger.info("[%s] Step 6 concluido: %d loaded, %d skipped, %d errors", execution_id, loaded, skipped, errors)

    except Exception as e:
        logger.exception("[%s] Step 6 FAILED: %s", execution_id, e)
        step.status = ETLStepLog.StepStatus.FAILED
        step.error_detail = str(e)
        step.finished_at = timezone.now()
        step.save()
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, name="core.tasks.load_token_ms", max_retries=3)
def load_token_ms(self, execution_id: str):
    """Envia os usuarios de staging em READY para o microsservico token-ms em lotes configuraveis."""
    from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

    from .keycloak_client import build_token_ms_payload
    from .models import ETLExecution, ETLStepLog
    from .token_ms_client import send_all

    execution = ETLExecution.objects.get(id=execution_id)
    step = ETLStepLog.objects.create(
        execution=execution,
        step_name=ETLStepLog.StepName.LOAD_TOKEN_MS,
        step_order=7,
    )
    logger.info("[%s] Step 7: Load token-ms", execution_id)

    try:
        def _payloads():
            for ModelClass in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
                qs = ModelClass.objects.filter(
                    execution_id=execution_id,
                    status__in=["ready", "loaded"],
                )
                for u in qs.iterator(chunk_size=1000):
                    route = (u.raw_data or {}).get("route") or {}
                    payload = route.get("token_ms") or build_token_ms_payload(u)
                    yield payload

        metrics = send_all(_payloads(), execution_id=execution_id)

        step.records_in = metrics["sent"]
        step.records_out = metrics["sent"]
        step.status = ETLStepLog.StepStatus.SUCCESS
        step.metadata = {"batches": metrics["batches"]}
        step.finished_at = timezone.now()
        step.save()

        logger.info(
            "[%s] Step 7 concluido: %d usuarios enviados em %d lotes",
            execution_id, metrics["sent"], metrics["batches"],
        )

    except Exception as e:
        logger.exception("[%s] Step 7 FAILED: %s", execution_id, e)
        step.status = ETLStepLog.StepStatus.FAILED
        step.error_detail = str(e)[:2000]
        step.finished_at = timezone.now()
        step.save()
        raise self.retry(exc=e, countdown=60)


@shared_task(bind=True, name="core.tasks.audit_etl")
def audit_etl(self, execution_id: str):
    """Etapa 8 — Fecha a execução e agrega métricas finais."""
    from .models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)
    step = ETLStepLog.objects.create(
        execution=execution,
        step_name=ETLStepLog.StepName.AUDIT,
        step_order=8,
    )

    logger.info("[%s] Step 8: Audit", execution_id)

    try:
        steps = execution.steps.all()
        total_errors = sum(s.records_error for s in steps)
        has_failures = steps.filter(status=ETLStepLog.StepStatus.FAILED).exists()

        execution.total_errors = total_errors

        if has_failures:
            execution.mark_finished("partial")
        else:
            execution.mark_finished("success")

        step.status = ETLStepLog.StepStatus.SUCCESS
        step.finished_at = timezone.now()
        step.metadata = {
            "total_steps": steps.count(),
            "failed_steps": steps.filter(status="failed").count(),
            "duration_seconds": execution.duration_seconds,
        }
        step.save()

        logger.info(
            "[%s] Pipeline finalizado — status=%s, extracted=%d, loaded=%d, errors=%d, duration=%.1fs",
            execution_id,
            execution.status,
            execution.total_extracted,
            execution.total_loaded,
            execution.total_errors,
            execution.duration_seconds or 0,
        )

        cleanup_old_staging.apply_async(countdown=30)

    except Exception as e:
        logger.exception("[%s] Step 8 FAILED: %s", execution_id, e)
        step.status = ETLStepLog.StepStatus.FAILED
        step.error_detail = str(e)
        step.finished_at = timezone.now()
        step.save()
        execution.mark_finished("failed")


@shared_task(name="core.tasks.cleanup_old_staging")
def cleanup_old_staging(keep_last: int = 2):
    """Apaga registros de staging de todas as execucoes, exceto as N mais recentes, para liberar espaco no banco."""
    from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

    from .models import ETLExecution

    keep_ids = list(
        ETLExecution.objects.filter(status__in=["success", "partial", "running", "pending"])
        .order_by("-finished_at")
        .values_list("id", flat=True)[:keep_last + 5]
    )

    if not keep_ids:
        return

    total_deleted = 0
    for ModelClass in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
        deleted, _ = ModelClass.objects.exclude(execution_id__in=keep_ids).delete()
        total_deleted += deleted
    if total_deleted:
        logger.info("Cleanup: %d staging records removidos (kept %d executions)", total_deleted, len(keep_ids))
