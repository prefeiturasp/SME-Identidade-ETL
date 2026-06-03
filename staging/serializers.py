"""Serializers DRF para os modelos de dados de staging."""
from rest_framework import serializers

from .models import (
    DedupResult,
    StagingLotacao,
    StagingPerfil,
    StagingUsuarioAluno,
    StagingUsuarioServidor,
    StagingUsuarioTerceiro,
)


class StagingUsuarioServidorSerializer(serializers.ModelSerializer):
    """Serializer para registros de staging de servidor (funcionario escolar)."""

    class Meta:
        """Metadados do serializer StagingUsuarioServidorSerializer."""

        model = StagingUsuarioServidor
        fields = [
            "id", "rf", "cpf", "email", "nome", "data_nascimento",
            "cargo", "funcao", "situacao", "lotacao", "lotacao_nome",
            "dre", "ue", "source", "status", "execution_id",
            "extracted_at", "transformed_at", "error_detail",
        ]
        read_only_fields = fields


class StagingUsuarioAlunoSerializer(serializers.ModelSerializer):
    """Serializer para registros de staging de aluno."""

    class Meta:
        """Metadados do serializer StagingUsuarioAlunoSerializer."""

        model = StagingUsuarioAluno
        fields = [
            "id", "cpf", "email", "nome", "data_nascimento",
            "matricula", "cod_escola", "turma", "dre", "ue",
            "situacao", "source", "status", "execution_id",
            "extracted_at", "transformed_at", "error_detail",
        ]
        read_only_fields = fields


class StagingUsuarioTerceiroSerializer(serializers.ModelSerializer):
    """Serializer para registros de staging de usuario terceiro."""

    class Meta:
        """Metadados do serializer StagingUsuarioTerceiroSerializer."""

        model = StagingUsuarioTerceiro
        fields = [
            "id", "cpf", "email", "nome", "data_nascimento",
            "tipo_acesso", "situacao", "source", "status", "execution_id",
            "extracted_at", "transformed_at", "error_detail",
        ]
        read_only_fields = fields


class StagingPerfilSerializer(serializers.ModelSerializer):
    """Serializer para registros de mapeamento de perfil/role."""

    class Meta:
        """Metadados do serializer StagingPerfilSerializer."""

        model = StagingPerfil
        fields = [
            "id",
            "cargo_codigo",
            "cargo_nome",
            "funcao_codigo",
            "funcao_nome",
            "keycloak_role",
            "keycloak_group_path",
            "is_active",
            "source",
        ]


class StagingLotacaoSerializer(serializers.ModelSerializer):
    """Serializer para registros de lotacao (unidade escolar / DRE)."""

    class Meta:
        """Metadados do serializer StagingLotacaoSerializer."""

        model = StagingLotacao
        fields = [
            "id",
            "codigo",
            "nome",
            "tipo",
            "dre_codigo",
            "dre_nome",
            "keycloak_group_path",
            "keycloak_group_id",
            "is_active",
        ]


class DedupResultSerializer(serializers.ModelSerializer):
    """Serializer para registros de resultado de deduplicacao com info de vencedor/perdedor."""

    winner_nome = serializers.CharField(source="winner.nome", read_only=True)
    winner_source = serializers.CharField(source="winner.source", read_only=True)
    loser_nome = serializers.SerializerMethodField()
    loser_source = serializers.SerializerMethodField()

    class Meta:
        """Metadados do serializer DedupResultSerializer."""

        model = DedupResult
        fields = [
            "id",
            "dedup_key",
            "cpf",
            "rf",
            "match_type",
            "decision",
            "merged_fields",
            "confidence",
            "reviewed",
            "review_note",
            "winner",
            "winner_nome",
            "winner_source",
            "loser",
            "loser_nome",
            "loser_source",
            "execution_id",
            "created_at",
        ]
        read_only_fields = [
            "id", "dedup_key", "cpf", "rf", "match_type", "decision",
            "merged_fields", "confidence", "winner", "winner_nome",
            "winner_source", "loser", "loser_nome", "loser_source",
            "execution_id", "created_at",
        ]

    def get_loser_nome(self, obj):
        """Retorna o nome de exibicao do registro perdedor, ou None se ausente."""
        return obj.loser.nome if obj.loser else None

    def get_loser_source(self, obj):
        """Retorna o identificador do sistema-fonte do registro perdedor, ou None se ausente."""
        return obj.loser.source if obj.loser else None
