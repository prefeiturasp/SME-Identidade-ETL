"""Serializadores DRF da app controle_etl."""

from rest_framework import serializers

from .models import (
    CheckpointEtl,
    ControleProvisionamento,
    ExecucaoETL,
    LogEtapaETL,
    MarcaDaguaExtracao,
    RastreioTentativa,
)


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
    realm_destino = serializers.CharField(default="sme-apps")
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


class HealthStatusSerializer(serializers.Serializer):
    """Serializa o status de saúde do serviço."""

    status = serializers.ChoiceField(choices=["healthy", "unhealthy"])
