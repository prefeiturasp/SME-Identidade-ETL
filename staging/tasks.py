"""Tasks de transformação, enriquecimento e deduplicação dos dados de staging.

Este módulo contém as etapas intermediárias do pipeline ETL responsáveis por:

- Normalização e validação de dados extraídos.
- Enriquecimento de registros com informações de lotação.
- Cruzamento de registros oriundos de múltiplas fontes.
- Deduplicação de usuários com base em CPF e RF.
- Consolidação de dados para preparação da carga nos sistemas de destino.

As tarefas são executadas via Celery e operam sobre os modelos de staging
gerados durante a etapa de extração.
"""

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

def _get_update_fields(extra_fields=None):
    """Monta a lista de campos utilizados no bulk update.

    Args:
        extra_fields (dict | None): Configuração de campos adicionais.

    Returns:
        list[str]: Lista de campos que serão atualizados.
    """
    fields = [
        "cpf",
        "nome",
        "status",
        "transformed_at",
        "error_detail",
    ]

    if not extra_fields:
        return fields

    if extra_fields.get("rf_field"):
        fields.append("rf")

    if extra_fields.get("lotacao_field"):
        fields.extend(["dre", "ue"])

    return fields

def _normalize_cpf_field(record):
    """Normaliza e valida o CPF do registro."""
    if not record.cpf:
        return

    cpf_clean = normalize_cpf(record.cpf)

    if cpf_clean and validate_cpf(cpf_clean):
        record.cpf = cpf_clean
        return

    record.error_detail = f"CPF inválido: {record.cpf}"
    record.cpf = cpf_clean

def _normalize_rf_field(record):
    """Normaliza o RF do registro."""
    if hasattr(record, "rf") and record.rf:
        record.rf = normalize_rf(record.rf)

def _normalize_name(record):
    """Normaliza o nome do registro."""
    if record.nome:
        record.nome = " ".join(record.nome.split()).title()

def _fill_lotacao(record, lotacao_map):
    """Preenche DRE e UE a partir da lotação.

    Args:
        record: Registro processado.
        lotacao_map (dict): Mapeamento de lotações.
    """
    if (
        not hasattr(record, "lotacao")
        or not record.lotacao
        or record.dre
    ):
        return

    lotacao = lotacao_map.get(record.lotacao)

    if not lotacao:
        return

    record.dre = lotacao.dre_codigo
    record.ue = lotacao.codigo if lotacao.tipo == "ue" else None

def _transform_record(record, lotacao_map):
    """Aplica todas as regras de transformação ao registro.

    Args:
        record: Registro processado.
        lotacao_map (dict): Mapeamento de lotações.
    """
    _normalize_cpf_field(record)
    _normalize_rf_field(record)
    _normalize_name(record)
    _fill_lotacao(record, lotacao_map)

def _flush_buffers(
    model_class,
    buf_ok,
    buf_err,
    update_fields,
    bulk_size,
):
    """Persiste os registros acumulados nos buffers.

    Args:
        model_class: Modelo processado.
        buf_ok (list): Registros transformados com sucesso.
        buf_err (list): Registros com erro.
        update_fields (list[str]): Campos atualizados.
        bulk_size (int): Tamanho do lote.

    Returns:
        tuple[int, int]: Quantidade de registros transformados e com erro.
    """
    transformed = 0
    errors = 0

    if buf_ok:
        model_class.objects.bulk_update(
            buf_ok,
            update_fields,
            batch_size=bulk_size,
        )
        transformed = len(buf_ok)
        buf_ok.clear()

    if buf_err:
        model_class.objects.bulk_update(
            buf_err,
            ["status", "error_detail"],
            batch_size=bulk_size,
        )
        errors = len(buf_err)
        buf_err.clear()

    return transformed, errors

def _transform_model(
    model_class,
    execution_id,
    lotacao_map,
    bulk_size,
    extra_fields=None,
):
    """Transforma registros brutos em registros processados.

    Aplica as regras de normalização e enriquecimento dos dados,
    atualizando os registros em lote para otimizar a persistência.

    Args:
        model_class: Modelo a ser processado.
        execution_id (UUID): Identificador da execução.
        lotacao_map (dict): Mapeamento de lotações.
        bulk_size (int): Quantidade de registros por lote.
        extra_fields (dict | None): Configuração de campos adicionais.

    Returns:
        tuple[int, int]: Quantidade de registros transformados e quantidade
            de registros com erro.
    """
    update_fields = _get_update_fields(extra_fields)

    transformed = 0
    errors = 0
    now = timezone.now()

    buf_ok = []
    buf_err = []

    queryset = model_class.objects.filter(
        execution_id=execution_id,
        status=model_class.Status.RAW,
    )

    for record in queryset.iterator(chunk_size=bulk_size):
        try:
            _transform_record(record, lotacao_map)

            record.status = model_class.Status.TRANSFORMED
            record.transformed_at = now

            buf_ok.append(record)

        except Exception as exc:
            record.status = model_class.Status.ERROR
            record.error_detail = f"Transform error: {exc}"

            buf_err.append(record)

        if len(buf_ok) + len(buf_err) >= bulk_size:
            ok, err = _flush_buffers(
                model_class,
                buf_ok,
                buf_err,
                update_fields,
                bulk_size,
            )
            transformed += ok
            errors += err

    ok, err = _flush_buffers(
        model_class,
        buf_ok,
        buf_err,
        update_fields,
        bulk_size,
    )

    transformed += ok
    errors += err

    return transformed, errors

@shared_task(bind=True, name="staging.tasks.transform_staging")
def transform_staging(self, execution_id: str):
    """Executa a etapa de transformação dos dados de staging.

    Processa registros de servidores, alunos e terceiros aplicando
    validações, normalizações e enriquecimentos necessários para
    as etapas posteriores do pipeline.

    Args:
        self: Instância da task Celery.
        execution_id: Identificador da execução ETL.

    Returns:
        None.

    Raises:
        Exception: Repropaga qualquer erro ocorrido durante o processamento.
    """
    from core.models import ETLExecution, ETLStepLog

    from .models import (
        StagingLotacao,
        StagingUsuarioAluno,
        StagingUsuarioServidor,
        StagingUsuarioTerceiro,
    )

    execution = ETLExecution.objects.get(id=execution_id)
    step = ETLStepLog.objects.create(
        execution=execution,
        step_name=ETLStepLog.StepName.STAGING,
        step_order=3,
    )

    logger.info(
        "[%s] Step 3: Transform staging (servidor/aluno/terceiro)",
        execution_id
    )

    try:
        lotacao_map = {
            lot.codigo: lot
            for lot in StagingLotacao.objects.only("codigo", "dre_codigo", "tipo")
        }

        bulk_size = 500
        total_transformed = 0
        total_errors = 0

        for model_class, extra in [
            (StagingUsuarioServidor, {"rf_field": True, "lotacao_field": True}),
            (StagingUsuarioAluno,    {"rf_field": False, "lotacao_field": True}),
            (StagingUsuarioTerceiro, {"rf_field": False, "lotacao_field": False}),
        ]:
            t, e = _transform_model(
                model_class,
                execution_id,
                lotacao_map,
                bulk_size,
                extra
            )
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

    except Exception as e:
        logger.exception("[%s] Step 3 FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)
        step.finished_at = timezone.now()
        step.save()
        raise

def _mark_non_servidores_ready(execution_id):
    """
    Marca registros de alunos e terceiros como READY no fluxo de staging.

    Atualiza todos os registros com status TRANSFORMED para READY, separados por
    tipo (alunos e terceiros), e retorna a quantidade de registros atualizados.

    Args:
        execution_id (str): Identificador da execução ETL.

    Returns:
        tuple[int, int]: Quantidade de alunos e terceiros atualizados.
    """
    from .models import StagingUsuarioAluno, StagingUsuarioTerceiro

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

    return n_alunos, n_terceiros

def _build_indexes(transformed_qs):
    """
    Constrói índices auxiliares para deduplicação de registros.

    Cria três estruturas:
    - Índice de CPF → lista de IDs
    - Índice de RF → lista de IDs
    - Mapa de registros por ID

    Args:
        transformed_qs (QuerySet): Queryset de registros transformados.

    Returns:
        tuple[dict, dict, dict]: cpf_index, rf_index, records_by_id.
    """
    cpf_index = defaultdict(list)
    rf_index = defaultdict(list)
    records_by_id = {}

    for record in transformed_qs.only(
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

    return cpf_index, rf_index, records_by_id

def _create_union_find(records_by_id):
    """
    Cria estrutura Union-Find (Disjoint Set) para agrupamento de registros.

    Permite unir registros duplicados e encontrar componentes conectados.

    Args:
        records_by_id (dict): Mapa de registros por ID.

    Returns:
        tuple: (parent dict, função find, função union)
    """
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

    return parent, find, union

def _merge_by_index(index, union_fn):
    """
    Aplica união de conjuntos com base em um índice (CPF ou RF).

    Args:
        index (dict): Índice contendo listas de IDs agrupados.
        union_fn (callable): Função union da estrutura Union-Find.
    """
    for ids in index.values():
        for i in range(1, len(ids)):
            union_fn(ids[0], ids[i])

def _crossref_cpf_rf(cpf_index, rf_index, records_by_id, union_fn):
    """
    Realiza cruzamento entre CPF e RF para reforçar agrupamentos duplicados.

    Identifica relações cruzadas entre CPF e RF e aplica união entre clusters.

    Args:
        cpf_index (dict): Índice de CPFs.
        rf_index (dict): Índice de RFs.
        records_by_id (dict): Mapa de registros por ID.
        union_fn (callable): Função de união do Union-Find.
    """
    cpf_to_rf = {}
    for _, rec in records_by_id.items():
        cpf = rec.cpf if rec.cpf and validate_cpf(rec.cpf) else None
        rf = rec.rf
        if cpf and rf:
            cpf_to_rf[cpf] = rf

    for cpf, rf in cpf_to_rf.items():
        cpf_ids = cpf_index.get(cpf, [])
        rf_ids = rf_index.get(rf, [])
        if cpf_ids and rf_ids:
            for rid in rf_ids:
                union_fn(cpf_ids[0], rid)

def _union_find_merge(cpf_index, rf_index, records_by_id):
    """
    Executa o processo completo de agrupamento usando Union-Find.

    Combina registros por CPF, RF e cruzamento CPF↔RF, gerando clusters finais.

    Args:
        cpf_index (dict): Índice de CPFs.
        rf_index (dict): Índice de RFs.
        records_by_id (dict): Mapa de registros por ID.

    Returns:
        dict: Clusters de IDs agrupados por raiz.
    """
    _, find, union = _create_union_find(records_by_id)

    _merge_by_index(cpf_index, union)
    _merge_by_index(rf_index, union)
    _crossref_cpf_rf(cpf_index, rf_index, records_by_id, union)

    clusters = defaultdict(list)
    for rid in records_by_id:
        clusters[find(rid)].append(rid)

    return clusters

def _merge_fields_from_loser(winner, loser):
    """
    Preenche campos vazios do registro vencedor com dados do registro perdedor.

    Apenas campos vazios no winner são preenchidos com valores do loser.

    Args:
        winner (Model): Registro vencedor.
        loser (Model): Registro perdedor.

    Returns:
        list[str]: Lista de campos que foram preenchidos.
    """
    merged_fields_list = []
    for field in MERGEABLE_FIELDS:
        winner_val = getattr(winner, field, None)
        loser_val = getattr(loser, field, None)
        if not winner_val and loser_val:
            setattr(winner, field, loser_val)
            merged_fields_list.append(field)
    return merged_fields_list

def _process_loser_record(winner, loser, execution_id):
    """
    Processa um registro duplicado perdedor no fluxo de deduplicação.

    Determina tipo de match, conflitos e decisão de merge/skip, além de
    gerar o objeto DedupResult.

    Args:
        winner (Model): Registro vencedor do cluster.
        loser (Model): Registro duplicado.
        execution_id (str): ID da execução ETL.

    Returns:
        tuple: (DedupResult, campos_mesclados, conflito_detectado)
    """
    from .models import DedupResult, StagingUsuarioServidor

    merged_fields_list = _merge_fields_from_loser(winner, loser)
    match_type = _determine_match_type(winner, loser)
    has_conflict = _check_conflicts(winner, loser)

    if has_conflict:
        decision = DedupResult.Decision.CONFLICT
    elif merged_fields_list:
        decision = DedupResult.Decision.MERGE
    else:
        decision = DedupResult.Decision.SKIP_DUPLICATE

    dedup_result = DedupResult(
        dedup_key=build_dedup_key(winner.cpf, winner.rf),
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

    loser.status = StagingUsuarioServidor.Status.SKIPPED
    loser.error_detail = f"Dedup: merged into {winner.id} ({match_type})"

    return dedup_result, merged_fields_list, has_conflict

def _process_single_cluster(member_ids, records_by_id, execution_id):
    """
    Processa um cluster de registros duplicados.

    Seleciona o vencedor, aplica merge de campos, processa perdedores e
    gera resultados de deduplicação.

    Args:
        member_ids (list[int]): IDs dos registros no cluster.
        records_by_id (dict): Mapa de registros carregados.
        execution_id (str): ID da execução ETL.

    Returns:
        dict: Resultado contendo winner, losers, dedup_results e flags de processamento.
    """
    from .models import StagingUsuarioServidor

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
        return {
            "winner": winner,
            "losers": [],
            "dedup_results": [],
            "error": True,
            "merged": False,
            "conflicts": 0,
        }

    if len(members) == 1:
        winner.status = StagingUsuarioServidor.Status.READY
        return {
            "winner": winner,
            "losers": [],
            "dedup_results": [],
            "error": False,
            "merged": False,
            "conflicts": 0,
        }

    losers = members[1:]
    dedup_results = []
    winner_updated_fields = []
    conflicts = 0

    for loser in losers:
        result, merged_fields, has_conflict = _process_loser_record(
            winner,
            loser,
            execution_id
        )
        dedup_results.append(result)

        for field in merged_fields:
            if field not in winner_updated_fields:
                winner_updated_fields.append(field)

        if has_conflict:
            conflicts += 1

    # Aplicar situação do CoreSSO se existir
    coresso_members = [m for m in members if m.source == "coresso"]
    if coresso_members:
        winner.situacao = coresso_members[0].situacao
        if "situacao" not in winner_updated_fields:
            winner_updated_fields.append("situacao")

    winner.status = StagingUsuarioServidor.Status.READY

    return {
        "winner": winner,
        "losers": losers,
        "dedup_results": dedup_results,
        "error": False,
        "merged": bool(winner_updated_fields),
        "conflicts": conflicts,
    }

def _process_all_clusters(clusters, records_by_id, execution_id):
    """
    Processa todos os clusters de deduplicação.

    Itera sobre clusters, executa processamento individual e agrega estatísticas.

    Args:
        clusters (dict): Clusters de IDs agrupados.
        records_by_id (dict): Mapa de registros.
        execution_id (str): ID da execução ETL.

    Returns:
        dict: Estatísticas e listas de atualização (winners, losers, erros, etc).
    """
    ready = 0
    skipped = 0
    merged = 0
    conflicts = 0
    errors = 0
    all_dedup_results = []
    winners_ready = []
    losers_skipped = []
    errors_no_key = []

    for cluster_root, member_ids in clusters.items():
        try:
            result = _process_single_cluster(member_ids, records_by_id, execution_id)

            if result["error"]:
                errors_no_key.append(result["winner"])
                errors += 1
            else:
                winners_ready.append(result["winner"])
                ready += 1
                if result["merged"]:
                    merged += 1
                if result["losers"]:
                    losers_skipped.extend(result["losers"])
                    skipped += len(result["losers"])
                all_dedup_results.extend(result["dedup_results"])
                conflicts += result["conflicts"]

        except Exception as e:
            logger.exception(
                "[%s] Dedup error for cluster %s: %s",
                execution_id,
                cluster_root,
                e
            )
            errors += 1

    return {
        "ready": ready,
        "skipped": skipped,
        "merged": merged,
        "conflicts": conflicts,
        "errors": errors,
        "all_dedup_results": all_dedup_results,
        "winners_ready": winners_ready,
        "losers_skipped": losers_skipped,
        "errors_no_key": errors_no_key,
    }

def _save_dedup_bulk(winners_ready, losers_skipped, errors_no_key, dedup_results):
    """
    Persiste em lote os resultados da deduplicação.

    Realiza bulk_update de registros e bulk_create de resultados de dedup.

    Args:
        winners_ready (list): Registros vencedores.
        losers_skipped (list): Registros descartados.
        errors_no_key (list): Registros com erro de chave.
        dedup_results (list): Objetos DedupResult.
    """
    from .models import DedupResult, StagingUsuarioServidor

    bulk = 500
    winner_fields = ["status", "email", "data_nascimento", "cargo", "funcao",
                     "situacao", "lotacao", "lotacao_nome", "dre", "ue"]

    if winners_ready:
        StagingUsuarioServidor.objects.bulk_update(
            winners_ready,
            winner_fields,
            batch_size=bulk
        )
    if losers_skipped:
        StagingUsuarioServidor.objects.bulk_update(
            losers_skipped, ["status", "error_detail"], batch_size=bulk,
        )
    if errors_no_key:
        StagingUsuarioServidor.objects.bulk_update(
            errors_no_key, ["status", "error_detail"], batch_size=bulk,
        )
    if dedup_results:
        DedupResult.objects.bulk_create(dedup_results, batch_size=500)

@shared_task(bind=True, name="staging.tasks.crossref_dedup")
def crossref_dedup(self, execution_id: str):
    """
    Executa o processo completo de cross-reference e deduplicação.

    Fluxo:
    1. Marca alunos e terceiros como READY
    2. Carrega servidores transformados
    3. Constrói índices de CPF/RF
    4. Gera clusters via Union-Find
    5. Processa deduplicação por cluster
    6. Persiste resultados em lote
    7. Atualiza logs da execução ETL

    Args:
        self: Instância da task Celery, usada para contexto interno do Celery.
        execution_id (str): Identificador da execução ETL.

    Returns:
        None
    """
    from core.models import ETLExecution, ETLStepLog

    from .models import StagingUsuarioServidor

    execution = ETLExecution.objects.get(id=execution_id)
    step = ETLStepLog.objects.create(
        execution=execution,
        step_name=ETLStepLog.StepName.CROSSREF_DEDUP,
        step_order=4,
    )

    logger.info(
        "[%s] Step 4: Crossref/Dedup (servidor) + mark ready (aluno/terceiro)",
        execution_id
    )

    try:
        # Marca alunos e terceiros como READY
        n_alunos, n_terceiros = _mark_non_servidores_ready(execution_id)
        logger.info(
            "[%s] Alunos prontos: %d | Terceiros prontos: %d",
            execution_id, n_alunos, n_terceiros,
        )

        # Busca servidores transformados
        transformed = StagingUsuarioServidor.objects.filter(
            execution_id=execution_id,
            status=StagingUsuarioServidor.Status.TRANSFORMED,
        )

        total = transformed.count()
        if total == 0:
            logger.warning(
                "[%s] Step 4: no transformed servidores to dedup",
                execution_id
            )
            step.records_in = n_alunos + n_terceiros
            step.records_out = n_alunos + n_terceiros
            step.status = "success"
            step.finished_at = timezone.now()
            step.save()
            return

        # Constrói índices e clusters
        cpf_index, rf_index, records_by_id = _build_indexes(transformed)
        clusters = _union_find_merge(cpf_index, rf_index, records_by_id)

        # Processa todos os clusters
        stats = _process_all_clusters(clusters, records_by_id, execution_id)

        # Salva resultados em bulk
        _save_dedup_bulk(
            stats["winners_ready"],
            stats["losers_skipped"],
            stats["errors_no_key"],
            stats["all_dedup_results"]
        )

        # Atualiza step log
        total_ready = stats["ready"] + n_alunos + n_terceiros
        step.records_in = total + n_alunos + n_terceiros
        step.records_out = total_ready
        step.records_error = stats["errors"]

        if stats["errors"] == 0:
            step.status = "success"
        elif stats["ready"] == 0:
            step.status = "failed"
        else:
            step.status = "success"

        step.finished_at = timezone.now()
        step.metadata = {
            "servidores_clusters": len(clusters),
            "servidores_ready": stats["ready"],
            "servidores_skipped": stats["skipped"],
            "servidores_merged": stats["merged"],
            "servidores_conflicts": stats["conflicts"],
            "alunos_ready": n_alunos,
            "terceiros_ready": n_terceiros,
            "dedup_results_created": len(stats["all_dedup_results"]),
        }
        step.save()

        execution.total_skipped = stats["skipped"]
        execution.save(update_fields=["total_skipped", "updated_at"])

        logger.info(
            "[%s] Step 4 concluido: srv=%d ready/%d skip | aluno=%d | terc=%d",
            execution_id, stats["ready"], stats["skipped"], n_alunos, n_terceiros,
        )

    except Exception as e:
        logger.exception("[%s] Step 4 FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)
        step.finished_at = timezone.now()
        step.save()
        raise

def _determine_match_type(winner, loser) -> str:
    """Determina o tipo de correspondência entre dois registros.

    A comparação considera CPF e RF para classificar o vínculo entre
    registros que participam de um mesmo agrupamento de deduplicação.

    Args:
        winner: Registro vencedor do agrupamento.
        loser: Registro candidato à fusão.

    Returns:
        Tipo de correspondência definido em
        DedupResult.MatchType.
    """
    from .models import DedupResult

    w_cpf = winner.cpf if winner.cpf and validate_cpf(winner.cpf) else None
    l_cpf = loser.cpf if loser.cpf and validate_cpf(loser.cpf) else None
    w_rf = winner.rf
    l_rf = loser.rf

    if w_cpf and l_cpf and w_cpf == l_cpf:
        if w_rf and l_rf and w_rf == l_rf:
            return DedupResult.MatchType.CPF_EXACT  # ambos batem
        return DedupResult.MatchType.CPF_EXACT

    if w_rf and l_rf and w_rf == l_rf:
        return DedupResult.MatchType.RF_EXACT

    return DedupResult.MatchType.CPF_RF_CROSS

def _check_conflicts(winner, loser) -> bool:
    """Verifica se existem conflitos entre dois registros.

    Um conflito ocorre quando ambos os registros possuem valores
    preenchidos para um mesmo campo e os valores são diferentes.

    Atualmente são avaliados os seguintes campos:

    - nome
    - cargo
    - situacao

    Args:
        winner: Registro vencedor da deduplicação.
        loser: Registro que será incorporado ao vencedor.

    Returns:
        True se houver ao menos um conflito.
        False caso contrário.
    """
    conflict_fields = ["nome", "cargo", "situacao"]
    for field in conflict_fields:
        w_val = getattr(winner, field, None)
        l_val = getattr(loser, field, None)
        if w_val and l_val and w_val.lower().strip() != l_val.lower().strip():
            return True
    return False
