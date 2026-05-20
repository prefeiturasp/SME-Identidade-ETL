"""ViewSets de API para navegar e gerenciar os registros de dados de staging."""
from django.db.models import Count
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import (
    DedupResult, StagingLotacao, StagingPerfil,
    StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro,
)
from .serializers import (
    DedupResultSerializer,
    StagingLotacaoSerializer,
    StagingPerfilSerializer,
    StagingUsuarioAlunoSerializer,
    StagingUsuarioServidorSerializer,
    StagingUsuarioTerceiroSerializer,
)


class StagingUsuarioServidorViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet somente-leitura para registros de staging de servidor."""

    queryset = StagingUsuarioServidor.objects.all()
    serializer_class = StagingUsuarioServidorSerializer
    filterset_fields = ["source", "status", "execution_id", "dre"]
    search_fields = ["rf", "cpf", "nome", "lotacao"]
    ordering_fields = ["extracted_at", "nome", "source"]


class StagingUsuarioAlunoViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet somente-leitura para registros de staging de aluno."""

    queryset = StagingUsuarioAluno.objects.all()
    serializer_class = StagingUsuarioAlunoSerializer
    filterset_fields = ["source", "status", "execution_id", "dre"]
    search_fields = ["cpf", "nome", "matricula", "cod_escola"]
    ordering_fields = ["extracted_at", "nome", "source"]


class StagingUsuarioTerceiroViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet somente-leitura para registros de staging de usuario terceiro."""

    queryset = StagingUsuarioTerceiro.objects.all()
    serializer_class = StagingUsuarioTerceiroSerializer
    filterset_fields = ["source", "status", "execution_id", "tipo_acesso"]
    search_fields = ["cpf", "nome"]
    ordering_fields = ["extracted_at", "nome", "source"]


class StagingPerfilViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet para gerenciar registros de mapeamento de perfil/role em staging."""

    queryset = StagingPerfil.objects.all()
    serializer_class = StagingPerfilSerializer
    filterset_fields = ["keycloak_role", "is_active"]
    search_fields = ["cargo_codigo", "cargo_nome"]


class StagingLotacaoViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet para gerenciar registros de lotacao (unidade escolar / DRE) em staging."""

    queryset = StagingLotacao.objects.all()
    serializer_class = StagingLotacaoSerializer
    filterset_fields = ["tipo", "dre_codigo", "is_active"]
    search_fields = ["codigo", "nome"]


class DedupResultViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """ViewSet para navegar e resolver os resultados de deduplicacao."""

    queryset = DedupResult.objects.all()
    serializer_class = DedupResultSerializer
    filterset_fields = ["execution_id", "match_type", "decision", "reviewed", "cpf", "rf"]
    search_fields = ["dedup_key", "cpf", "rf"]
    ordering_fields = ["created_at", "confidence"]

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """Retorna estatisticas agregadas de dedup, opcionalmente filtradas por execution_id."""
        execution_id = request.query_params.get("execution_id")
        qs = self.get_queryset()
        if execution_id:
            qs = qs.filter(execution_id=execution_id)

        by_decision = dict(
            qs.values_list("decision").annotate(count=Count("id")).values_list("decision", "count")
        )
        by_match = dict(
            qs.values_list("match_type").annotate(count=Count("id")).values_list("match_type", "count")
        )

        return Response({
            "total_dedup_results": qs.count(),
            "by_decision": by_decision,
            "by_match_type": by_match,
            "pending_review": qs.filter(decision="conflict", reviewed=False).count(),
            "execution_id": execution_id,
        })

    @action(detail=False, methods=["get"])
    def conflicts(self, request):
        """Return unresolved conflict dedup results, optionally filtered by execution_id."""
        qs = self.get_queryset().filter(decision="conflict", reviewed=False)
        execution_id = request.query_params.get("execution_id")
        if execution_id:
            qs = qs.filter(execution_id=execution_id)

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)
