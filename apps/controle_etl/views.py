"""Views da API de controle do pipeline ETL de identidades."""

import contextlib
import logging
import uuid
from typing import Any

from django.conf import settings
from django.db.models import Count, OuterRef, QuerySet, Subquery, Sum
from django.http import HttpRequest, HttpResponse
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
    ConcederAcessoSerializer,
    ControleProvisionamentoSerializer,
    CriarExecucaoETLSerializer,
    CriarUsuarioManualSerializer,
    ExecucaoETLSerializer,
    ExtrairVinculosSerializer,
    HealthStatusSerializer,
    IdentidadeKeycloakSerializer,
    ListarExecucaoETLSerializer,
    MarcaDaguaExtracaoSerializer,
    PipelineSistemaSerializer,
    ProvisionarPerfisSerializer,
    ProvisionarSistemasSerializer,
    ProvisionarVinculosSerializer,
    RastreioTentativaSerializer,
    SincronizarUsuarioSerializer,
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
    realm_destino: str = settings.KEYCLOAK_REALM,
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
    """Lista execuções do pipeline ETL e dispara novas execuções.

    O `POST` é o ponto de entrada usado tanto pela API externa quanto
    pelo botão de disparo manual no dashboard HTML (via `_disparar_execucao`),
    por isso a criação da `ExecucaoETL` e o `apply_async` ficam centralizados
    nessa função auxiliar em vez de duplicados entre os dois consumidores.
    """

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
                "realm_destino", settings.KEYCLOAK_REALM
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
    """Detalha uma execução específica, incluindo suas etapas.

    Usada pelo dashboard e por integrações externas para acompanhar o
    progresso de uma `ExecucaoETL` já disparada, expondo as etapas
    (`LogEtapaETL`) registradas até o momento da consulta.
    """

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
    """Cancela uma execução pendente ou em andamento.

    Revoga a tarefa Celery associada (`terminate=True`) e marca a
    `ExecucaoETL` como cancelada. Execuções já finalizadas (sucesso, falha
    ou cancelamento anterior) são rejeitadas com 400, pois revogar uma
    tarefa que já terminou não tem efeito e mascararia o estado real.
    """

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
    """Lista o histórico de idempotência de provisionamento no Keycloak.

    `ControleProvisionamento` é a fonte de verdade sobre o que já foi
    criado/atualizado no Keycloak (usuário, grupo, role ou client),
    usada pelo orquestrador para evitar reprocessar entidades já
    provisionadas nesta ou em execuções anteriores.
    """

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
    """Consulta o estado real de uma identidade direto no Keycloak.

    Busca a conta por RF, CPF ou e-mail via Keycloak Admin API
    (mesma lógica de deduplicação usada no upsert,
    ``_buscar_todas_contas_kc``) — reflete o estado atual do
    Keycloak, independentemente de qual caminho de upsert criou o
    usuário (pipeline, ``usuario/criar/``, ``usuario/sincronizar/``
    ou ``usuario/conceder-acesso/``). Exige autenticação por API Key,
    já que cada consulta gera uma chamada real ao Keycloak.
    """

    @extend_schema(
        summary="Consulta identidade por CPF, RF ou e-mail",
        description=(
            "Busca a conta do usuário diretamente no Keycloak, por RF, "
            "CPF ou e-mail (ao menos um obrigatório). Retorna todas as "
            "contas encontradas — normalmente uma, mas pode haver mais "
            "de uma se existirem contas duplicadas ainda não mescladas."
        ),
        parameters=[
            OpenApiParameter(
                "cpf", str, description="CPF do usuário (com ou sem máscara)"
            ),
            OpenApiParameter(
                "rf", str, description="Registro funcional do usuário"
            ),
            OpenApiParameter("email", str, description="E-mail do usuário"),
            OpenApiParameter(
                "realm",
                str,
                description="Realm Keycloak (padrão: KEYCLOAK_REALM)",
            ),
        ],
        responses={
            200: IdentidadeKeycloakSerializer(many=True),
            400: None,
            502: None,
        },
    )
    def get(self, request: Request) -> Response:
        """Busca a conta do usuário direto no Keycloak."""
        from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
            _buscar_todas_contas_kc,
            obter_admin_keycloak,
        )

        cpf_bruto = request.query_params.get("cpf") or ""
        cpf = "".join(c for c in cpf_bruto if c.isdigit())
        rf = (request.query_params.get("rf") or "").strip()
        email = (request.query_params.get("email") or "").strip()
        realm = request.query_params.get("realm") or settings.KEYCLOAK_REALM

        if not cpf and not rf and not email:
            return Response(
                {
                    "detalhe": (
                        "Informe ao menos um identificador:"
                        " cpf, rf ou email."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            admin = obter_admin_keycloak(realm=realm)
            contas = _buscar_todas_contas_kc(admin, rf, cpf, email)
        except Exception as exc:
            logger.exception("Falha ao consultar identidade no KC: %s", exc)
            return Response(
                {"detalhe": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        base = settings.KEYCLOAK_URL_SERVIDOR.rstrip("/")
        dados = [
            {
                "kc_user_id": str(conta.get("id", "")),
                "username": conta.get("username", ""),
                "nome": " ".join(
                    filter(
                        None,
                        [conta.get("firstName"), conta.get("lastName")],
                    )
                ),
                "email": conta.get("email"),
                "ativo": bool(conta.get("enabled")),
                "cpf": (conta.get("attributes", {}).get("cpf") or [None])[0],
                "rf": (conta.get("attributes", {}).get("rf") or [None])[0],
                "kc_url": (
                    f"{base}/admin/master/console/"
                    f"#/{realm}/users/{conta.get('id')}/settings"
                ),
            }
            for conta in contas
        ]
        return Response(dados)


@extend_schema(tags=["Monitoramento"])
class MarcaDaguaView(APIView):
    """Lista o watermark de extração de cada fonte.

    O watermark (`MarcaDaguaExtracao`) marca até onde cada fonte
    (SE1426, CoreSSO, EOL Alunos) já foi extraída, permitindo que a
    próxima execução retome de forma incremental em vez de reprocessar
    tudo desde o início.
    """

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
    """Força reprocessamento completo de uma fonte na próxima extração.

    Zera o watermark (`ultimo_processado_em`/`ultima_pagina`) em vez de
    apagar o registro, para manter o histórico do restante do modelo
    (`MarcaDaguaExtracao`) intacto. Uso típico: recuperação após
    inconsistência detectada na fonte de origem.
    """

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
    """Lista pontos de retomada gravados durante uma execução.

    `CheckpointEtl` guarda o progresso intermediário de uma `ExecucaoETL`
    para permitir retomar a partir do último ponto salvo em vez de
    reiniciar o pipeline do zero após uma falha ou interrupção.
    """

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
    """Lista o histórico de tentativas de cada task Celery do pipeline.

    `RastreioTentativa` registra cada tentativa (inclusive retries) de
    uma task dentro de uma `ExecucaoETL`, o que é útil para diagnosticar
    lentidão ou falhas intermitentes em uma etapa específica do pipeline.
    """

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
    """Resumo público do estado mais recente de cada fonte ETL.

    Pensado para telas externas de status (sem autenticação, `AllowAny`)
    que precisam de uma visão rápida "está tudo rodando?" sem consultar
    o histórico completo de execuções.
    """

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
    """Health check público consultado por orquestradores externos.

    Verifica apenas a conectividade com o banco `default` (SYNC_REC_DB) —
    não valida Keycloak, Celery/KeyDB nem as fontes externas — porque é
    o dado mínimo necessário para orquestradores de infraestrutura
    decidirem se o serviço está apto a receber tráfego.
    """

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
    from datetime import timedelta  # noqa: PLC0415

    ultimos_30_dias = timezone.now() - timedelta(days=30)
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


@extend_schema(tags=["Sistemas"])
@api_view(["POST"])
def extrair_sistemas(request: Request) -> Response:
    """Dispara extração síncrona de sistemas do CoreSSO."""
    from apps.extracao.tasks import extrair_sistemas_coresso  # noqa: PLC0415

    try:
        total = extrair_sistemas_coresso()
    except Exception as exc:
        logger.exception("Falha na extração de sistemas: %s", exc)
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response({"total_extraido": total})


@extend_schema(
    tags=["Sistemas"],
    request=ProvisionarSistemasSerializer,
)
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
    sis_id = corpo.get("coresso_sis_id")

    qs = SistemaStaging.objects.filter(situacao=1)
    if sis_id:
        qs = qs.filter(coresso_sis_id=int(sis_id))
    elif sigla:
        qs = qs.filter(sigla=sigla)

    admin = obter_admin_keycloak(realm=realm)
    criados: list[dict] = []
    atualizados: list[dict] = []
    erros: list[dict] = []
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


@extend_schema(tags=["Sistemas"])
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


@extend_schema(tags=["Perfis"])
@api_view(["POST"])
def extrair_perfis(request: Request) -> Response:
    """Dispara extração síncrona de perfis do CoreSSO."""
    from apps.extracao.tasks import extrair_perfis_coresso  # noqa: PLC0415

    try:
        total = extrair_perfis_coresso()
    except Exception as exc:
        logger.exception("Falha na extração de perfis: %s", exc)
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response({"total_extraido": total})


@extend_schema(
    tags=["Perfis"],
    request=ProvisionarPerfisSerializer,
)
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
    gru_id = corpo.get("coresso_gru_id")

    qs = PerfilCoressoStaging.objects.select_related("sistema").all()
    if sis_id:
        qs = qs.filter(coresso_sis_id=int(sis_id))
    if gru_id:
        qs = qs.filter(coresso_gru_id=str(gru_id))

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


@extend_schema(tags=["Perfis"])
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
# Vínculos usuário↔grupo CoreSSO → client roles Keycloak
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Vínculos"],
    request=ExtrairVinculosSerializer,
)
@api_view(["POST"])
def extrair_vinculos(request: Request) -> Response:
    """Extrai vínculos usuário↔grupo do CoreSSO (contagem)."""
    from apps.extracao.tasks import (  # noqa: PLC0415
        extrair_vinculos_usuario_grupo_coresso,
    )

    corpo = request.data or {}
    sis_id = corpo.get("coresso_sis_id")
    gru_id = corpo.get("coresso_gru_id")

    try:
        total = sum(
            1
            for _ in extrair_vinculos_usuario_grupo_coresso(
                sis_id=int(sis_id) if sis_id else None,
                gru_id=gru_id or None,
            )
        )
    except Exception as exc:
        logger.exception("Falha na extração de vínculos: %s", exc)
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response({"total_extraido": total})


@extend_schema(
    tags=["Vínculos"],
    request=ProvisionarVinculosSerializer,
)
@api_view(["POST"])
def provisionar_vinculos(request: Request) -> Response:
    """Atribui client roles aos usuários no Keycloak."""
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        atribuir_client_roles_usuario_kc,
        obter_admin_keycloak,
    )
    from apps.extracao.tasks import (  # noqa: PLC0415
        extrair_vinculos_usuario_grupo_coresso,
    )

    corpo = request.data or {}
    realm = corpo.get("realm") or None
    sis_id = corpo.get("coresso_sis_id")
    gru_id = corpo.get("coresso_gru_id")

    try:
        admin = obter_admin_keycloak(realm=realm)
        vinculos = extrair_vinculos_usuario_grupo_coresso(
            sis_id=int(sis_id) if sis_id else None,
            gru_id=gru_id or None,
        )
        resultado = atribuir_client_roles_usuario_kc(admin, vinculos)
    except Exception as exc:
        logger.exception("Falha no provisionamento de vínculos: %s", exc)
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response(resultado)


@extend_schema(tags=["Vínculos"])
@api_view(["GET"])
def listar_vinculos(request: Request) -> Response:
    """Resumo de perfis provisionados por sistema."""
    from apps.staging.models import (  # noqa: PLC0415
        PerfilCoressoStaging,
        SistemaStaging,
    )

    sistemas = SistemaStaging.objects.filter(situacao=1).order_by(
        "coresso_sis_id"
    )
    dados = []
    for s in sistemas:
        perfis = PerfilCoressoStaging.objects.filter(
            coresso_sis_id=s.coresso_sis_id
        )
        dados.append(
            {
                "coresso_sis_id": s.coresso_sis_id,
                "nome": s.nome,
                "kc_client_id": s.kc_client_id,
                "total_perfis": perfis.count(),
                "provisionados": perfis.filter(
                    situacao_provisionamento="provisionado"
                ).count(),
            }
        )
    return Response(dados)


# ---------------------------------------------------------------------------
# Criação manual de usuário (sem vínculo prévio no CoreSSO)
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Usuário"],
    request=CriarUsuarioManualSerializer,
)
@api_view(["POST"])
def criar_usuario_manual(request: Request) -> Response:
    """Cria ou atualiza um usuário no Keycloak a partir de dados diretos.

    Diferente de ``sincronizar_usuario``/``conceder_acesso``, não
    depende do usuário já existir no CoreSSO — os dados vêm da
    própria requisição. É materializado como registro de staging
    (``fonte="api_manual"``) e provisionado pelo mesmo caminho
    idempotente usado pelo pipeline (``provisionar_usuario_kc``).
    """
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        _conceder_roles_sistema_kc,
        obter_admin_keycloak,
        provisionar_usuario_kc,
    )
    from apps.staging.models import (  # noqa: PLC0415
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    serializer = CriarUsuarioManualSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    dados = serializer.validated_data

    modelo: type[Any] = {
        "servidor": UsuarioServidorStaging,
        "aluno": UsuarioAlunoStaging,
        "terceiro": UsuarioTerceiroStaging,
    }[dados["tipo_usuario"]]

    realm = dados.get("realm") or settings.KEYCLOAK_REALM
    cpf = dados.get("cpf", "").strip()
    rf = dados.get("rf", "").strip()

    campos_busca = {"fonte": "api_manual"}
    if cpf:
        campos_busca["cpf"] = cpf
    if rf and modelo is UsuarioServidorStaging:
        campos_busca["rf"] = rf

    valores_padrao = {
        "id_execucao": uuid.uuid4(),
        "nome": dados["nome"],
        "email": dados.get("email", ""),
        "situacao": "ativo",
    }
    if not cpf and rf:
        valores_padrao["cpf"] = ""
    if modelo is UsuarioServidorStaging and rf:
        valores_padrao["rf"] = rf

    try:
        usuario, _ = modelo.objects.update_or_create(
            defaults=valores_padrao,
            **campos_busca,
        )
    except Exception as exc:
        logger.exception("Falha ao materializar usuário manual: %s", exc)
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        admin = obter_admin_keycloak(realm=realm)
        resultado = provisionar_usuario_kc(admin, usuario, realm=realm)
    except Exception as exc:
        logger.exception(
            "Falha ao provisionar usuário manual %s: %s",
            usuario.id,
            exc,
        )
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    sis_id = dados.get("sistema")
    nomes_roles = dados.get("roles")
    if sis_id and nomes_roles:
        try:
            resultado_roles = _conceder_roles_sistema_kc(
                admin,
                resultado["kc_user_id"],
                sis_id,
                nomes_roles,
                username=usuario.nome,
            )
        except Exception as exc:
            logger.exception(
                "Falha ao conceder acesso ao usuário manual %s: %s",
                usuario.id,
                exc,
            )
            return Response(
                {**resultado, "erro_acesso": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        resultado = {**resultado, **resultado_roles}

    return Response(resultado)


# ---------------------------------------------------------------------------
# Sincronização individual de usuário
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Usuário"],
    request=SincronizarUsuarioSerializer,
)
@api_view(["POST"])
def sincronizar_usuario(request: Request) -> Response:
    """Sincroniza um usuário no Keycloak com todos os seus roles.

    Busca o usuário no CoreSSO por RF, CPF ou email,
    cria/atualiza no Keycloak e atribui todos os client
    roles dos sistemas aos quais pertence.
    """
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        obter_admin_keycloak,
        sincronizar_usuario_kc,
    )
    from apps.extracao.tasks import (  # noqa: PLC0415
        buscar_dados_usuario_coresso,
    )

    corpo = request.data or {}
    identificador = corpo.get("identificador", "").strip()
    realm = corpo.get("realm") or settings.KEYCLOAK_REALM

    if not identificador:
        return Response(
            {"detalhe": "identificador é obrigatório (RF, CPF ou email)."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        dados = buscar_dados_usuario_coresso(identificador)
    except Exception as exc:
        logger.exception(
            "Falha ao buscar usuário %s: %s",
            identificador,
            exc,
        )
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if not dados:
        return Response(
            {
                "detalhe": (
                    f"Usuário '{identificador}'" " não encontrado no CoreSSO."
                )
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        admin = obter_admin_keycloak(realm=realm)
        resultado = sincronizar_usuario_kc(admin, dados, realm=realm)
    except Exception as exc:
        logger.exception(
            "Falha ao sincronizar %s: %s",
            identificador,
            exc,
        )
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(resultado)


# ---------------------------------------------------------------------------
# Concessão de acesso a sistema + roles
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Usuário"],
    request=ConcederAcessoSerializer,
)
@api_view(["POST"])
def conceder_acesso(request: Request) -> Response:
    """Concede acesso a um sistema e roles no Keycloak.

    Busca o usuário no CoreSSO, cria/atualiza no Keycloak
    e atribui os client roles informados.
    """
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        conceder_acesso_kc,
        obter_admin_keycloak,
    )
    from apps.extracao.tasks import (  # noqa: PLC0415
        buscar_dados_usuario_coresso,
    )

    serializer = ConcederAcessoSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    dados_validados = serializer.validated_data

    identificador = dados_validados["identificador"].strip()
    sis_id = dados_validados["sistema"]
    nomes_roles = dados_validados["roles"]
    realm = dados_validados.get("realm") or settings.KEYCLOAK_REALM

    try:
        dados = buscar_dados_usuario_coresso(identificador)
    except Exception as exc:
        logger.exception(
            "Falha ao buscar usuário %s: %s",
            identificador,
            exc,
        )
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if not dados:
        return Response(
            {
                "detalhe": (
                    f"Usuário '{identificador}'" " não encontrado no CoreSSO."
                )
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        admin = obter_admin_keycloak(realm=realm)
        resultado = conceder_acesso_kc(
            admin, dados, sis_id, nomes_roles, realm=realm
        )
    except Exception as exc:
        logger.exception(
            "Falha ao conceder acesso %s: %s",
            identificador,
            exc,
        )
        return Response(
            {"detalhe": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(resultado)


# ---------------------------------------------------------------------------
# Pipeline completo por sistema (extração + clients + roles + vínculos)
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Pipeline"],
    request=PipelineSistemaSerializer,
)
@api_view(["POST"])
def executar_pipeline_sistema(request: Request) -> Response:
    """Executa o pipeline completo para um sistema e/ou grupo.

    Etapas: extrair sistemas → provisionar client →
    extrair perfis → provisionar roles → atribuir vínculos.

    Body (JSON):
        coresso_sis_id: int (obrigatório)
        coresso_gru_id: str (opcional)
        realm: str (opcional, padrão: settings)
        forcar_atualizacao: bool (opcional, padrão: false)
    """
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        atribuir_client_roles_usuario_kc,
        obter_admin_keycloak,
        provisionar_client_kc,
        provisionar_role_client_kc,
    )
    from apps.extracao.tasks import (  # noqa: PLC0415
        extrair_perfis_coresso,
        extrair_sistemas_coresso,
        extrair_vinculos_usuario_grupo_coresso,
    )
    from apps.staging.models import (  # noqa: PLC0415
        PerfilCoressoStaging,
        SistemaStaging,
    )

    corpo = request.data or {}
    sis_id = corpo.get("coresso_sis_id")
    gru_id = corpo.get("coresso_gru_id")
    realm = corpo.get("realm") or None
    forcar = bool(corpo.get("forcar_atualizacao", False))

    if not sis_id:
        return Response(
            {"detalhe": "coresso_sis_id é obrigatório."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    sis_id_int = int(sis_id)
    resultado: dict[str, Any] = {
        "coresso_sis_id": sis_id_int,
        "forcar_atualizacao": forcar,
    }
    if gru_id:
        resultado["coresso_gru_id"] = gru_id

    try:
        total_sistemas = extrair_sistemas_coresso()
        resultado["sistemas_extraidos"] = total_sistemas

        total_perfis = extrair_perfis_coresso()
        resultado["perfis_extraidos"] = total_perfis

        admin = obter_admin_keycloak(realm=realm)

        sistema = SistemaStaging.objects.filter(
            coresso_sis_id=sis_id_int, situacao=1
        ).first()
        if sistema:
            r_cli = provisionar_client_kc(admin, sistema, realm=realm)
            resultado["client"] = r_cli
        else:
            resultado["client"] = {
                "acao": "ignorado",
                "motivo": "sistema não encontrado ou inativo",
            }

        qs_perfis = PerfilCoressoStaging.objects.select_related(
            "sistema"
        ).filter(coresso_sis_id=sis_id_int)
        if gru_id:
            qs_perfis = qs_perfis.filter(coresso_gru_id=str(gru_id))

        roles_criados = roles_erros = 0
        for perfil in qs_perfis.iterator():
            try:
                provisionar_role_client_kc(admin, perfil)
                roles_criados += 1
            except Exception:
                roles_erros += 1
        resultado["roles"] = {
            "provisionados": roles_criados,
            "erros": roles_erros,
        }

        vinculos = extrair_vinculos_usuario_grupo_coresso(
            sis_id=sis_id_int,
            gru_id=gru_id or None,
        )
        r_vinc = atribuir_client_roles_usuario_kc(admin, vinculos)
        resultado["vinculos"] = r_vinc

    except Exception as exc:
        logger.exception("Pipeline sistema %s falhou: %s", sis_id, exc)
        resultado["erro"] = str(exc)
        return Response(
            resultado,
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(resultado)


# ---------------------------------------------------------------------------
# Dashboard e Kanban (HTML)
# ---------------------------------------------------------------------------


class DashboardView(View):
    """Renderiza a tela HTML de acompanhamento das execuções ETL.

    Combina a última execução de cada fonte com uma lista filtrável do
    histórico completo, servindo como visão operacional para quem
    acompanha o pipeline sem precisar consumir a API diretamente.
    """

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
    """Renderiza o kanban de etapas do pipeline por fonte ou execução.

    Por padrão mostra a última execução de cada fonte lado a lado; o
    filtro `id_execucao` permite fixar uma execução específica (mesmo
    que não seja mais a mais recente) para inspecionar suas etapas
    (`LogEtapaETL`) isoladamente.
    """

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
        etapas_por_execucao: dict[str, list[LogEtapaETL]] = {}
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
