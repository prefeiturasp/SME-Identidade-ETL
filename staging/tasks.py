"""Tasks Celery para transformacao, deduplicacao e crossref dos dados de staging."""
import gc
import logging
from collections import defaultdict

from celery import shared_task
from django.utils import timezone

from .utils import build_dedup_key, normalize_cpf, normalize_rf, validate_cpf

logger = logging.getLogger(__name__)

SOURCE_PRIORITY = {
    "se1426": 1,
    "eol_db": 2,
    "coresso": 3,
}

MERGEABLE_FIELDS = [
    "email", "data_nascimento", "cargo", "funcao", "situacao",
    "lotacao", "lotacao_nome", "dre", "ue",
]


def _normalize_cpf_field(record) -> None:
    """Normaliza e valida o CPF do record em-place."""
    if not record.cpf:
        return
    cpf_clean = normalize_cpf(record.cpf)
    if cpf_clean and validate_cpf(cpf_clean):
        record.cpf = cpf_clean
    else:
        record.error_detail = f"CPF inválido: {record.cpf}"
        record.cpf = cpf_clean


def _apply_lotacao_to_record(record, lotacao_map) -> None:
    """Preenche dre/ue a partir do mapa de lotação, se disponível."""
    if not (hasattr(record, "lotacao") and record.lotacao and not record.dre):
        return
    lot = lotacao_map.get(record.lotacao)
    if lot:
        record.dre = lot["dre_codigo"]
        record.ue = lot["codigo"] if lot["tipo"] == "ue" else None


def _flush_transform_buffers(model_class, buf_ok, buf_err, update_fields, bulk_size):
    """Persiste e limpa os buffers de transformação, retornando (ok_count, err_count)."""
    ok_count = err_count = 0
    if buf_ok:
        model_class.objects.bulk_update(buf_ok, update_fields, batch_size=bulk_size)
        ok_count = len(buf_ok)
        buf_ok.clear()
    if buf_err:
        model_class.objects.bulk_update(buf_err, ["status", "error_detail"], batch_size=bulk_size)
        err_count = len(buf_err)
        buf_err.clear()
    return ok_count, err_count


def _transform_model(model_class, execution_id, lotacao_map, bulk_size, extra_fields=None):
    base_fields = ["cpf", "nome", "status", "transformed_at", "error_detail"]
    _extra = extra_fields or {}
    rf_field = _extra.get("rf_field", False)
    lotacao_field = _extra.get("lotacao_field", False)
    extra_update = []
    if rf_field:
        extra_update.append("rf")
    if lotacao_field:
        extra_update.extend(["dre", "ue"])
    update_fields = base_fields + extra_update

    now = timezone.now()
    transformed = 0
    errors = 0
    buf_ok: list = []
    buf_err: list = []
    processed = 0

    # raw_data (JSONField) não é usado na transformação — defer evita carregar
    # centenas de KB por registro em memória, reduzindo drasticamente o consumo.
    for record in (
        model_class.objects
        .filter(execution_id=execution_id, status=model_class.Status.RAW)
        .defer("raw_data")
        .iterator(chunk_size=bulk_size)
    ):
        try:
            _normalize_cpf_field(record)

            if hasattr(record, "rf") and record.rf:
                record.rf = normalize_rf(record.rf)

            if record.nome:
                record.nome = " ".join(record.nome.split()).title()

            _apply_lotacao_to_record(record, lotacao_map)

            record.status = model_class.Status.TRANSFORMED
            record.transformed_at = now
            buf_ok.append(record)

        except Exception as e:
            record.status = model_class.Status.ERROR
            record.error_detail = f"Transform error: {e}"
            buf_err.append(record)

        if len(buf_ok) + len(buf_err) >= bulk_size:
            ok, err = _flush_transform_buffers(model_class, buf_ok, buf_err, update_fields, bulk_size)
            transformed += ok
            errors += err

        processed += 1
        if processed % 50_000 == 0:
            gc.collect()
            logger.info(
                "[transform] %s: %d processados, %d ok / %d erros",
                model_class.__name__, processed, transformed, errors,
            )

    ok, err = _flush_transform_buffers(model_class, buf_ok, buf_err, update_fields, bulk_size)
    transformed += ok
    errors += err
    gc.collect()
    return transformed, errors


@shared_task(
    bind=True,
    name="staging.tasks.transform_staging",
    soft_time_limit=10800,  # 3h → SoftTimeLimitExceeded
    time_limit=11100,       # 3h5m → SIGTERM forçado
)
def transform_staging(self, execution_id: str):
    """Normalize and validate all RAW staging records, marking them TRANSFORMED or ERROR."""
    from celery.exceptions import SoftTimeLimitExceeded
    from core.models import ETLExecution, ETLStepLog
    from core.tasks import ExecutionCancelledError, _check_cancelled
    from .models import StagingLotacao, StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

    _check_cancelled(execution_id)

    execution = ETLExecution.objects.get(id=execution_id)
    step = ETLStepLog.objects.create(
        execution=execution,
        step_name=ETLStepLog.StepName.STAGING,
        step_order=3,
    )

    total_raw = (
        StagingUsuarioServidor.objects.filter(execution_id=execution_id, status="raw").count()
        + StagingUsuarioAluno.objects.filter(execution_id=execution_id, status="raw").count()
        + StagingUsuarioTerceiro.objects.filter(execution_id=execution_id, status="raw").count()
    )
    logger.info(
        "[%s] Step 3: %d registros pendentes de transformação (servidores + alunos + terceiros)",
        execution_id, total_raw,
    )
    logger.info("[%s] Step 3: Transform staging (servidor/aluno/terceiro)", execution_id)

    try:
        lotacao_map = {
            lot["codigo"]: lot
            for lot in StagingLotacao.objects.values("codigo", "dre_codigo", "tipo")
        }

        BULK_SIZE = 500
        total_transformed = 0
        total_errors = 0

        user_types = execution.user_types or "all"
        type_parts = {p.strip() for p in user_types.split(",") if p.strip()}
        include_all = "all" in type_parts

        models_to_transform = []
        if include_all or "servidor" in type_parts:
            models_to_transform.append((StagingUsuarioServidor, {"rf_field": True, "lotacao_field": True}))
        if include_all or "aluno" in type_parts:
            models_to_transform.append((StagingUsuarioAluno, {"rf_field": False, "lotacao_field": True}))
        if include_all or "terceiro" in type_parts:
            models_to_transform.append((StagingUsuarioTerceiro, {"rf_field": False, "lotacao_field": False}))

        for model_class, extra in models_to_transform:
            t, e = _transform_model(model_class, execution_id, lotacao_map, BULK_SIZE, extra)
            total_transformed += t
            total_errors += e

        step.records_in = total_transformed + total_errors
        step.records_out = total_transformed
        step.records_error = total_errors
        step.status = "success" if total_errors == 0 else "failed"
        step.finished_at = timezone.now()
        step.save()

        execution.total_transformed = total_transformed
        execution.save(update_fields=["total_transformed", "updated_at"])

        logger.info(
            "[%s] Step 3 concluido: %d transformed, %d errors",
            execution_id, total_transformed, total_errors,
        )

    except SoftTimeLimitExceeded:
        logger.error("[%s] Step 3 TIMEOUT: transform excedeu tempo limite", execution_id)
        step.status = "failed"
        step.error_detail = "SoftTimeLimitExceeded — transform demorou mais de 3h"
        step.finished_at = timezone.now()
        step.save()
        raise

    except Exception as e:
        logger.exception("[%s] Step 3 FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)
        step.finished_at = timezone.now()
        step.save()
        raise


def _union_find(x, parent: dict):
    """Path-compressed find para union-find."""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union_merge(a, b, parent: dict) -> None:
    """Union para union-find."""
    ra, rb = _union_find(a, parent), _union_find(b, parent)
    if ra != rb:
        parent[ra] = rb


def _build_cpf_rf_indexes(transformed, total: int, total_chunks: int, chunk_size: int, execution_id: str):
    """Carrega registros em batches e constrói índices CPF/RF para union-find."""
    cpf_index = defaultdict(list)
    rf_index = defaultdict(list)
    records_by_id = {}
    processed_records = 0

    for chunk_num in range(total_chunks):
        offset = chunk_num * chunk_size
        chunk = transformed.only(
            "id", "rf", "cpf", "source", "extracted_at",
            "email", "data_nascimento", "cargo", "funcao", "situacao",
            "lotacao", "lotacao_nome", "dre", "ue", "nome",
        )[offset:offset + chunk_size]

        chunk_count = 0
        for record in chunk:
            records_by_id[record.id] = record
            if record.cpf and validate_cpf(record.cpf):
                cpf_index[record.cpf].append(record.id)
            if record.rf:
                rf_index[record.rf].append(record.id)
            chunk_count += 1

        processed_records += chunk_count
        logger.info(
            "[%s] Batch %d/%d — processados %d/%d registros (%.1f%%) — CPFs: %d | RFs: %d",
            execution_id, chunk_num + 1, total_chunks,
            processed_records, total, (processed_records / total * 100),
            len(cpf_index), len(rf_index),
        )
        gc.collect()

    return cpf_index, rf_index, records_by_id


def _apply_cross_unions(records_by_id, cpf_index, rf_index, parent):
    """Aplica union entre registros que compartilham CPF e RF, retorna contagem."""
    cross_unions = 0
    for rec in records_by_id.values():
        cpf = rec.cpf if rec.cpf and validate_cpf(rec.cpf) else None
        rf = rec.rf
        if cpf and rf:
            cpf_ids = cpf_index.get(cpf, [])
            rf_ids = rf_index.get(rf, [])
            if cpf_ids and rf_ids:
                for rid in rf_ids:
                    _union_merge(cpf_ids[0], rid, parent)
                    cross_unions += 1
    return cross_unions


def _build_clusters_by_union_find(cpf_index, rf_index, records_by_id, execution_id):
    """Aplica union-find nos índices CPF/RF e retorna dict de clusters."""
    parent = {rid: rid for rid in records_by_id}

    cpf_unions = 0
    for cpf, ids in cpf_index.items():
        for i in range(1, len(ids)):
            _union_merge(ids[0], ids[i], parent)
            cpf_unions += 1

    rf_unions = 0
    for rf, ids in rf_index.items():
        for i in range(1, len(ids)):
            _union_merge(ids[0], ids[i], parent)
            rf_unions += 1

    cross_unions = _apply_cross_unions(records_by_id, cpf_index, rf_index, parent)

    clusters = defaultdict(list)
    for rid in records_by_id:
        clusters[_union_find(rid, parent)].append(rid)

    logger.info(
        "[%s] Índices: CPF_unions=%d RF_unions=%d cross_unions=%d → %d clusters",
        execution_id, cpf_unions, rf_unions, cross_unions, len(clusters),
    )
    return clusters


def _flush_dedup_buffers(winners_ready, losers_skipped, errors_no_key, staging_class, bulk_size) -> None:
    """Persiste buffers de dedup no banco e limpa memória."""
    if winners_ready:
        staging_class.objects.bulk_update(winners_ready, ["status"], batch_size=bulk_size)
        winners_ready.clear()
    if losers_skipped:
        staging_class.objects.bulk_update(
            losers_skipped, ["status", "error_detail"], batch_size=bulk_size,
        )
        losers_skipped.clear()
    if errors_no_key:
        staging_class.objects.bulk_update(
            errors_no_key, ["status", "error_detail"], batch_size=bulk_size,
        )
        errors_no_key.clear()
    gc.collect()


def _process_dedup_cluster(member_ids, records_by_id, staging_class):
    """Processa um cluster de dedup, retornando (winner, losers, error_record).

    Retorna (winner, losers, None) em caso normal, ou (None, [], error_record)
    se o winner não tiver chave de dedup válida.
    """
    members = [records_by_id[mid] for mid in member_ids]
    members.sort(
        key=lambda r: (
            SOURCE_PRIORITY.get(r.source, 99),
            r.extracted_at or timezone.now(),
        )
    )
    winner = members[0]
    dedup_key = build_dedup_key(winner.cpf, winner.rf)
    if not dedup_key:
        winner.status = staging_class.Status.ERROR
        winner.error_detail = "Sem CPF válido nem RF para dedup"
        return None, [], winner

    losers = []
    for loser in members[1:]:
        loser.status = staging_class.Status.SKIPPED
        loser.error_detail = f"Dedup simplificado: skipped (winner={str(winner.id)[:8]})"
        losers.append(loser)

    winner.status = staging_class.Status.READY
    return winner, losers, None


def _dedup_step_status(errors: int, ready: int) -> str:
    """Retorna 'failed' apenas quando há erros e nenhum registro foi marcado como ready."""
    if errors > 0 and ready == 0:
        return "failed"
    return "success"


@shared_task(bind=True, name="staging.tasks.crossref_dedup")
def crossref_dedup(self, execution_id: str):
    """Faz cross-reference dos usuarios de staging entre as fontes e deduplica por CPF/RF."""
    from core.models import ETLExecution, ETLStepLog
    from .models import (
        DedupResult, StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro,
    )

    execution = ETLExecution.objects.get(id=execution_id)
    step = ETLStepLog.objects.create(
        execution=execution,
        step_name=ETLStepLog.StepName.CROSSREF_DEDUP,
        step_order=4,
    )

    # Verifica cancelamento antes de iniciar
    from core.tasks import ExecutionCancelledError, _check_cancelled
    _check_cancelled(execution_id)

    logger.info("[%s] Step 4: Crossref/Dedup SIMPLIFICADO - SOMENTE servidores (alunos/terceiros ignorados)", execution_id)

    try:
        # ALUNOS E TERCEIROS NÃO SÃO PROCESSADOS NESTA VERSÃO
        # Serão processados em execução futura com deduplicação completa
        n_alunos = 0
        n_terceiros = 0
        logger.info(
            "[%s] Alunos: IGNORADOS | Terceiros: IGNORADOS (processamento futuro)",
            execution_id,
        )

        base_qs = StagingUsuarioServidor.objects.filter(
            execution_id=execution_id,
            status=StagingUsuarioServidor.Status.TRANSFORMED,
        ).order_by("id")

        # Aplica limite por max_records_extract (da execução) se configurado
        limit = execution.max_records_extract
        if limit:
            total = min(limit, base_qs.count())
            logger.info("[%s] Step 4: limitado a %d registros (max_records_extract=%d)", execution_id, total, limit)
            transformed = base_qs[:total]
        else:
            total = base_qs.count()
            transformed = base_qs

        if total == 0:
            logger.warning("[%s] Step 4: no transformed servidores to dedup", execution_id)
            step.records_in = n_alunos + n_terceiros
            step.records_out = n_alunos + n_terceiros
            step.status = "success"
            step.finished_at = timezone.now()
            step.save()
            return

        CHUNK_SIZE = 50_000
        total_chunks = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
        logger.info(
            "[%s] Iniciando crossref/dedup de %d servidores em %d batches de até %d registros",
            execution_id, total, total_chunks, CHUNK_SIZE,
        )

        cpf_index, rf_index, records_by_id = _build_cpf_rf_indexes(
            transformed, total, total_chunks, CHUNK_SIZE, execution_id,
        )
        clusters = _build_clusters_by_union_find(cpf_index, rf_index, records_by_id, execution_id)

        FLUSH_INTERVAL = 2_000
        BULK_SIZE = 500
        LOG_INTERVAL = max(10000, len(clusters) // 10)

        ready = skipped = errors = 0
        winners_ready: list = []
        losers_skipped: list = []
        errors_no_key: list = []
        processed_clusters = 0
        total_clusters = len(clusters)

        for cluster_root, member_ids in clusters.items():
            try:
                winner, losers, error_rec = _process_dedup_cluster(
                    member_ids, records_by_id, StagingUsuarioServidor,
                )
                if error_rec:
                    errors_no_key.append(error_rec)
                    errors += 1
                else:
                    winners_ready.append(winner)
                    losers_skipped.extend(losers)
                    ready += 1
                    skipped += len(losers)

                processed_clusters += 1

                if processed_clusters % FLUSH_INTERVAL == 0:
                    logger.info(
                        "[%s] Flush intermediário aos %d clusters — salvando buffers no banco...",
                        execution_id, processed_clusters,
                    )
                    _flush_dedup_buffers(
                        winners_ready, losers_skipped, errors_no_key, StagingUsuarioServidor, BULK_SIZE,
                    )

                if processed_clusters % LOG_INTERVAL == 0:
                    logger.info(
                        "[%s] Dedup progresso: %d/%d clusters (%.1f%%) — ready=%d skip=%d err=%d",
                        execution_id, processed_clusters, total_clusters,
                        (processed_clusters / total_clusters * 100),
                        ready, skipped, errors,
                    )

            except Exception as e:
                logger.warning("[%s] Dedup error for cluster %s: %s", execution_id, cluster_root, e)
                errors += 1
                processed_clusters += 1

        logger.info("[%s] Dedup — flush final dos buffers...", execution_id)
        _flush_dedup_buffers(
            winners_ready, losers_skipped, errors_no_key, StagingUsuarioServidor, BULK_SIZE,
        )
        logger.info(
            "[%s] Dedup SIMPLIFICADA persistida — %d clusters | ready=%d skip=%d err=%d (SEM merge)",
            execution_id, processed_clusters, ready, skipped, errors,
        )

        total_ready = ready + n_alunos + n_terceiros
        step.records_in = total + n_alunos + n_terceiros
        step.records_out = total_ready
        step.records_error = errors
        step.status = _dedup_step_status(errors, ready)
        step.finished_at = timezone.now()
        step.metadata = {
            "servidores_clusters": len(clusters),
            "servidores_ready": ready,
            "servidores_skipped": skipped,
            "alunos_ready": n_alunos,
            "terceiros_ready": n_terceiros,
            "modo": "simplificado_sem_merge",
        }
        step.save()

        execution.total_skipped = skipped
        execution.save(update_fields=["total_skipped", "updated_at"])

        logger.info(
            "[%s] Step 4 concluído: srv=%d ready/%d skip | aluno=%d | terc=%d",
            execution_id, ready, skipped, n_alunos, n_terceiros,
        )

    except Exception as e:
        logger.exception("[%s] Step 4 FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)
        step.finished_at = timezone.now()
        step.save()
        raise


def _determine_match_type(winner, loser) -> str:
    from .models import DedupResult

    w_cpf = winner.cpf if winner.cpf and validate_cpf(winner.cpf) else None
    l_cpf = loser.cpf if loser.cpf and validate_cpf(loser.cpf) else None
    w_rf = winner.rf
    l_rf = loser.rf

    if w_cpf and l_cpf and w_cpf == l_cpf:
        return str(DedupResult.MatchType.CPF_EXACT)  # cpf e rf batem

    if w_rf and l_rf and w_rf == l_rf:
        return str(DedupResult.MatchType.RF_EXACT)

    return str(DedupResult.MatchType.CPF_RF_CROSS)


def _check_conflicts(winner, loser) -> bool:
    """Verifica se há campos com valores conflitantes (ambos preenchidos, diferentes)."""
    conflict_fields = ["nome", "cargo", "situacao"]
    for field in conflict_fields:
        w_val = getattr(winner, field, None)
        l_val = getattr(loser, field, None)
        if w_val and l_val and w_val.lower().strip() != l_val.lower().strip():
            return True
    return False
