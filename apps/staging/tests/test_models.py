"""Testes para apps.staging.models."""

from __future__ import annotations

import uuid

import pytest

from apps.staging.models import (
    PerfilCoressoStaging,
    SistemaStaging,
    UsuarioAlunoStaging,
    UsuarioServidorStaging,
    UsuarioTerceiroStaging,
)


@pytest.mark.django_db
class TestStr:
    def test_usuario_servidor_staging(self) -> None:
        u = UsuarioServidorStaging.objects.create(
            id_execucao=uuid.uuid4(),
            fonte="se1426",
            rf="1234567",
            cpf="12345678901",
            situacao="ativo",
        )
        assert str(u) == "Servidor rf=1234567 cpf=12345678901 [ativo]"

    def test_usuario_aluno_staging(self) -> None:
        a = UsuarioAlunoStaging.objects.create(
            id_execucao=uuid.uuid4(),
            fonte="eol_alunos",
            cpf="98765432100",
            cod_escola="EE001",
            situacao="ativo",
        )
        assert str(a) == "Aluno cpf=98765432100 escola=EE001 [ativo]"

    def test_usuario_terceiro_staging(self) -> None:
        t = UsuarioTerceiroStaging.objects.create(
            id_execucao=uuid.uuid4(),
            fonte="coresso",
            cpf="11122233344",
            tipo_acesso="legado-coresso",
            situacao="ativo",
        )
        assert str(t) == (
            "Terceiro cpf=11122233344 tipo=legado-coresso [ativo]"
        )

    def test_sistema_staging_usa_sigla(self) -> None:
        s = SistemaStaging.objects.create(
            coresso_sis_id=42, nome="Sistema X", sigla="SISX"
        )
        assert str(s) == "Sistema SISX (sis_id=42)"

    def test_sistema_staging_sem_sigla_usa_nome(self) -> None:
        s = SistemaStaging.objects.create(
            coresso_sis_id=43, nome="Sistema Y", sigla=None
        )
        assert str(s) == "Sistema Sistema Y (sis_id=43)"

    def test_perfil_coresso_staging(self) -> None:
        p = PerfilCoressoStaging.objects.create(
            coresso_gru_id="gru-1",
            coresso_sis_id=42,
            nome="Perfil Admin",
        )
        assert str(p) == "Perfil Perfil Admin (gru_id=gru-1)"
