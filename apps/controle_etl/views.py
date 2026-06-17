"""Views da API de controle do pipeline ETL de identidades."""

import contextlib
import logging

from django.db.models import Count, OuterRef, QuerySet, Subquery, Sum
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    CheckpointEtl,
    ControleProvisionamento,
    ExecucaoETL,
    LogEtapaETL,
    MarcaDaguaExtracao,
    RastreioTentativa,
)
from .serializers import (
    CheckpointEtlSerializer,
    ControleProvisionamentoSerializer,
    CriarExecucaoETLSerializer,
    ExecucaoETLSerializer,
    HealthStatusSerializer,
    ListarExecucaoETLSerializer,
    MarcaDaguaExtracaoSerializer,
    RastreioTentativaSerializer,
)

logger = logging.getLogger("etl_identidade")

_LIMITE_EXECUCOES_RECENTES = 100
_LIMITE_DASHBOARD = 100
_FONTES_VALIDAS = ["todos", "se1426", "coresso", "eol_alunos"]


def _qs_ultima_execucao_por_fonte() -> QuerySet:
    """Retorna queryset com a última execução de cada fonte."""
    return ExecucaoETL.objects.filter(
        criado_em=Subquery(
            ExecucaoETL.objects.filter(fonte=OuterRef("fonte"))
            .order_by("-criado_em")
            .values("criado_em")[:1]
        )
    ).order_by("fonte")


def _aplicar_filtros_execucao(
    qs: QuerySet,
    fonte: str,
    data_inicio: str,
    data_fim: str,
    situacao: str,
) -> QuerySet:
    """Aplica filtros opcionais a um queryset de ExecucaoETL."""
    if fonte:
        qs = qs.filter(fonte=fonte)
    if data_inicio:
        qs = qs.filter(criado_em__date__gte=data_inicio)
    if data_fim:
        qs = qs.filter(criado_em__date__lte=data_fim)
    if situacao:
        qs = qs.filter(situacao=situacao)
    return qs


def _disparar_execucao(
    fonte: str = "todos",
    realm_destino: str = "sme-apps",
    observacao: str = "",
    disparado_por: str = "api",
) -> ExecucaoETL:
    """Cria a ExecucaoETL e dispara o pipeline completo via Celery.

    Reaproveitada pelo endpoint de API (`ExecucoesView.post`) e pelo
    formulário de disparo do dashboard HTML.

    Args:
        fonte: todos | se1426 | coresso | eol_alunos.
        realm_destino: Realm Keycloak de destino.
        observacao: Texto livre opcional.
        disparado_por: Identificação de quem disparou a execução.

    Returns:
        A ExecucaoETL criada, já com `id_tarefa_celery` preenchido.
    """
    from .tasks import task_identidade_executar_pipeline  # noqa: PLC0415

    execucao = ExecucaoETL.objects.create(
        fonte=fonte,
        realm_destino=realm_destino,
        tipo_disparo=ExecucaoETL.TipoDisparo.MANUAL,
        observacao=observacao,
        disparado_por=disparado_por,
    )

    tarefa = task_identidade_executar_pipeline.apply_async(
        kwargs={"id_execucao": str(execucao.id_execucao)},
    )
    execucao.id_tarefa_celery = tarefa.id
    execucao.save(update_fields=["id_tarefa_celery"])

    logger.info(
        "Execução ETL %s disparada — fonte=%s, por=%s",
        execucao.id_execucao,
        execucao.fonte,
        disparado_por,
    )
    return execucao


@extend_schema(tags=["Execuções"])
class ExecucoesView(APIView):
    """Lista e cria execuções do pipeline ETL."""

    @extend_schema(
        summary="Lista execuções ETL",
        description=(
            "Retorna até 100 execuções do pipeline de identidade, com "
            "filtros opcionais por situação e fonte."
        ),
        parameters=[
            OpenApiParameter(
                "situacao", str, description="Filtra pela situação da execução"
            ),
            OpenApiParameter(
                "fonte",
                str,
                description=(
                    "Filtra pela fonte: todos | se1426 | coresso | eol_alunos"
                ),
            ),
        ],
        responses={200: ListarExecucaoETLSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        """Lista execuções com filtros opcionais por situação e fonte."""
        qs = ExecucaoETL.objects.all()
        situacao = request.query_params.get("situacao")
        fonte = request.query_params.get("fonte")
        if situacao:
            qs = qs.filter(situacao=situacao)
        if fonte:
            qs = qs.filter(fonte=fonte)
        dados = ListarExecucaoETLSerializer(qs[:100], many=True).data
        return Response(dados)

    @extend_schema(
        summary="Dispara execução completa ou por fonte",
        description=(
            "Cria uma ExecucaoETL e dispara o pipeline completo "
            "(task_identidade_executar_pipeline): extração paralela "
            "das fontes selecionadas seguida de resolução de identidade, "
            "provisionamento no Keycloak, carga de atributos no token-ms "
            "e registro operacional. Use `fonte=todos` (padrão) para "
            "executar todas as fontes ou uma fonte específica "
            "(`se1426`, `coresso`, `eol_alunos`) para execução parcial."
        ),
        request=CriarExecucaoETLSerializer,
        responses={201: ExecucaoETLSerializer},
    )
    def post(self, request: Request) -> Response:
        """Cria e dispara uma nova execução ETL."""
        serializador = CriarExecucaoETLSerializer(data=request.data)
        serializador.is_valid(raise_exception=True)

        execucao = _disparar_execucao(
            fonte=serializador.validated_data.get("fonte", "todos"),
            realm_destino=serializador.validated_data.get(
                "realm_destino", "sme-apps"
            ),
            observacao=serializador.validated_data.get("observacao", ""),
            disparado_por=request.META.get("HTTP_X_FORWARDED_USER", "api"),
        )
        return Response(
            ExecucaoETLSerializer(execucao).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Execuções"])
class DetalheExecucaoView(APIView):
    """Retorna detalhe de uma execução ETL."""

    @extend_schema(
        summary="Detalhe de execução",
        description=(
            "Retorna a ExecucaoETL identificada por `pk`, incluindo as "
            "etapas (LogEtapaETL) já registradas para o pipeline."
        ),
        responses={200: ExecucaoETLSerializer, 404: None},
    )
    def get(self, request: Request, pk: int) -> Response:
        """Retorna a execução com suas etapas."""
        try:
            execucao = ExecucaoETL.objects.get(pk=pk)
        except ExecucaoETL.DoesNotExist:
            return Response(
                {"detalhe": "Execução não encontrada."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ExecucaoETLSerializer(execucao).data)


@extend_schema(tags=["Execuções"])
class CancelarExecucaoView(APIView):
    """Cancela uma execução em andamento."""

    @extend_schema(
        summary="Cancela uma execução",
        description=(
            "Revoga a tarefa Celery associada e marca a ExecucaoETL como "
            "cancelada. Só é permitido para execuções pendentes ou em "
            "andamento."
        ),
        request=None,
        responses={200: ExecucaoETLSerializer, 400: None, 404: None},
    )
    def post(self, request: Request, pk: int) -> Response:
        """Revoga a tarefa Celery e marca como cancelada."""
        from config.celery import app as celery_app  # noqa: PLC0415

        try:
            execucao = ExecucaoETL.objects.get(pk=pk)
        except ExecucaoETL.DoesNotExist:
            return Response(
                {"detalhe": "Execução não encontrada."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if execucao.situacao not in (
            ExecucaoETL.Situacao.PENDENTE,
            ExecucaoETL.Situacao.EXECUTANDO,
        ):
            return Response(
                {
                    "detalhe": (
                        f"Execução com situação '{execucao.situacao}'"
                        " não pode ser cancelada."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if execucao.id_tarefa_celery:
            celery_app.control.revoke(
                execucao.id_tarefa_celery, terminate=True
            )

        execucao.situacao = ExecucaoETL.Situacao.CANCELADO
        execucao.finalizado_em = timezone.now()
        execucao.save(
            update_fields=["situacao", "finalizado_em", "atualizado_em"]
        )
        logger.info("Execução ETL %s cancelada.", execucao.id_execucao)
        return Response(ExecucaoETLSerializer(execucao).data)


@extend_schema(tags=["Provisionamento"])
class ControleProvisionamentoView(APIView):
    """Lista registros de controle de provisionamento."""

    @extend_schema(
        summary="Lista registros de provisionamento",
        description=(
            "Retorna até 200 registros de ControleProvisionamento "
            "(idempotência de provisionamento no Keycloak), com filtros "
            "opcionais por tipo de entidade, sistema de origem e situação "
            "ativa."
        ),
        parameters=[
            OpenApiParameter(
                "tipo_entidade",
                str,
                description="usuario | grupo | role | client",
            ),
            OpenApiParameter(
                "sistema_origem", str, description="se1426 | coresso"
            ),
            OpenApiParameter(
                "ativo", bool, description="Filtra registros ativos/inativos"
            ),
        ],
        responses={200: ControleProvisionamentoSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        """Lista com filtros opcionais."""
        qs = ControleProvisionamento.objects.all()
        tipo = request.query_params.get("tipo_entidade")
        sistema = request.query_params.get("sistema_origem")
        ativo = request.query_params.get("ativo")
        if tipo:
            qs = qs.filter(tipo_entidade=tipo)
        if sistema:
            qs = qs.filter(sistema_origem=sistema)
        if ativo is not None:
            qs = qs.filter(ativo=ativo.lower() == "true")
        dados = ControleProvisionamentoSerializer(qs[:200], many=True).data
        return Response(dados)


@extend_schema(tags=["Identidades"])
class ConsultaIdentidadeView(APIView):
    """Consulta o histórico de provisionamento de uma identidade."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Consulta identidade por CPF, RF ou e-mail",
        description=(
            "Endpoint público de leitura. Busca no histórico permanente "
            "de ControleProvisionamento (tipo_entidade=usuario) pelo "
            "identificador informado (apenas um por requisição: `cpf`, "
            "`rf` ou `email`), opcionalmente restringindo por "
            "`sistema_origem` (ex: se1426, coresso). Não dispara "
            "reprocessamento nem consulta as fontes externas — apenas o "
            "último estado conhecido de provisionamento."
        ),
        parameters=[
            OpenApiParameter(
                "cpf", str, description="CPF do usuário (com ou sem máscara)"
            ),
            OpenApiParameter(
                "rf", str, description="Registro funcional do usuário"
            ),
            OpenApiParameter(
                "email",
                str,
                description=(
                    "Não suportado: e-mail não é indexado no histórico de "
                    "provisionamento. Informar retorna 400."
                ),
            ),
            OpenApiParameter(
                "sistema_origem",
                str,
                description="Filtra pelo sistema de origem: se1426 | coresso",
            ),
        ],
        responses={
            200: ControleProvisionamentoSerializer(many=True),
            400: None,
        },
    )
    def get(self, request: Request) -> Response:
        """Busca registros de provisionamento por cpf, rf ou email."""
        cpf = request.query_params.get("cpf")
        rf = request.query_params.get("rf")
        email = request.query_params.get("email")
        sistema_origem = request.query_params.get("sistema_origem")

        if email and not cpf and not rf:
            return Response(
                {
                    "detalhe": (
                        "Busca por e-mail não é suportada: o histórico de "
                        "provisionamento (ControleProvisionamento) indexa "
                        "apenas CPF, RF ou matrícula. Informe `cpf` ou `rf`."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not cpf and not rf:
            return Response(
                {"detalhe": "Informe ao menos um identificador: cpf ou rf."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = ControleProvisionamento.objects.filter(
            tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
        )
        if sistema_origem:
            qs = qs.filter(sistema_origem=sistema_origem)

        if cpf:
            cpf_normalizado = "".join(c for c in cpf if c.isdigit())
            qs = qs.filter(id_origem=cpf_normalizado)
        elif rf:
            qs = qs.filter(id_origem=rf.strip())

        dados = ControleProvisionamentoSerializer(qs, many=True).data
        return Response(dados)


@extend_schema(tags=["Monitoramento"])
class MarcaDaguaView(APIView):
    """Lista os watermarks por fonte."""

    @extend_schema(
        summary="Lista watermarks de extração",
        description=(
            "Retorna o estado de watermark (MarcaDaguaExtracao) de cada "
            "fonte: último timestamp processado, última página e total "
            "acumulado."
        ),
        responses={200: MarcaDaguaExtracaoSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        """Retorna todos os watermarks."""
        dados = MarcaDaguaExtracaoSerializer(
            MarcaDaguaExtracao.objects.all(), many=True
        ).data
        return Response(dados)


@extend_schema(tags=["Monitoramento"])
class ResetarMarcaDaguaView(APIView):
    """Reseta o watermark de uma fonte para reprocessamento completo."""

    @extend_schema(
        summary="Reseta o watermark de uma fonte",
        description=(
            "Zera `ultimo_processado_em` e `ultima_pagina` da fonte "
            "informada, forçando reprocessamento completo na próxima "
            "execução."
        ),
        request=None,
        responses={200: MarcaDaguaExtracaoSerializer},
    )
    def post(self, request: Request, fonte: str) -> Response:
        """Zera ultimo_processado_em e ultima_pagina da fonte."""
        marca, _ = MarcaDaguaExtracao.objects.get_or_create(fonte=fonte)
        marca.ultimo_processado_em = None
        marca.ultima_pagina = 0
        marca.save(
            update_fields=[
                "ultimo_processado_em",
                "ultima_pagina",
                "atualizado_em",
            ]
        )
        logger.info("Watermark da fonte '%s' resetado.", fonte)
        return Response(MarcaDaguaExtracaoSerializer(marca).data)


@extend_schema(tags=["Checkpoints"])
class CheckpointsView(APIView):
    """Lista checkpoints de execuções."""

    @extend_schema(
        summary="Lista checkpoints de retomada",
        description=(
            "Retorna até 100 checkpoints (CheckpointEtl), com filtro "
            "opcional por `id_execucao`."
        ),
        parameters=[
            OpenApiParameter(
                "id_execucao", str, description="UUID da ExecucaoETL"
            ),
        ],
        responses={200: CheckpointEtlSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        """Retorna checkpoints com filtro opcional por execução."""
        qs = CheckpointEtl.objects.all()
        id_execucao = request.query_params.get("id_execucao")
        if id_execucao:
            qs = qs.filter(id_execucao=id_execucao)
        dados = CheckpointEtlSerializer(qs[:100], many=True).data
        return Response(dados)


@extend_schema(tags=["Execuções"])
class TentativasView(APIView):
    """Lista rastreios de tentativas por execução."""

    @extend_schema(
        summary="Lista tentativas de tarefas",
        description=(
            "Retorna até 200 registros de RastreioTentativa, com filtros "
            "opcionais por `id_execucao` e `nome_tarefa`."
        ),
        parameters=[
            OpenApiParameter(
                "id_execucao", str, description="UUID da ExecucaoETL"
            ),
            OpenApiParameter(
                "nome_tarefa", str, description="Nome da task Celery"
            ),
        ],
        responses={200: RastreioTentativaSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        """Retorna tentativas com filtros opcionais."""
        qs = RastreioTentativa.objects.all()
        id_execucao = request.query_params.get("id_execucao")
        nome_tarefa = request.query_params.get("nome_tarefa")
        if id_execucao:
            qs = qs.filter(id_execucao=id_execucao)
        if nome_tarefa:
            qs = qs.filter(nome_tarefa=nome_tarefa)
        dados = RastreioTentativaSerializer(
            qs.order_by("-iniciado_em")[:200], many=True
        ).data
        return Response(dados)


@extend_schema(tags=["Monitoramento"])
class ResumoExecucoesView(APIView):
    """Resumo público com a última execução por fonte."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Última execução por fonte",
        description=(
            "Endpoint público. Retorna a execução mais recente de cada "
            "fonte ETL (todos, se1426, coresso, eol_alunos), útil para "
            "visualizar rapidamente o estado atual de cada pipeline."
        ),
        responses={200: ListarExecucaoETLSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        """Retorna a última execução de cada fonte."""
        serializer = ListarExecucaoETLSerializer(
            _qs_ultima_execucao_por_fonte(), many=True
        )
        return Response(serializer.data)


@extend_schema(tags=["Health"])
class HealthCheckView(APIView):
    """Health check público do serviço de ETL de identidade."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Health check",
        description=(
            "Endpoint público. Verifica a conectividade com o banco de "
            "dados default (SYNC_REC_DB). Retorna 200 quando saudável e "
            "503 quando indisponível."
        ),
        responses={200: HealthStatusSerializer, 503: HealthStatusSerializer},
    )
    def get(self, request: Request) -> Response:
        """Retorna o status de saúde do serviço."""
        resultado = self._check_database()
        status_http = (
            status.HTTP_200_OK
            if resultado["status"] == "healthy"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        serializer = HealthStatusSerializer(resultado)
        return Response(serializer.data, status=status_http)

    def _check_database(self) -> dict[str, str]:
        """Retorna se está conectado ao banco de dados default."""
        from django.db import connections  # noqa: PLC0415

        try:
            with connections["default"].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return {"status": "healthy"}
        except Exception:
            return {"status": "unhealthy"}


@extend_schema(tags=["Execuções"])
@api_view(["GET"])
def estatisticas(request: Request) -> Response:
    """Retorna estatísticas agregadas das últimas 30 execuções."""
    ultimos_30_dias = timezone.now() - timezone.timedelta(days=30)
    execucoes = ExecucaoETL.objects.filter(criado_em__gte=ultimos_30_dias)
    totais = execucoes.aggregate(
        total_execucoes=Count("id"),
        total_extraido=Sum("total_extraido"),
        total_carregado=Sum("total_carregado"),
        total_erros=Sum("total_erros"),
    )
    por_situacao = dict(
        execucoes.values_list("situacao")
        .annotate(total=Count("id"))
        .values_list("situacao", "total")
    )
    provisionamento = ControleProvisionamento.objects.aggregate(
        total=Count("id"),
        ativos=Count(
            "id",
            filter=__import__("django.db.models", fromlist=["Q"]).Q(
                ativo=True
            ),
        ),
    )
    return Response(
        {
            "periodo": "ultimos_30_dias",
            "execucoes": {
                "total": totais["total_execucoes"],
                "por_situacao": por_situacao,
                "total_extraido": totais["total_extraido"] or 0,
                "total_carregado": totais["total_carregado"] or 0,
                "total_erros": totais["total_erros"] or 0,
            },
            "provisionamento": provisionamento,
            "timestamp": timezone.now().isoformat(),
        }
    )


@api_view(["POST"])
def extrair_sistemas(request: Request) -> Response:
    """Dispara extração síncrona de sistemas do CoreSSO."""
    from apps.extracao.tasks import extrair_sistemas_coresso  # noqa: PLC0415

    try:
        total = extrair_sistemas_coresso(id_execucao=None)
    except Exception as exc:
        logger.exception("Falha na extração de sistemas: %s", exc)
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response({"total_extraido": total})


@api_view(["POST"])
def provisionar_sistemas(request: Request) -> Response:
    """Provisiona sistemas do CoreSSO como clients no Keycloak."""
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        obter_admin_keycloak,
        provisionar_client_kc,
    )
    from apps.staging.models import SistemaStaging  # noqa: PLC0415

    corpo = request.data or {}
    realm = corpo.get("realm") or None
    sigla = corpo.get("sigla")

    qs = SistemaStaging.objects.filter(situacao=1)
    if sigla:
        qs = qs.filter(sigla=sigla)

    admin = obter_admin_keycloak(realm=realm)
    criados, atualizados, erros = [], [], []
    for sistema in qs:
        try:
            resultado = provisionar_client_kc(admin, sistema, realm=realm)
            (criados if resultado["acao"] == "criado" else atualizados).append(
                resultado
            )
        except Exception as exc:
            logger.exception(
                "Falha ao provisionar client %s: %s", sistema.sigla, exc
            )
            sistema.situacao_provisionamento = (
                SistemaStaging.SituacaoProvisionamento.ERRO
            )
            sistema.detalhe_erro = str(exc)
            sistema.save(
                update_fields=[
                    "situacao_provisionamento",
                    "detalhe_erro",
                    "atualizado_em",
                ]
            )
            erros.append({"sistema": sistema.nome, "erro": str(exc)})

    return Response(
        {
            "criados": len(criados),
            "atualizados": len(atualizados),
            "erros": erros,
        }
    )


@api_view(["GET"])
def listar_sistemas(request: Request) -> Response:
    """Lista sistemas CoreSSO sincronizados."""
    from apps.staging.models import SistemaStaging  # noqa: PLC0415

    return Response(
        [
            {
                "coresso_sis_id": s.coresso_sis_id,
                "nome": s.nome,
                "sigla": s.sigla,
                "kc_client_id": s.kc_client_id,
                "kc_realm": s.kc_realm,
                "situacao_provisionamento": s.situacao_provisionamento,
            }
            for s in SistemaStaging.objects.all().order_by("coresso_sis_id")
        ]
    )


@api_view(["POST"])
def extrair_perfis(request: Request) -> Response:
    """Dispara extração síncrona de perfis do CoreSSO."""
    from apps.extracao.tasks import extrair_perfis_coresso  # noqa: PLC0415

    try:
        total = extrair_perfis_coresso(id_execucao=None)
    except Exception as exc:
        logger.exception("Falha na extração de perfis: %s", exc)
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response({"total_extraido": total})


@api_view(["POST"])
def provisionar_perfis(request: Request) -> Response:
    """Provisiona perfis CoreSSO como client roles no Keycloak."""
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        obter_admin_keycloak,
        provisionar_role_client_kc,
    )
    from apps.staging.models import PerfilCoressoStaging  # noqa: PLC0415

    corpo = request.data or {}
    realm = corpo.get("realm") or None
    sis_id = corpo.get("coresso_sis_id")

    qs = PerfilCoressoStaging.objects.select_related("sistema").all()
    if sis_id:
        qs = qs.filter(coresso_sis_id=int(sis_id))

    admin = obter_admin_keycloak(realm=realm)
    criados = atualizados = ignorados = erros = 0
    detalhes_erro: list[dict] = []

    for idx, perfil in enumerate(qs.iterator()):
        if idx and idx % 50 == 0:
            with contextlib.suppress(Exception):
                admin = obter_admin_keycloak(realm=realm)
        try:
            resultado = provisionar_role_client_kc(admin, perfil)
            if resultado["acao"] == "criado":
                criados += 1
            elif resultado["acao"] == "atualizado":
                atualizados += 1
            else:
                ignorados += 1
        except Exception as exc:
            erros += 1
            detalhes_erro.append(
                {"perfil": perfil.nome, "erro": str(exc)[:200]}
            )
            perfil.situacao_provisionamento = (
                PerfilCoressoStaging.SituacaoProvisionamento.ERRO
            )
            perfil.detalhe_erro = str(exc)
            perfil.save(
                update_fields=[
                    "situacao_provisionamento",
                    "detalhe_erro",
                    "atualizado_em",
                ]
            )

    return Response(
        {
            "criados": criados,
            "atualizados": atualizados,
            "ignorados": ignorados,
            "erros": erros,
            "detalhes_erro": detalhes_erro[:20],
        }
    )


@api_view(["GET"])
def listar_perfis(request: Request) -> Response:
    """Lista perfis CoreSSO sincronizados."""
    from apps.staging.models import PerfilCoressoStaging  # noqa: PLC0415

    sis_id = request.query_params.get("coresso_sis_id")
    qs = PerfilCoressoStaging.objects.select_related("sistema")
    if sis_id:
        qs = qs.filter(coresso_sis_id=int(sis_id))
    return Response(
        [
            {
                "coresso_gru_id": p.coresso_gru_id,
                "nome": p.nome,
                "coresso_sis_id": p.coresso_sis_id,
                "kc_role_nome": p.kc_role_nome,
                "kc_role_id": p.kc_role_id,
                "situacao_provisionamento": p.situacao_provisionamento,
            }
            for p in qs.order_by("coresso_sis_id", "nome")[:1000]
        ]
    )


# ---------------------------------------------------------------------------
# Dashboard e Kanban (HTML)
# ---------------------------------------------------------------------------


class DispararExecucaoDashboardView(View):
    """Dispara uma execução a partir do formulário do dashboard HTML.

    Diferente de `ExecucoesView.post` (API autenticada por API Key), este
    endpoint é acionado pelo próprio formulário renderizado em
    `/dashboard/` e redireciona de volta para a página após o disparo.
    """

    def post(self, request: HttpRequest) -> HttpResponseRedirect:
        """Cria e dispara a execução, redirecionando para o dashboard."""
        fonte = request.POST.get("fonte", "todos")
        if fonte not in _FONTES_VALIDAS:
            fonte = "todos"

        usuario = getattr(request, "user", None)
        disparado_por = getattr(usuario, "username", None) or "dashboard"
        _disparar_execucao(fonte=fonte, disparado_por=disparado_por)
        return HttpResponseRedirect("/dashboard/")


class DashboardView(View):
    """Dashboard público de monitoramento das execuções ETL."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Renderiza o dashboard com resumo por fonte."""
        fonte = request.GET.get("fonte", "")
        data_inicio = request.GET.get("data_inicio", "")
        data_fim = request.GET.get("data_fim", "")
        situacao = request.GET.get("situacao", "")

        ultima_por_fonte = list(_qs_ultima_execucao_por_fonte())

        qs_filtrado = _aplicar_filtros_execucao(
            qs=ExecucaoETL.objects.all(),
            fonte=fonte,
            data_inicio=data_inicio,
            data_fim=data_fim,
            situacao=situacao,
        ).order_by("-criado_em")

        execucoes = qs_filtrado[:_LIMITE_DASHBOARD]

        # Últimas 10 execuções com etapas
        ultimas_10 = list(qs_filtrado.prefetch_related("etapas")[:10])

        situacoes_disponiveis = (
            ExecucaoETL.objects.values_list("situacao", flat=True)
            .distinct()
            .order_by("situacao")
        )

        return render(
            request,
            "dashboard.html",
            {
                "ultima_por_fonte": ultima_por_fonte,
                "execucoes": execucoes,
                "ultimas_10": ultimas_10,
                "fontes": _FONTES_VALIDAS,
                "situacoes": situacoes_disponiveis,
                "filtros": {
                    "fonte": fonte,
                    "data_inicio": data_inicio,
                    "data_fim": data_fim,
                    "situacao": situacao,
                },
            },
        )


def _resolver_execucoes_kanban(
    qs_base: QuerySet,
    id_execucao_filtro: str,
    fonte_filtro: str,
    execucoes_disponiveis: list,
) -> tuple[list, str]:
    """Resolve execuções a renderizar no kanban."""
    if not id_execucao_filtro:
        ultima = list(_qs_ultima_execucao_por_fonte())
        if fonte_filtro:
            ultima = [e for e in ultima if e.fonte == fonte_filtro]
        return ultima, "Nenhuma execução encontrada."

    try:
        selecionada = qs_base.get(id_execucao=id_execucao_filtro)
    except (ExecucaoETL.DoesNotExist, ValueError):
        selecionada = None

    if selecionada is None:
        return [], "Execução não encontrada para os filtros aplicados."

    if not any(
        e.id_execucao == selecionada.id_execucao for e in execucoes_disponiveis
    ):
        execucoes_disponiveis.insert(0, selecionada)
    return [selecionada], "Nenhuma execução encontrada."


class KanbanView(View):
    """Kanban de processamento ETL por fonte, com etapas do pipeline."""

    def get(self, request: HttpRequest) -> HttpResponse:
        """Renderiza kanban com as etapas (LogEtapaETL) de cada execução."""
        fonte_filtro = request.GET.get("fonte", "")
        id_execucao_filtro = request.GET.get("id_execucao", "").strip()

        qs_execucoes_select = ExecucaoETL.objects.all()
        if fonte_filtro:
            qs_execucoes_select = qs_execucoes_select.filter(
                fonte=fonte_filtro
            )

        execucoes_disponiveis = list(
            qs_execucoes_select.order_by("-criado_em")[
                :_LIMITE_EXECUCOES_RECENTES
            ]
        )

        ultima_por_fonte, mensagem_kanban_vazio = _resolver_execucoes_kanban(
            qs_execucoes_select,
            id_execucao_filtro,
            fonte_filtro,
            execucoes_disponiveis,
        )

        ids_execucao = [e.id_execucao for e in ultima_por_fonte]
        etapas_por_execucao = {}
        for etapa in LogEtapaETL.objects.filter(
            execucao__id_execucao__in=ids_execucao
        ).order_by("ordem_etapa"):
            etapas_por_execucao.setdefault(
                str(etapa.execucao.id_execucao), []
            ).append(etapa)

        fontes_kanban = []
        for exec_obj in ultima_por_fonte:
            key = str(exec_obj.id_execucao)
            etapas = etapas_por_execucao.get(key, [])
            fontes_kanban.append(
                {
                    "exec": exec_obj,
                    "etapas": etapas,
                    "total_entrada": sum(e.registros_entrada for e in etapas),
                    "total_saida": sum(e.registros_saida for e in etapas),
                    "total_erro": sum(e.registros_erro for e in etapas),
                }
            )

        todas_fontes = list(
            ExecucaoETL.objects.values_list("fonte", flat=True)
            .distinct()
            .order_by("fonte")
        )

        return render(
            request,
            "kanban.html",
            {
                "fontes_kanban": fontes_kanban,
                "fonte_filtro": fonte_filtro,
                "id_execucao_filtro": id_execucao_filtro,
                "execucoes_disponiveis": execucoes_disponiveis,
                "todas_fontes": todas_fontes,
                "mensagem_kanban_vazio": mensagem_kanban_vazio,
            },
        )
