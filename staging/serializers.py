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
    class Meta:
        model = StagingUsuarioServidor
        fields = [
            "id", "rf", "cpf", "email", "nome", "data_nascimento",
            "cargo", "funcao", "situacao", "lotacao", "lotacao_nome",
            "dre", "ue", "source", "status", "execution_id",
            "extracted_at", "transformed_at", "error_detail",
        ]
        read_only_fields = fields


class StagingUsuarioAlunoSerializer(serializers.ModelSerializer):
    class Meta:
        model = StagingUsuarioAluno
        fields = [
            "id", "cpf", "email", "nome", "data_nascimento",
            "matricula", "cod_escola", "turma", "dre", "ue",
            "situacao", "source", "status", "execution_id",
            "extracted_at", "transformed_at", "error_detail",
        ]
        read_only_fields = fields


class StagingUsuarioTerceiroSerializer(serializers.ModelSerializer):
    class Meta:
        model = StagingUsuarioTerceiro
        fields = [
            "id", "cpf", "email", "nome", "data_nascimento",
            "tipo_acesso", "situacao", "source", "status", "execution_id",
            "extracted_at", "transformed_at", "error_detail",
        ]
        read_only_fields = fields


class StagingPerfilSerializer(serializers.ModelSerializer):
    class Meta:
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
    class Meta:
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
    winner_nome = serializers.CharField(source="winner.nome", read_only=True)
    winner_source = serializers.CharField(source="winner.source", read_only=True)
    loser_nome = serializers.SerializerMethodField()
    loser_source = serializers.SerializerMethodField()

    class Meta:
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
        return obj.loser.nome if obj.loser else None

    def get_loser_source(self, obj):
        return obj.loser.source if obj.loser else None
