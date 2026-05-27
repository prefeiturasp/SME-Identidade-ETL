"""ViewSets de API e views baseadas em funcao para gerenciamento das execucoes do ETL."""
import logging

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from core.service import KeycloakUpsertService
from etl_ms.celery import app as celery_app
from extract.tasks import extract_coresso_perfis, extract_coresso_sistemas
from staging.models import RetroalimentacaoCoreSSO, StagingPerfilCoreSSO, StagingSistema
from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

from .keycloak_client import get_admin_client, upsert_kc_client, upsert_kc_client_role
from .models import ETLExecution, UpsertControl
from .serializers import (
    ETLExecutionCreateSerializer,
    ETLExecutionListSerializer,
    ETLExecutionSerializer,
    ReloadKeycloakSerializer,
    RunExtractSerializer,
    RunStepSerializer,
    SyncSelectiveSerializer,
    UpsertControlSerializer,
)
from .tasks import run_etl_pipeline

logger = logging.getLogger(__name__)


class ETLExecutionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet para listagem, recuperacao e disparo de execucoes do pipeline ETL."""

    queryset = ETLExecution.objects.all()
    lookup_field = "id"

    def get_serializer_class(self):
        """Retorna o serializer apropriado conforme a action atual."""
        if self.action == "list":
            return ETLExecutionListSerializer
        if self.action == "create":
            return ETLExecutionCreateSerializer
        return ETLExecutionSerializer

    def create(self, request, *args, **kwargs):
        """Dispara uma nova execucao do pipeline ETL a partir de uma requisicao da API."""
        serializer = ETLExecutionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        execution = ETLExecution.objects.create(
            trigger_type=ETLExecution.TriggerType.MANUAL,
            source=serializer.validated_data.get("source", "all"),
            target_realm=serializer.validated_data.get("target_realm", "sme-apps"),
            note=serializer.validated_data.get("note", ""),
            load_keycloak=serializer.validated_data.get("load_keycloak", False),
            load_token_ms=serializer.validated_data.get("load_token_ms", True),
            max_records=serializer.validated_data.get("max_records", None),
            max_records_extract=serializer.validated_data.get("max_records_extract", None),
            user_types=serializer.validated_data.get("user_types", "all"),
            skip_steps=serializer.validated_data.get("skip_steps", []),
            executed_by=request.META.get("HTTP_X_FORWARDED_USER", "api"),
        )

        task = run_etl_pipeline.delay(str(execution.id))
        execution.celery_task_id = task.id
        execution.save(update_fields=["celery_task_id"])

        logger.info("ETL execution %s triggered manually", execution.id)
        return Response(
            ETLExecutionSerializer(execution).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def reload_keycloak(self, request, id=None):
        """Re-executa apenas o step 6 (Load Keycloak) de uma execucao existente.

        Util para re-carga apos limpeza do Keycloak ou testes parciais com max_records.
        Idempotente: reseta o ETLStepLog do step 6 e, opcionalmente, os staging records.
        """
        from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

        from .models import ETLStepLog
        from .tasks import load_keycloak

        execution = self.get_object()

        if execution.status == ETLExecution.Status.RUNNING:
            return Response(
                {"detail": "Execução ainda em andamento. Aguarde ou cancele antes de re-executar o step 6."},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ReloadKeycloakSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        max_records = serializer.validated_data.get("max_records")
        reset_loaded = serializer.validated_data.get("reset_loaded", True)

        execution_id = str(execution.id)

        # Reseta staging records loaded → ready (para re-carga após limpeza do KC)
        if reset_loaded:
            reset_counts = {}
            for model_class in (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro):
                n = model_class.objects.filter(
                    execution_id=execution_id, status="loaded"
                ).update(status="ready")
                if n:
                    reset_counts[model_class.__name__] = n
            logger.info(
                "[%s] reload_keycloak: staging records resetados → ready: %s",
                execution_id, reset_counts,
            )

        # Conta quantos estão prontos
        ready_count = (
            StagingUsuarioServidor.objects.filter(execution_id=execution_id, status="ready").count()
            + StagingUsuarioAluno.objects.filter(execution_id=execution_id, status="ready").count()
            + StagingUsuarioTerceiro.objects.filter(execution_id=execution_id, status="ready").count()
        )

        if ready_count == 0:
            return Response(
                {"detail": "Nenhum registro em status 'ready' para esta execução. Tente com reset_loaded=true."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # Remove o ETLStepLog do step 6 para contornar o _step_done() e permitir re-execução
        ETLStepLog.objects.filter(
            execution=execution, step_name=ETLStepLog.StepName.LOAD_KEYCLOAK
        ).delete()

        # Atualiza flags da execução
        execution.load_keycloak = True
        if max_records is not None:
            execution.max_records = max_records
        execution.status = ETLExecution.Status.RUNNING
        execution.save(update_fields=["load_keycloak", "max_records", "status", "updated_at"])

        task = load_keycloak.delay(execution_id)
        execution.celery_task_id = task.id
        execution.save(update_fields=["celery_task_id"])

        logger.info(
            "[%s] reload_keycloak disparado — ready=%d, max_records=%s, reset_loaded=%s",
            execution_id, ready_count, max_records, reset_loaded,
        )

        return Response(
            {
                "detail": "Step 6 (Load Keycloak) re-agendado com sucesso.",
                "execution_id": execution_id,
                "ready_records": ready_count,
                "max_records": max_records,
                "reset_loaded": reset_loaded,
                "celery_task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, id=None):
        """Cancela uma execucao em PENDING ou RUNNING, revogando sua task Celery."""
        execution = self.get_object()
        if execution.status not in (
            ETLExecution.Status.PENDING,
            ETLExecution.Status.RUNNING,
        ):
            return Response(
                {"detail": f"Execução com status '{execution.status}' não pode ser cancelada"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if execution.celery_task_id:
            celery_app.control.revoke(execution.celery_task_id, terminate=True)

        execution.status = ETLExecution.Status.CANCELLED
        execution.finished_at = timezone.now()
        execution.save(update_fields=["status", "finished_at", "updated_at"])

        logger.info("ETL execution %s cancelled", execution.id)
        return Response(ETLExecutionSerializer(execution).data)

    # ------------------------------------------------------------------
    # Helper interno
    # ------------------------------------------------------------------

    def _guard_step(self, execution, step_names: list, force: bool = True):
        """Valida e prepara a execucao para re-executar um step individual.

        Retorna Response 409 se a execucao estiver RUNNING; None caso contrario.
        Quando force=True, apaga os ETLStepLogs listados e marca execution como RUNNING.
        """
        from .models import ETLStepLog

        if execution.status == ETLExecution.Status.RUNNING:
            return Response(
                {"detail": "Execução ainda em andamento. Aguarde ou cancele antes de re-executar um step."},
                status=status.HTTP_409_CONFLICT,
            )
        if force:
            ETLStepLog.objects.filter(execution=execution, step_name__in=step_names).delete()
        execution.status = ETLExecution.Status.RUNNING
        execution.save(update_fields=["status", "updated_at"])
        return None

    # ------------------------------------------------------------------
    # Steps individuais (0–8)
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="run-sync-catalogo")
    def run_sync_catalogo(self, request, id=None):
        """Step 0 — Re-executa a sincronizacao de Sistemas e Perfis do CoreSSO como Clients/Roles no Keycloak."""
        from .models import ETLStepLog
        from .tasks import run_sync_catalogo_step

        execution = self.get_object()
        serializer = RunStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        err = self._guard_step(
            execution,
            [ETLStepLog.StepName.SYNC_CATALOGO],
            force=serializer.validated_data["force"],
        )
        if err:
            return err

        task = run_sync_catalogo_step.delay(str(execution.id))
        execution.celery_task_id = task.id
        execution.save(update_fields=["celery_task_id"])

        logger.info("[%s] run_sync_catalogo agendado", execution.id)
        return Response(
            {
                "detail": "Step 0 (Sync Catálogo) agendado.",
                "execution_id": str(execution.id),
                "celery_task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="run-extract")
    def run_extract(self, request, id=None):
        """Steps 1/1b/1c/2 — Re-executa a extracao de uma ou todas as fontes (SE1426, EOL, CoreSSO)."""
        from celery import chain as celery_chain

        from extract.tasks import extract_coresso, extract_eol_alunos, extract_eol_db, extract_se1426

        from .models import ETLStepLog

        execution = self.get_object()
        serializer = RunExtractSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = serializer.validated_data["source"]
        force = serializer.validated_data["force"]

        step_names_by_source = {
            "se1426": [ETLStepLog.StepName.EXTRACT_SE1426],
            "eol_db": [ETLStepLog.StepName.EXTRACT_EOL_DB],
            "eol_alunos": [ETLStepLog.StepName.EXTRACT_EOL_DB],
            "coresso": [ETLStepLog.StepName.EXTRACT_CORESSO],
            "all": [
                ETLStepLog.StepName.EXTRACT_SE1426,
                ETLStepLog.StepName.EXTRACT_EOL_DB,
                ETLStepLog.StepName.EXTRACT_CORESSO,
            ],
        }
        err = self._guard_step(execution, step_names_by_source.get(source, []), force=force)
        if err:
            return err

        execution_id = str(execution.id)
        tasks = []
        if source in ("all", "se1426"):
            tasks.append(extract_se1426.si(execution_id))
        if source in ("all", "eol_db"):
            tasks.append(extract_eol_db.si(execution_id))
        if source in ("all", "eol_alunos", "eol_db"):
            tasks.append(extract_eol_alunos.si(execution_id))
        if source in ("all", "coresso"):
            tasks.append(extract_coresso.si(execution_id))

        if not tasks:
            return Response(
                {"detail": f"Nenhuma task configurada para source='{source}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = celery_chain(*tasks).delay()
        execution.celery_task_id = result.id
        execution.save(update_fields=["celery_task_id"])

        logger.info("[%s] run_extract agendado — source=%s, %d task(s)", execution.id, source, len(tasks))
        return Response(
            {
                "detail": f"Extração (source='{source}') agendada — {len(tasks)} task(s).",
                "execution_id": execution_id,
                "source": source,
                "tasks_count": len(tasks),
                "celery_task_id": result.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="run-transform")
    def run_transform(self, request, id=None):
        """Step 3 — Re-executa a transformacao e normalizacao dos registros de staging."""
        from staging.tasks import transform_staging

        from .models import ETLStepLog

        execution = self.get_object()
        serializer = RunStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        err = self._guard_step(
            execution,
            [ETLStepLog.StepName.STAGING],
            force=serializer.validated_data["force"],
        )
        if err:
            return err

        task = transform_staging.delay(str(execution.id))
        execution.celery_task_id = task.id
        execution.save(update_fields=["celery_task_id"])

        logger.info("[%s] run_transform agendado", execution.id)
        return Response(
            {
                "detail": "Step 3 (Transform Staging) agendado.",
                "execution_id": str(execution.id),
                "celery_task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="run-crossref")
    def run_crossref(self, request, id=None):
        """Step 4 — Re-executa o cross-reference e deduplicacao entre fontes."""
        from staging.tasks import crossref_dedup

        from .models import ETLStepLog

        execution = self.get_object()
        serializer = RunStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        err = self._guard_step(
            execution,
            [ETLStepLog.StepName.CROSSREF_DEDUP],
            force=serializer.validated_data["force"],
        )
        if err:
            return err

        task = crossref_dedup.delay(str(execution.id))
        execution.celery_task_id = task.id
        execution.save(update_fields=["celery_task_id"])

        logger.info("[%s] run_crossref agendado", execution.id)
        return Response(
            {
                "detail": "Step 4 (Crossref/Dedup) agendado.",
                "execution_id": str(execution.id),
                "celery_task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="run-decide")
    def run_decide(self, request, id=None):
        """Step 5 — Re-executa a decisao de create vs update para cada registro de staging."""
        from .models import ETLStepLog
        from .tasks import decide_target

        execution = self.get_object()
        serializer = RunStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        err = self._guard_step(
            execution,
            [ETLStepLog.StepName.DECISION],
            force=serializer.validated_data["force"],
        )
        if err:
            return err

        task = decide_target.delay(str(execution.id))
        execution.celery_task_id = task.id
        execution.save(update_fields=["celery_task_id"])

        logger.info("[%s] run_decide agendado", execution.id)
        return Response(
            {
                "detail": "Step 5 (Decide Target) agendado.",
                "execution_id": str(execution.id),
                "celery_task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="run-load-token")
    def run_load_token(self, request, id=None):
        """Step 7 — Re-executa o envio de usuarios staged para o Token-MS."""
        from .models import ETLStepLog
        from .tasks import load_token_ms

        execution = self.get_object()
        serializer = RunStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        err = self._guard_step(
            execution,
            [ETLStepLog.StepName.LOAD_TOKEN_MS],
            force=serializer.validated_data["force"],
        )
        if err:
            return err

        execution.load_token_ms = True
        execution.save(update_fields=["load_token_ms", "updated_at"])

        task = load_token_ms.delay(str(execution.id))
        execution.celery_task_id = task.id
        execution.save(update_fields=["celery_task_id"])

        logger.info("[%s] run_load_token agendado", execution.id)
        return Response(
            {
                "detail": "Step 7 (Load Token-MS) agendado.",
                "execution_id": str(execution.id),
                "celery_task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="run-audit")
    def run_audit(self, request, id=None):
        """Step 8 — Re-executa o audit e finaliza a execucao (marca como success/partial/failed)."""
        from .models import ETLStepLog
        from .tasks import audit_etl

        execution = self.get_object()
        serializer = RunStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        err = self._guard_step(
            execution,
            [ETLStepLog.StepName.AUDIT],
            force=serializer.validated_data["force"],
        )
        if err:
            return err

        task = audit_etl.delay(str(execution.id))
        execution.celery_task_id = task.id
        execution.save(update_fields=["celery_task_id"])

        logger.info("[%s] run_audit agendado", execution.id)
        return Response(
            {
                "detail": "Step 8 (Audit) agendado. A execução será finalizada ao concluir.",
                "execution_id": str(execution.id),
                "celery_task_id": task.id,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class UpsertControlViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet somente-leitura para navegar pelos registros de controle de upsert do Keycloak."""

    queryset = UpsertControl.objects.all()
    serializer_class = UpsertControlSerializer
    filterset_fields = [
        "entity_type",
        "source_system",
        "target_realm",
        "is_active",
    ]
    search_fields = ["source_id", "target_id"]


@api_view(["GET"])
def etl_stats(request):
    """Retorna estatisticas agregadas de execucoes do ETL dos ultimos 30 dias."""
    last_30_days = timezone.now() - timezone.timedelta(days=30)

    executions = ETLExecution.objects.filter(created_at__gte=last_30_days)

    stats = executions.aggregate(
        total_executions=Count("id"),
        total_extracted=Sum("total_extracted"),
        total_loaded=Sum("total_loaded"),
        total_errors=Sum("total_errors"),
    )

    status_counts = dict(
        executions.values_list("status").annotate(count=Count("id")).values_list("status", "count")
    )

    upsert_stats = UpsertControl.objects.aggregate(
        total_records=Count("id"),
        active_records=Count("id", filter=Q(is_active=True)),
    )

    return Response(
        {
            "period": "last_30_days",
            "executions": {
                "total": stats["total_executions"],
                "by_status": status_counts,
                "total_extracted": stats["total_extracted"] or 0,
                "total_loaded": stats["total_loaded"] or 0,
                "total_errors": stats["total_errors"] or 0,
            },
            "upsert_control": upsert_stats,
            "timestamp": timezone.now().isoformat(),
        }
    )


@api_view(["POST"])
def test_kc_upsert(request, cpf=None):
    body = request.data or {}

    cpf = cpf or body.get("cpf")
    rf = body.get("rf")

    if not cpf and not rf:
        return Response(
            {
                "detail": (
                    "Informe 'cpf' "
                    "(path ou body) "
                    "ou 'rf' no body."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        service = KeycloakUpsertService(
            cpf=cpf,
            rf=rf,
            realm=body.get("realm"),
            execution_id=body.get("execution_id"),
            assign_roles=body.get(
                "assign_roles",
                True,
            ),
            push_token_ms=body.get(
                "push_token_ms",
                True,
            ),
        )

        result = service.execute()

        return Response(
            result,
            status=status.HTTP_200_OK,
        )

    except ValueError as e:
        return Response(
            {
                "detail": str(e),
                "cpf": cpf,
                "rf": rf,
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    except Exception as e:
        logger.exception(
            "test_kc_upsert FAILED: %s",
            e,
        )

        return Response(
            {
                "detail": (
                    "Falha ao executar "
                    "upsert no Keycloak"
                ),
                "error": str(e),
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )


@api_view(["GET"])
def sistemas_list(request):
    """Retorna a lista de todos os staging sistemas com o respectivo mapeamento de client Keycloak."""
    return Response([
        {
            "coresso_sis_id": s.coresso_sis_id,
            "nome": s.nome,
            "sigla": s.sigla,
            "kc_client_id": s.kc_client_id,
            "kc_client_uuid": s.kc_client_uuid,
            "kc_realm": s.kc_realm,
            "status": s.status,
            "url_callback": s.url_callback,
        }
        for s in StagingSistema.objects.all().order_by("coresso_sis_id")
    ])


@api_view(["POST"])
def sistemas_extract(request):
    """Extrai sistemas CoreSSO para staging_sistema."""
    try:
        total = extract_coresso_sistemas()
        return Response({"total_extracted": total})
    except Exception as e:
        logger.exception("sistemas_extract FAILED: %s", e)
        return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["POST"])
def sistemas_load_keycloak(request):
    """Carrega sistemas staging como clients OIDC no Keycloak."""
    from django.conf import settings

    sigla = request.data.get("sigla")
    realm = getattr(settings, "KEYCLOAK_REALM", "sme-apps")

    qs = StagingSistema.objects.filter(situacao=1)
    if sigla:
        qs = qs.filter(sigla=sigla)

    admin = get_admin_client(realm=realm)
    created_items = []
    updated_items = []
    errors = []

    for sistema in qs:
        try:
            result = upsert_kc_client(admin, sistema, realm=realm)
            if result.get("action") == "created":
                created_items.append(result)
            else:
                updated_items.append(result)
        except Exception as e:
            logger.warning("Falha upsert client %s: %s", sistema.sigla, e)
            errors.append({"sigla": sistema.sigla, "error": str(e)})

    return Response({
        "total": len(created_items) + len(updated_items),
        "created": created_items,
        "updated": updated_items,
        "errors": errors,
    })


@api_view(["GET"])
def perfis_list(request):
    """Retorna a lista de todos os perfis CoreSSO de staging com o mapeamento de roles do Keycloak."""
    sis_id = request.query_params.get("coresso_sis_id")
    qs = StagingPerfilCoreSSO.objects.select_related("sistema")
    if sis_id:
        qs = qs.filter(coresso_sis_id=int(sis_id))
    return Response([
        {
            "coresso_gru_id": p.coresso_gru_id,
            "nome": p.nome,
            "coresso_sis_id": p.coresso_sis_id,
            "kc_client_id": p.sistema.kc_client_id if p.sistema else None,
            "kc_role_name": p.kc_role_name,
            "kc_role_id": p.kc_role_id,
            "status": p.status,
        }
        for p in qs.order_by("coresso_sis_id", "nome")[:1000]
    ])


@api_view(["POST"])
def perfis_extract(request):
    """Extrai perfis CoreSSO para staging_perfil_coresso."""
    try:
        total = extract_coresso_perfis()
        return Response({"total_extracted": total})
    except Exception as e:
        logger.exception("perfis_extract FAILED: %s", e)
        return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["POST"])
def perfis_load_keycloak(request):
    """Carrega perfis staging como client roles no Keycloak."""
    from django.conf import settings

    coresso_sis_id = request.data.get("coresso_sis_id")
    realm = getattr(settings, "KEYCLOAK_REALM", "sme-apps")

    qs = StagingPerfilCoreSSO.objects.select_related("sistema")
    if coresso_sis_id:
        qs = qs.filter(coresso_sis_id=int(coresso_sis_id))

    admin = get_admin_client(realm=realm)
    created = updated = skipped = errors = 0
    count = 0

    for perfil in qs.iterator(chunk_size=200):
        count += 1
        if count % 50 == 0:
            try:
                admin = get_admin_client(realm=realm)
            except Exception as e:
                logger.warning("Falha ao renovar admin client: %s", e)
        try:
            result = upsert_kc_client_role(admin, perfil)
            action = result.get("action", "created")
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            logger.warning("Falha upsert client role %s: %s", getattr(perfil, "kc_role_name", str(perfil)), e)
            errors += 1

    return Response({
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    })


@api_view(["GET"])
def retroalim_list(request):
    """Retorna uma lista paginada de registros de retroalimentacao do CoreSSO."""
    qs = RetroalimentacaoCoreSSO.objects.all()
    st = request.query_params.get("status")
    if st:
        qs = qs.filter(status=st)
    return Response([
        {
            "id": str(e.id),
            "tipo": e.tipo,
            "rf": e.rf,
            "cpf": e.cpf,
            "status": e.status,
            "delivery_attempts": e.delivery_attempts,
            "created_at": e.created_at.isoformat(),
            "payload": e.payload,
        }
        for e in qs.order_by("-created_at")[:200]
    ])


@api_view(["POST"])
def sync_selective(request):
    """Sincroniza seletivamente usuarios por CPF, RF ou quantidade de registros READY.

    Retorna status individual por registro: success, skipped, error, not_found.
    Indica tambem se o usuario existe no CoreSSO (source=coresso no staging).
    """
    from .serializers import SyncSelectiveSerializer

    serializer = SyncSelectiveSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    cpfs = [c.replace(".", "").replace("-", "").strip() for c in data.get("cpfs", [])]
    rfs = [r.strip() for r in data.get("rfs", [])]
    limit = data.get("limit")
    realm = data.get("realm", "sme-apps")
    do_load_keycloak = data.get("load_keycloak", False)
    do_push_token_ms = data.get("push_token_ms", True)

    USER_MODELS = (StagingUsuarioServidor, StagingUsuarioAluno, StagingUsuarioTerceiro)
    results = []

    def _find_in_staging(cpf=None, rf=None):
        """Busca o registro mais recente no staging para o CPF ou RF informado."""
        for model_class in USER_MODELS:
            qs = model_class.objects.all()
            if cpf:
                qs = qs.filter(cpf=cpf)
            elif rf and model_class == StagingUsuarioServidor:
                qs = qs.filter(rf=rf)
            else:
                continue
            record = qs.order_by("-extracted_at").first()
            if record:
                return record
        return None

    def _build_result(identifier, identifier_type, record, kc_result=None, error=None):
        exists_coresso = False
        if record:
            exists_coresso = record.source == "coresso" or (
                StagingUsuarioTerceiro.objects.filter(cpf=record.cpf).exists()
                if record.cpf else False
            )
        return {
            "identifier": identifier,
            "identifier_type": identifier_type,
            "nome": record.nome if record else None,
            "source": record.source if record else None,
            "found_in_staging": record is not None,
            "exists_coresso": exists_coresso,
            "kc_action": kc_result.get("action") if kc_result else None,
            "kc_user_id": kc_result.get("kc_user_id") if kc_result else None,
            "status": (
                "error" if error
                else ("success" if kc_result else ("not_found" if not record else "skipped"))
            ),
            "error": str(error) if error else None,
        }

    # -- Por CPFs informados --
    for cpf in cpfs:
        record = _find_in_staging(cpf=cpf)
        if not record:
            results.append(_build_result(cpf, "cpf", None))
            continue
        if not do_load_keycloak:
            results.append(_build_result(cpf, "cpf", record))
            continue
        try:
            svc = KeycloakUpsertService(
                cpf=cpf, realm=realm,
                assign_roles=True, push_token_ms=do_push_token_ms,
            )
            kc = svc.execute()
            results.append(_build_result(cpf, "cpf", record, kc_result=kc))
        except Exception as e:
            logger.exception("sync_selective CPF=%s error: %s", cpf, e)
            results.append(_build_result(cpf, "cpf", record, error=e))

    # -- Por RFs informados --
    for rf in rfs:
        record = _find_in_staging(rf=rf)
        if not record:
            results.append(_build_result(rf, "rf", None))
            continue
        if not do_load_keycloak:
            results.append(_build_result(rf, "rf", record))
            continue
        try:
            svc = KeycloakUpsertService(
                rf=rf, realm=realm,
                assign_roles=True, push_token_ms=do_push_token_ms,
            )
            kc = svc.execute()
            results.append(_build_result(rf, "rf", record, kc_result=kc))
        except Exception as e:
            logger.exception("sync_selective RF=%s error: %s", rf, e)
            results.append(_build_result(rf, "rf", record, error=e))

    # -- Por quantidade (limit): seleciona registros READY da última execução --
    if limit and not cpfs and not rfs:
        last_exec = (
            ETLExecution.objects.filter(status__in=["success", "partial"])
            .order_by("-finished_at")
            .first()
        )
        if not last_exec:
            return Response(
                {"detail": "Nenhuma execução concluída encontrada para selecionar registros."},
                status=status.HTTP_404_NOT_FOUND,
            )

        for model_class in USER_MODELS:
            if len(results) >= limit:
                break
            remaining = limit - len(results)
            records = list(
                model_class.objects.filter(
                    execution_id=last_exec.id,
                    status__in=["ready", "loaded"],
                ).order_by("-extracted_at")[:remaining]
            )
            for record in records:
                identifier = record.cpf or (getattr(record, "rf", None))
                id_type = "cpf" if record.cpf else "rf"
                if not identifier:
                    continue
                if not do_load_keycloak:
                    results.append(_build_result(identifier, id_type, record))
                    continue
                try:
                    svc = KeycloakUpsertService(
                        cpf=record.cpf if record.cpf else None,
                        rf=getattr(record, "rf", None) if not record.cpf else None,
                        realm=realm,
                        assign_roles=True,
                        push_token_ms=do_push_token_ms,
                    )
                    kc = svc.execute()
                    results.append(_build_result(identifier, id_type, record, kc_result=kc))
                except Exception as e:
                    logger.exception("sync_selective limit record=%s error: %s", identifier, e)
                    results.append(_build_result(identifier, id_type, record, error=e))

    summary = {
        "total": len(results),
        "success": sum(1 for r in results if r["status"] == "success"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "not_found": sum(1 for r in results if r["status"] == "not_found"),
        "error": sum(1 for r in results if r["status"] == "error"),
        "load_keycloak": do_load_keycloak,
        "realm": realm,
    }

    return Response({"summary": summary, "results": results})