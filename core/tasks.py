"""Tasks Celery para orquestrar o pipeline completo do ETL, da extracao a carga no Keycloak."""
import logging

from celery import chain, chord, shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _step_done(execution_id: str, step_name: str) -> bool:
    """Retorna True se o step já foi concluído com sucesso para esta execução."""
    from .models import ETLStepLog
    return ETLStepLog.objects.filter(
        execution_id=execution_id,
        step_name=step_name,
        status=ETLStepLog.StepStatus.SUCCESS,
    ).exists()


def _get_or_create_step(execution, step_name: str, step_order: int):
    """Obtém ou cria um ETLStepLog, resetando para RUNNING se já existia com falha."""
    from .models import ETLStepLog
    step, created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name=step_name,
        defaults={"step_order": step_order},
    )
    if not created:
        step.status = ETLStepLog.StepStatus.RUNNING
        step.finished_at = None
        step.error_detail = None
        step.save(update_fields=["status", "finished_at", "error_detail"])
    return step


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


def _sync_coresso_catalogo(execution_id: str, realm: str) -> None:
    """Step 0 — sincroniza Sistemas e Perfis do CoreSSO como Clients/Client Roles no Keycloak."""
    from django.conf import settings

    from extract.tasks import extract_coresso_perfis, extract_coresso_sistemas
    from staging.models import StagingPerfilCoreSSO, StagingSistema

    from .keycloak_client import get_admin_client, upsert_kc_client, upsert_kc_client_role
    from .models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)
    step = _get_or_create_step(execution, ETLStepLog.StepName.SYNC_CATALOGO, 0)

    if not getattr(settings, "CORESSO_DB_SERVER", None):
        logger.info("[%s] Step 0: CORESSO_DB_SERVER ausente — pulando sync catálogo", execution_id)
        step.status = ETLStepLog.StepStatus.SKIPPED
        step.metadata = {"reason": "CORESSO_DB_SERVER não configurado"}
        step.finished_at = timezone.now()
        step.save()
        return

    logger.info("[%s] Step 0: Sync catálogo (sistemas + perfis) → realm=%s", execution_id, realm)
    try:
        # 1. Extrai sistemas CoreSSO → staging_sistema
        total_sistemas = extract_coresso_sistemas(execution_id=execution_id)

        # 2. Faz upsert de cada sistema como Client OIDC no Keycloak
        admin = get_admin_client(realm=realm)
        loaded_sistemas = errors_sistemas = 0
        for sistema in StagingSistema.objects.filter(situacao=1):
            try:
                upsert_kc_client(admin, sistema, realm=realm)
                loaded_sistemas += 1
            except Exception as e:
                logger.warning("[%s] Falha upsert client %s: %s", execution_id, sistema.sigla, e)
                errors_sistemas += 1

        # 3. Extrai perfis CoreSSO → staging_perfil_coresso
        total_perfis = extract_coresso_perfis(execution_id=execution_id)

        # 4. Faz upsert de cada perfil como Client Role no Keycloak
        loaded_perfis = errors_perfis = 0
        for perfil in StagingPerfilCoreSSO.objects.select_related("sistema").iterator(chunk_size=200):
            try:
                upsert_kc_client_role(admin, perfil)
                loaded_perfis += 1
            except Exception as e:
                logger.warning("[%s] Falha upsert client role %s: %s", execution_id, perfil.kc_role_name, e)
                errors_perfis += 1

        step.records_in = total_sistemas + total_perfis
        step.records_out = loaded_sistemas + loaded_perfis
        step.records_error = errors_sistemas + errors_perfis
        step.status = ETLStepLog.StepStatus.SUCCESS
        step.metadata = {
            "sistemas_extracted": total_sistemas,
            "sistemas_loaded_kc": loaded_sistemas,
            "sistemas_errors": errors_sistemas,
            "perfis_extracted": total_perfis,
            "perfis_loaded_kc": loaded_perfis,
            "perfis_errors": errors_perfis,
        }
        step.finished_at = timezone.now()
        step.save()
        logger.info(
            "[%s] Step 0 concluído: %d sistemas, %d perfis carregados no KC",
            execution_id, loaded_sistemas, loaded_perfis,
        )
    except Exception as e:
        logger.exception("[%s] Step 0 FAILED: %s", execution_id, e)
        step.status = ETLStepLog.StepStatus.FAILED
        step.error_detail = str(e)[:2000]
        step.finished_at = timezone.now()
        step.save()
        # Não aborta o pipeline — usuários ainda podem ser carregados sem client roles
        logger.warning("[%s] Continuando pipeline mesmo com falha no Step 0", execution_id)


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

        # Step 0: sincroniza catálogo de sistemas e perfis antes de extrair usuários
        _sync_coresso_catalogo(execution_id=execution_id, realm=execution.target_realm)

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

    # Idempotência: pula se já concluiu com sucesso
    if _step_done(execution_id, ETLStepLog.StepName.DECISION):
        logger.info("[%s] Step 5: já concluído — pulando", execution_id)
        return

    step = _get_or_create_step(execution, ETLStepLog.StepName.DECISION, 5)
    logger.info("[%s] Step 5: roteamento", execution_id)

    try:
        total = 0
        for model_class in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
            qs = model_class.objects.filter(execution_id=execution_id, status="ready")
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
                    model_class.objects.bulk_update(to_update, ["raw_data"], batch_size=500)
                    to_update = []
            if to_update:
                model_class.objects.bulk_update(to_update, ["raw_data"], batch_size=500)

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


def _try_assign_roles(admin, kc_user_id: str, usuario) -> None:
    """Tenta atribuir client roles ao usuario no Keycloak; loga aviso em caso de falha."""
    from .keycloak_client import assign_user_client_roles
    login = (
        (getattr(usuario, "rf", None) or "").strip()
        or "".join(c for c in (usuario.cpf or "") if c.isdigit())
    )
    try:
        assign_user_client_roles(admin, kc_user_id, login)
    except Exception as role_exc:
        logger.warning(
            "assign_user_client_roles falhou para %s (%s): %s",
            login, kc_user_id, role_exc,
        )


def _upsert_single_usuario(admin, usuario, realm: str, execution) -> tuple[int, int, int]:
    """Faz upsert de um único usuario no Keycloak e atualiza seu status.

    Retorna (loaded, skipped, errors).
    """
    from .keycloak_client import upsert_user_to_keycloak
    try:
        result = upsert_user_to_keycloak(admin, usuario, realm=realm, execution=execution)
        if result["action"] == "skipped":
            usuario.status = "skipped"
            usuario.save(update_fields=["status"])
            return 0, 1, 0
        usuario.status = "loaded"
        usuario.save(update_fields=["status"])
        kc_user_id = result.get("kc_user_id")
        if kc_user_id:
            _try_assign_roles(admin, kc_user_id, usuario)
        return 1, 0, 0
    except Exception as e:
        logger.warning("Load KC error for %s: %s", getattr(usuario, "rf", None) or usuario.cpf, e)
        usuario.status = "error"
        usuario.error_detail = str(e)[:1000]
        usuario.save(update_fields=["status", "error_detail"])
        return 0, 0, 1


@shared_task(bind=True, name="core.tasks.load_keycloak", max_retries=3)
def load_keycloak(self, execution_id: str):
    """Faz upsert em lote dos usuarios em READY para o Keycloak e registra os resultados em UpsertControl."""
    from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

    from .keycloak_client import get_admin_client
    from .models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)

    # Idempotência: pula se já concluiu com sucesso
    if _step_done(execution_id, ETLStepLog.StepName.LOAD_KEYCLOAK):
        logger.info("[%s] Step 6: já concluído — pulando", execution_id)
        return

    step = _get_or_create_step(execution, ETLStepLog.StepName.LOAD_KEYCLOAK, 6)

    if not execution.load_keycloak:
        logger.info(
            "[%s] Step 6: Load Keycloak desabilitado (load_keycloak=False)", execution_id,
        )
        step.status = ETLStepLog.StepStatus.SKIPPED
        step.finished_at = timezone.now()
        step.metadata = {"reason": "load_keycloak=False"}
        step.save()
        return

    logger.info("[%s] Step 6: Load Keycloak — realm=%s", execution_id, execution.target_realm)

    try:
        admin = get_admin_client(realm=execution.target_realm)

        loaded = errors = skipped = total = 0

        for model_class in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
            usuarios = model_class.objects.filter(execution_id=execution_id, status="ready")
            total += usuarios.count()
            for usuario in usuarios.iterator(chunk_size=200):
                l, s, e = _upsert_single_usuario(admin, usuario, execution.target_realm, execution)
                loaded += l
                skipped += s
                errors += e

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

    # Idempotência: pula se já concluiu com sucesso
    if _step_done(execution_id, ETLStepLog.StepName.LOAD_TOKEN_MS):
        logger.info("[%s] Step 7: já concluído — pulando", execution_id)
        return

    step = _get_or_create_step(execution, ETLStepLog.StepName.LOAD_TOKEN_MS, 7)

    if not execution.load_token_ms:
        logger.info("[%s] Step 7: Load Token-MS desabilitado (load_token_ms=False)", execution_id)
        step.status = ETLStepLog.StepStatus.SKIPPED
        step.finished_at = timezone.now()
        step.metadata = {"reason": "load_token_ms=False"}
        step.save()
        return

    logger.info("[%s] Step 7: Load token-ms", execution_id)

    try:
        def _payloads():
            for model_class in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
                qs = model_class.objects.filter(
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

    step = None
    try:
        # Idempotência
        if _step_done(execution_id, ETLStepLog.StepName.AUDIT):
            logger.info("[%s] Step 8: já concluído — pulando", execution_id)
            return

        step = _get_or_create_step(execution, ETLStepLog.StepName.AUDIT, 8)

        logger.info("[%s] Step 8: Audit", execution_id)

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
        if step is None:
            step = _get_or_create_step(execution, ETLStepLog.StepName.AUDIT, 8)
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
    for model_class in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
        deleted, _ = model_class.objects.exclude(execution_id__in=keep_ids).delete()
        total_deleted += deleted
    if total_deleted:
        logger.info("Cleanup: %d staging records removidos (kept %d executions)", total_deleted, len(keep_ids))