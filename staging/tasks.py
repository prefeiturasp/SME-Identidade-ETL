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


def _transform_model(ModelClass, execution_id, lotacao_map, BULK_SIZE, extra_fields=None):
    base_fields = ["cpf", "nome", "status", "transformed_at", "error_detail"]
    rf_field = extra_fields.get("rf_field", False) if extra_fields else False
    lotacao_field = extra_fields.get("lotacao_field", False) if extra_fields else False
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

    def _flush():
        nonlocal transformed, errors
        if buf_ok:
            ModelClass.objects.bulk_update(buf_ok, update_fields, batch_size=BULK_SIZE)
            transformed += len(buf_ok)
            buf_ok.clear()
        if buf_err:
            ModelClass.objects.bulk_update(buf_err, ["status", "error_detail"], batch_size=BULK_SIZE)
            errors += len(buf_err)
            buf_err.clear()

    # raw_data (JSONField) não é usado na transformação — defer evita carregar
    # centenas de KB por registro em memória, reduzindo drasticamente o consumo.
    for record in (
        ModelClass.objects
        .filter(execution_id=execution_id, status=ModelClass.Status.RAW)
        .defer("raw_data")
        .iterator(chunk_size=BULK_SIZE)
    ):
        try:
            if record.cpf:
                cpf_clean = normalize_cpf(record.cpf)
                if cpf_clean and validate_cpf(cpf_clean):
                    record.cpf = cpf_clean
                else:
                    record.error_detail = f"CPF inválido: {record.cpf}"
                    record.cpf = cpf_clean

            if hasattr(record, "rf") and record.rf:
                record.rf = normalize_rf(record.rf)

            if record.nome:
                record.nome = " ".join(record.nome.split()).title()

            if hasattr(record, "lotacao") and record.lotacao and not record.dre:
                lot = lotacao_map.get(record.lotacao)
                if lot:
                    record.dre = lot["dre_codigo"]
                    record.ue = lot["codigo"] if lot["tipo"] == "ue" else None

            record.status = ModelClass.Status.TRANSFORMED
            record.transformed_at = now
            buf_ok.append(record)

        except Exception as e:
            record.status = ModelClass.Status.ERROR
            record.error_detail = f"Transform error: {e}"
            buf_err.append(record)

        if len(buf_ok) + len(buf_err) >= BULK_SIZE:
            _flush()

        processed += 1
        if processed % 50_000 == 0:
            gc.collect()
            logger.info(
                "[transform] %s: %d processados, %d ok / %d erros",
                ModelClass.__name__, processed, transformed, errors,
            )

    _flush()
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
    from .models import StagingLotacao, StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

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

        for ModelClass, extra in [
            (StagingUsuarioServidor, {"rf_field": True, "lotacao_field": True}),
            (StagingUsuarioAluno,    {"rf_field": False, "lotacao_field": True}),
            (StagingUsuarioTerceiro, {"rf_field": False, "lotacao_field": False}),
        ]:
            t, e = _transform_model(ModelClass, execution_id, lotacao_map, BULK_SIZE, extra)
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

    logger.info("[%s] Step 4: Crossref/Dedup (servidor) + mark ready (aluno/terceiro)", execution_id)

    try:

        alunos_ready = StagingUsuarioAluno.objects.filter(
            execution_id=execution_id,
            status=StagingUsuarioAluno.Status.TRANSFORMED,
        )
        n_alunos = alunos_ready.update(status=StagingUsuarioAluno.Status.READY)

        terceiros_ready = StagingUsuarioTerceiro.objects.filter(
            execution_id=execution_id,
            status=StagingUsuarioTerceiro.Status.TRANSFORMED,
        )
        n_terceiros = terceiros_ready.update(status=StagingUsuarioTerceiro.Status.READY)

        logger.info(
            "[%s] Alunos prontos: %d | Terceiros prontos: %d",
            execution_id, n_alunos, n_terceiros,
        )

        transformed = StagingUsuarioServidor.objects.filter(
            execution_id=execution_id,
            status=StagingUsuarioServidor.Status.TRANSFORMED,
        )

        total = transformed.count()
        if total == 0:
            logger.warning("[%s] Step 4: no transformed servidores to dedup", execution_id)
            step.records_in = n_alunos + n_terceiros
            step.records_out = n_alunos + n_terceiros
            step.status = "success"
            step.finished_at = timezone.now()
            step.save()
            return

        cpf_index = defaultdict(list)
        rf_index = defaultdict(list)
        records_by_id = {}

        for record in transformed.only(
            "id", "rf", "cpf", "source", "extracted_at",
            "email", "data_nascimento", "cargo", "funcao", "situacao",
            "lotacao", "lotacao_nome", "dre", "ue", "nome",
        ).iterator(chunk_size=1000):
            records_by_id[record.id] = record
            cpf = record.cpf
            rf = record.rf
            if cpf and validate_cpf(cpf):
                cpf_index[cpf].append(record.id)
            if rf:
                rf_index[rf].append(record.id)

        parent = {rid: rid for rid in records_by_id}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for cpf, ids in cpf_index.items():
            for i in range(1, len(ids)):
                union(ids[0], ids[i])

        for rf, ids in rf_index.items():
            for i in range(1, len(ids)):
                union(ids[0], ids[i])

        cpf_to_rf = {}
        rf_to_cpf = {}
        for rid, rec in records_by_id.items():
            cpf = rec.cpf if rec.cpf and validate_cpf(rec.cpf) else None
            rf = rec.rf
            if cpf and rf:
                cpf_to_rf[cpf] = rf
                rf_to_cpf[rf] = cpf

        for cpf, rf in cpf_to_rf.items():
            cpf_ids = cpf_index.get(cpf, [])
            rf_ids = rf_index.get(rf, [])
            if cpf_ids and rf_ids:
                for rid in rf_ids:
                    union(cpf_ids[0], rid)

        clusters = defaultdict(list)
        for rid in records_by_id:
            clusters[find(rid)].append(rid)

        ready = 0
        skipped = 0
        merged = 0
        conflicts = 0
        errors = 0
        dedup_results = []
        winners_ready: list = []
        losers_skipped: list = []
        errors_no_key: list = []

        for cluster_root, member_ids in clusters.items():
            try:
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
                    winner.status = StagingUsuarioServidor.Status.ERROR
                    winner.error_detail = "Sem CPF válido nem RF para dedup"
                    errors_no_key.append(winner)
                    errors += 1
                    continue

                if len(members) == 1:
                    winner.status = StagingUsuarioServidor.Status.READY
                    winners_ready.append(winner)
                    ready += 1
                    continue

                losers = members[1:]
                winner_updated_fields = []

                for loser in losers:
                    merged_fields_list = []
                    for field in MERGEABLE_FIELDS:
                        winner_val = getattr(winner, field, None)
                        loser_val = getattr(loser, field, None)
                        if not winner_val and loser_val:
                            setattr(winner, field, loser_val)
                            merged_fields_list.append(field)
                            if field not in winner_updated_fields:
                                winner_updated_fields.append(field)

                    match_type = _determine_match_type(winner, loser)
                    has_conflict = _check_conflicts(winner, loser)
                    decision = (
                        DedupResult.Decision.CONFLICT if has_conflict
                        else DedupResult.Decision.MERGE if merged_fields_list
                        else DedupResult.Decision.SKIP_DUPLICATE
                    )
                    if has_conflict:
                        conflicts += 1

                    dedup_results.append(
                        DedupResult(
                            dedup_key=dedup_key,
                            winner=None,
                            loser=None,
                            match_type=match_type,
                            decision=decision,
                            merged_fields=merged_fields_list + [
                                f"_winner_id:{winner.id}", f"_loser_id:{loser.id}",
                            ],
                            cpf=winner.cpf,
                            rf=winner.rf,
                            execution_id=execution_id,
                            confidence=1.0 if match_type != DedupResult.MatchType.CPF_RF_CROSS else 0.9,
                        )
                    )

                    loser.status = StagingUsuarioServidor.Status.SKIPPED
                    loser.error_detail = f"Dedup: merged into {winner.id} ({match_type})"
                    losers_skipped.append(loser)
                    skipped += 1

                coresso_members = [m for m in members if m.source == "coresso"]
                if coresso_members:
                    winner.situacao = coresso_members[0].situacao
                    if "situacao" not in winner_updated_fields:
                        winner_updated_fields.append("situacao")

                winner.status = StagingUsuarioServidor.Status.READY
                winners_ready.append(winner)
                ready += 1
                if winner_updated_fields:
                    merged += 1

            except Exception as e:
                logger.warning("[%s] Dedup error for cluster %s: %s", execution_id, cluster_root, e)
                errors += 1

        BULK = 500
        winner_fields = ["status", "email", "data_nascimento", "cargo", "funcao",
                         "situacao", "lotacao", "lotacao_nome", "dre", "ue"]
        if winners_ready:
            StagingUsuarioServidor.objects.bulk_update(winners_ready, winner_fields, batch_size=BULK)
        if losers_skipped:
            StagingUsuarioServidor.objects.bulk_update(
                losers_skipped, ["status", "error_detail"], batch_size=BULK,
            )
        if errors_no_key:
            StagingUsuarioServidor.objects.bulk_update(
                errors_no_key, ["status", "error_detail"], batch_size=BULK,
            )
        if dedup_results:
            DedupResult.objects.bulk_create(dedup_results, batch_size=500)

        total_ready = ready + n_alunos + n_terceiros
        step.records_in = total + n_alunos + n_terceiros
        step.records_out = total_ready
        step.records_error = errors
        step.status = "success" if errors == 0 else ("failed" if ready == 0 else "success")
        step.finished_at = timezone.now()
        step.metadata = {
            "servidores_clusters": len(clusters),
            "servidores_ready": ready,
            "servidores_skipped": skipped,
            "servidores_merged": merged,
            "servidores_conflicts": conflicts,
            "alunos_ready": n_alunos,
            "terceiros_ready": n_terceiros,
            "dedup_results_created": len(dedup_results),
        }
        step.save()

        execution.total_skipped = skipped
        execution.save(update_fields=["total_skipped", "updated_at"])

        logger.info(
            "[%s] Step 4 concluido: srv=%d ready/%d skip | aluno=%d | terc=%d",
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
