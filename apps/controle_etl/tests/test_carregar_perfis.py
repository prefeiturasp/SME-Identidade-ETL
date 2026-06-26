"""Testes para o management command carregar_perfis."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from apps.staging.models import PerfilCoressoStaging, SistemaStaging

_CMD = "apps.controle_etl.management.commands.carregar_perfis"


@pytest.fixture()
def _sistema_e_perfis(db: Any) -> None:
    """Cria sistema e perfis no staging."""
    sistema = SistemaStaging.objects.create(
        coresso_sis_id=42,
        nome="Teste",
        kc_client_uuid="uuid-42",
    )
    PerfilCoressoStaging.objects.create(
        coresso_gru_id="g1",
        coresso_sis_id=42,
        sistema=sistema,
        nome="Admin",
        kc_role_nome="Admin",
    )
    PerfilCoressoStaging.objects.create(
        coresso_gru_id="g2",
        coresso_sis_id=42,
        sistema=sistema,
        nome="Editor",
        kc_role_nome="Editor",
    )


@pytest.mark.django_db
class TestCarregarPerfis:
    def test_sem_perfis_exibe_zero(self) -> None:
        admin = MagicMock()
        with patch(
            f"{_CMD}.obter_admin_keycloak",
            return_value=admin,
        ):
            out = StringIO()
            call_command("carregar_perfis", stdout=out)
        assert "Total de perfis: 0" in out.getvalue()

    @pytest.mark.usefixtures("_sistema_e_perfis")
    def test_provisiona_perfis(self) -> None:
        admin = MagicMock()
        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.provisionar_role_client_kc",
                return_value={"acao": "criado"},
            ) as mock_prov,
        ):
            out = StringIO()
            call_command("carregar_perfis", stdout=out)
        assert mock_prov.call_count == 2
        assert "criados=2" in out.getvalue()

    @pytest.mark.usefixtures("_sistema_e_perfis")
    def test_filtra_por_sis_id(self) -> None:
        SistemaStaging.objects.create(coresso_sis_id=99, nome="Outro")
        PerfilCoressoStaging.objects.create(
            coresso_gru_id="g99",
            coresso_sis_id=99,
            nome="X",
            kc_role_nome="X",
        )
        admin = MagicMock()
        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.provisionar_role_client_kc",
                return_value={"acao": "criado"},
            ) as mock_prov,
        ):
            out = StringIO()
            call_command("carregar_perfis", "--sis-id=42", stdout=out)
        assert mock_prov.call_count == 2

    @pytest.mark.usefixtures("_sistema_e_perfis")
    def test_conta_erros(self) -> None:
        admin = MagicMock()
        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.provisionar_role_client_kc",
                side_effect=Exception("boom"),
            ),
        ):
            out = StringIO()
            call_command("carregar_perfis", stdout=out)
        assert "erros=2" in out.getvalue()

    @pytest.mark.usefixtures("_sistema_e_perfis")
    def test_conta_ignorados(self) -> None:
        admin = MagicMock()
        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.provisionar_role_client_kc",
                return_value={"acao": "ignorado"},
            ),
        ):
            out = StringIO()
            call_command("carregar_perfis", stdout=out)
        assert "ignorados=2" in out.getvalue()

    @pytest.mark.usefixtures("_sistema_e_perfis")
    def test_conta_atualizados(self) -> None:
        admin = MagicMock()
        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.provisionar_role_client_kc",
                return_value={"acao": "atualizado"},
            ),
        ):
            out = StringIO()
            call_command("carregar_perfis", stdout=out)
        assert "atualizados=2" in out.getvalue()

    def test_progresso_a_cada_50(self, db: Any) -> None:
        """Cobre o bloco de progresso (idx % 50 == 0)."""
        sistema = SistemaStaging.objects.create(
            coresso_sis_id=77,
            nome="Lote",
            kc_client_uuid="uuid-77",
        )
        for i in range(51):
            PerfilCoressoStaging.objects.create(
                coresso_gru_id=f"lote-{i}",
                coresso_sis_id=77,
                sistema=sistema,
                nome=f"P{i}",
                kc_role_nome=f"P{i}",
            )
        admin = MagicMock()
        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.provisionar_role_client_kc",
                return_value={"acao": "criado"},
            ),
        ):
            out = StringIO()
            call_command(
                "carregar_perfis",
                "--sis-id=77",
                stdout=out,
            )
        texto = out.getvalue()
        assert "[50/51]" in texto
        assert "criados=51" in texto
