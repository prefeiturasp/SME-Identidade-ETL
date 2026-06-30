"""Serializadores DRF da app controle_etl."""

from django.conf import settings
from rest_framework import serializers

from .models import (
    CheckpointEtl,
    ControleProvisionamento,
    ExecucaoETL,
    LogEtapaETL,
    MarcaDaguaExtracao,
    RastreioTentativa,
)

_HT_REALM = "Realm Keycloak."
_HT_FILTRAR_SIS_ID = "Filtrar por coresso_sis_id."
_HT_FILTRAR_GRU_ID = "Filtrar por coresso_gru_id."


class LogEtapaETLSerializer(serializers.ModelSerializer):
    """Serializa o log de uma etapa do pipeline ETL."""

    class Meta:
        model = LogEtapaETL
        fields = [
            "id",
            "nome_etapa",
            "ordem_etapa",
            "situacao",
            "registros_entrada",
            "registros_saida",
            "registros_erro",
            "iniciado_em",
            "finalizado_em",
            "detalhe_erro",
            "metadados",
        ]
        read_only_fields = fields


class ExecucaoETLSerializer(serializers.ModelSerializer):
    """Serializa uma execução ETL com suas etapas."""

    etapas = LogEtapaETLSerializer(many=True, read_only=True)
    duracao_segundos = serializers.FloatField(read_only=True)

    class Meta:
        model = ExecucaoETL
        fields = [
            "id",
            "id_execucao",
            "fonte",
            "realm_destino",
            "tipo_disparo",
            "situacao",
            "total_extraido",
            "total_transformado",
            "total_carregado",
            "total_erros",
            "total_ignorados",
            "iniciado_em",
            "finalizado_em",
            "criado_em",
            "atualizado_em",
            "id_tarefa_celery",
            "observacao",
            "disparado_por",
            "duracao_segundos",
            "etapas",
        ]
        read_only_fields = [
            "id",
            "id_execucao",
            "situacao",
            "total_extraido",
            "total_transformado",
            "total_carregado",
            "total_erros",
            "total_ignorados",
            "iniciado_em",
            "finalizado_em",
            "criado_em",
            "atualizado_em",
            "id_tarefa_celery",
            "duracao_segundos",
            "etapas",
        ]


class CriarExecucaoETLSerializer(serializers.Serializer):
    """Serializa a criação de uma execução ETL via API."""

    fonte = serializers.ChoiceField(
        choices=["todos", "se1426", "coresso", "eol_alunos"],
        default="todos",
    )
    realm_destino = serializers.CharField(
        default=settings.KEYCLOAK_REALM,
    )
    observacao = serializers.CharField(required=False, allow_blank=True)


class ListarExecucaoETLSerializer(serializers.ModelSerializer):
    """Serializa listagem resumida de execuções ETL."""

    class Meta:
        model = ExecucaoETL
        fields = [
            "id",
            "id_execucao",
            "fonte",
            "situacao",
            "total_extraido",
            "total_carregado",
            "total_erros",
            "criado_em",
            "iniciado_em",
            "finalizado_em",
        ]
        read_only_fields = fields


class ControleProvisionamentoSerializer(serializers.ModelSerializer):
    """Serializa o controle de provisionamento de entidades."""

    class Meta:
        model = ControleProvisionamento
        fields = [
            "id",
            "tipo_entidade",
            "sistema_origem",
            "id_origem",
            "id_destino",
            "realm_destino",
            "hash_conteudo",
            "versao",
            "ativo",
            "erro_sincronizacao",
            "sincronizado_em",
            "criado_em",
        ]
        read_only_fields = fields


class MarcaDaguaExtracaoSerializer(serializers.ModelSerializer):
    """Serializa o watermark de extração por fonte."""

    class Meta:
        model = MarcaDaguaExtracao
        fields = [
            "fonte",
            "ultimo_processado_em",
            "ultima_pagina",
            "total_processado",
            "atualizado_em",
        ]
        read_only_fields = fields


class CheckpointEtlSerializer(serializers.ModelSerializer):
    """Serializa um checkpoint de retomada de execução."""

    class Meta:
        model = CheckpointEtl
        fields = [
            "id_execucao",
            "etapa",
            "pagina_atual",
            "ultimo_id_processado",
            "estado_json",
            "atualizado_em",
        ]
        read_only_fields = fields


class RastreioTentativaSerializer(serializers.ModelSerializer):
    """Serializa o rastreio de tentativas de uma tarefa."""

    class Meta:
        model = RastreioTentativa
        fields = [
            "id",
            "id_execucao",
            "nome_tarefa",
            "numero_tentativa",
            "iniciado_em",
            "erro",
            "duracao_segundos",
        ]
        read_only_fields = fields


class SincronizarUsuarioSerializer(serializers.Serializer):
    """Valida payload de sincronização de usuário."""

    identificador = serializers.CharField(
        help_text="RF, CPF ou email do usuário."
    )
    realm = serializers.CharField(
        default=settings.KEYCLOAK_REALM,
        required=False,
        help_text=_HT_REALM,
    )


class ConcederAcessoSerializer(serializers.Serializer):
    """Valida payload de concessão de acesso a sistema."""

    identificador = serializers.CharField(
        help_text="RF, CPF ou email do usuário."
    )
    sistema = serializers.IntegerField(
        help_text="coresso_sis_id do sistema alvo."
    )
    roles = serializers.ListField(
        child=serializers.CharField(),
        min_length=1,
        help_text="Nomes dos roles/perfis a conceder.",
    )
    realm = serializers.CharField(
        default=settings.KEYCLOAK_REALM, required=False
    )


class ProvisionarSistemasSerializer(serializers.Serializer):
    """Valida payload de provisionamento de sistemas."""

    realm = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_HT_REALM,
    )
    sigla = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Filtrar por sigla do sistema.",
    )
    coresso_sis_id = serializers.IntegerField(
        required=False,
        help_text=_HT_FILTRAR_SIS_ID,
    )


class ProvisionarPerfisSerializer(serializers.Serializer):
    """Valida payload de provisionamento de perfis."""

    realm = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_HT_REALM,
    )
    coresso_sis_id = serializers.IntegerField(
        required=False,
        help_text=_HT_FILTRAR_SIS_ID,
    )
    coresso_gru_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_HT_FILTRAR_GRU_ID,
    )


class ExtrairVinculosSerializer(serializers.Serializer):
    """Valida payload de extração de vínculos."""

    coresso_sis_id = serializers.IntegerField(
        required=False,
        help_text=_HT_FILTRAR_SIS_ID,
    )
    coresso_gru_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_HT_FILTRAR_GRU_ID,
    )


class ProvisionarVinculosSerializer(serializers.Serializer):
    """Valida payload de provisionamento de vínculos."""

    realm = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_HT_REALM,
    )
    coresso_sis_id = serializers.IntegerField(
        required=False,
        help_text=_HT_FILTRAR_SIS_ID,
    )
    coresso_gru_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_HT_FILTRAR_GRU_ID,
    )


class PipelineSistemaSerializer(serializers.Serializer):
    """Valida payload do pipeline completo por sistema."""

    coresso_sis_id = serializers.IntegerField(
        help_text="ID do sistema no CoreSSO (obrigatório)."
    )
    coresso_gru_id = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Filtrar por grupo específico.",
    )
    realm = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_HT_REALM,
    )
    forcar_atualizacao = serializers.BooleanField(
        default=False,
        required=False,
        help_text="Forçar atualização mesmo sem mudanças.",
    )


class HealthStatusSerializer(serializers.Serializer):
    """Serializa o status de saúde do serviço."""

    status = serializers.ChoiceField(choices=["healthy", "unhealthy"])
