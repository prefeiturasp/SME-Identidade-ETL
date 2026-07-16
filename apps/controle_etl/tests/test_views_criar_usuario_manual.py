"""Testes para a view de criação manual de usuário (sem CoreSSO)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.staging.models import (
    UsuarioAlunoStaging,
    UsuarioServidorStaging,
    UsuarioTerceiroStaging,
)

_ORQUESTRADOR = "apps.controle_etl.orquestrador_kc"


@pytest.fixture()
def cliente(settings: Any) -> APIClient:
    """Retorna APIClient autenticado com chave de teste."""
    settings.API_KEY = "chave-teste"
    settings.API_KEY_HEADER = "X-API-Key"
    c = APIClient()
    c.credentials(HTTP_X_API_KEY="chave-teste")
    return c


@pytest.mark.django_db
class TestCriarUsuarioManualView:
    """Testes da view criar_usuario_manual."""

    URL = "/identidade-etl/api/v1/etl/usuario/criar/"

    def test_sem_api_key_retorna_401(self) -> None:
        """Deve rejeitar requisição sem API Key."""
        resp = APIClient().post(
            self.URL, {"nome": "Teste", "cpf": "12345678900"}, format="json"
        )
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_sem_nome_retorna_400(self, cliente: APIClient) -> None:
        """Deve rejeitar payload sem o campo obrigatório nome."""
        resp = cliente.post(self.URL, {"cpf": "12345678900"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_sem_cpf_e_sem_rf_retorna_400(self, cliente: APIClient) -> None:
        """Deve exigir ao menos cpf ou rf."""
        resp = cliente.post(self.URL, {"nome": "Teste"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "cpf" in str(resp.json()).lower()

    def test_tipo_usuario_invalido_retorna_400(
        self, cliente: APIClient
    ) -> None:
        """Deve rejeitar tipo_usuario fora do choices permitido."""
        resp = cliente.post(
            self.URL,
            {
                "nome": "Teste",
                "cpf": "12345678900",
                "tipo_usuario": "robo",
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_cria_terceiro_com_sucesso(self, cliente: APIClient) -> None:
        """Deve criar registro de staging e provisionar no Keycloak."""
        resultado = {
            "acao": "criado",
            "kc_user_id": "kc-123",
            "hash_conteudo": "abc",
        }
        with (
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.provisionar_usuario_kc",
                return_value=resultado,
            ) as mock_provisionar,
        ):
            resp = cliente.post(
                self.URL,
                {
                    "nome": "Fulano de Tal",
                    "cpf": "12345678900",
                    "email": "fulano@externo.com",
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["acao"] == "criado"
        assert resp.json()["kc_user_id"] == "kc-123"

        assert UsuarioTerceiroStaging.objects.count() == 1
        usuario = UsuarioTerceiroStaging.objects.first()
        assert usuario.fonte == "api_manual"
        assert usuario.nome == "Fulano de Tal"
        assert usuario.cpf == "12345678900"

        mock_provisionar.assert_called_once()
        args, kwargs = mock_provisionar.call_args
        assert args[1] == usuario
        assert kwargs["realm"]

    def test_cria_servidor_com_rf(self, cliente: APIClient) -> None:
        """Deve materializar em UsuarioServidorStaging quando solicitado."""
        with (
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.provisionar_usuario_kc",
                return_value={"acao": "criado", "kc_user_id": "kc-1"},
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "nome": "Servidor Teste",
                    "rf": "1234567",
                    "tipo_usuario": "servidor",
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        assert UsuarioServidorStaging.objects.count() == 1
        assert UsuarioServidorStaging.objects.first().rf == "1234567"

    def test_cria_aluno(self, cliente: APIClient) -> None:
        """Deve materializar em UsuarioAlunoStaging quando solicitado."""
        with (
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.provisionar_usuario_kc",
                return_value={"acao": "criado", "kc_user_id": "kc-1"},
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "nome": "Aluno Teste",
                    "cpf": "98765432100",
                    "tipo_usuario": "aluno",
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        assert UsuarioAlunoStaging.objects.count() == 1

    def test_reenvio_com_mesmo_cpf_atualiza_registro(
        self, cliente: APIClient
    ) -> None:
        """Reenviar o mesmo cpf/fonte deve atualizar, não duplicar."""
        with (
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.provisionar_usuario_kc",
                return_value={"acao": "criado", "kc_user_id": "kc-1"},
            ),
        ):
            cliente.post(
                self.URL,
                {"nome": "Nome Antigo", "cpf": "11122233344"},
                format="json",
            )
            cliente.post(
                self.URL,
                {"nome": "Nome Novo", "cpf": "11122233344"},
                format="json",
            )

        assert UsuarioTerceiroStaging.objects.count() == 1
        assert UsuarioTerceiroStaging.objects.first().nome == "Nome Novo"

    def test_erro_ao_provisionar_retorna_502(self, cliente: APIClient) -> None:
        """Deve retornar 502 quando o Keycloak falhar."""
        with (
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.provisionar_usuario_kc",
                side_effect=Exception("keycloak indisponível"),
            ),
        ):
            resp = cliente.post(
                self.URL,
                {"nome": "Teste", "cpf": "55566677788"},
                format="json",
            )

        assert resp.status_code == status.HTTP_502_BAD_GATEWAY

    def test_roles_sem_sistema_retorna_400(self, cliente: APIClient) -> None:
        """Deve exigir sistema quando roles é informado."""
        resp = cliente.post(
            self.URL,
            {
                "nome": "Teste",
                "cpf": "12345678900",
                "roles": ["Admin"],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_sistema_sem_roles_retorna_400(self, cliente: APIClient) -> None:
        """Deve exigir roles quando sistema é informado."""
        resp = cliente.post(
            self.URL,
            {
                "nome": "Teste",
                "cpf": "12345678900",
                "sistema": 1,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_cria_e_concede_acesso_na_mesma_chamada(
        self, cliente: APIClient
    ) -> None:
        """Deve conceder o acesso ao sistema/roles após criar o usuário."""
        resultado_criacao = {"acao": "criado", "kc_user_id": "kc-1"}
        resultado_roles = {
            "sistema": "Sistema X",
            "client_id": "sistema-x",
            "roles_atribuidos": ["Admin"],
            "roles_nao_encontrados": [],
            "erros": 0,
        }
        with (
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.provisionar_usuario_kc",
                return_value=resultado_criacao,
            ),
            patch(
                f"{_ORQUESTRADOR}._conceder_roles_sistema_kc",
                return_value=resultado_roles,
            ) as mock_conceder,
        ):
            resp = cliente.post(
                self.URL,
                {
                    "nome": "Fulano",
                    "cpf": "12345678900",
                    "sistema": 1,
                    "roles": ["Admin"],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_200_OK
        corpo = resp.json()
        assert corpo["acao"] == "criado"
        assert corpo["roles_atribuidos"] == ["Admin"]
        mock_conceder.assert_called_once()
        args, _ = mock_conceder.call_args
        assert args[1] == "kc-1"
        assert args[2] == 1
        assert args[3] == ["Admin"]

    def test_erro_ao_conceder_acesso_retorna_502(
        self, cliente: APIClient
    ) -> None:
        """Deve retornar 502 se falhar ao conceder acesso pós-criação."""
        with (
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.provisionar_usuario_kc",
                return_value={"acao": "criado", "kc_user_id": "kc-1"},
            ),
            patch(
                f"{_ORQUESTRADOR}._conceder_roles_sistema_kc",
                side_effect=Exception("falha ao atribuir role"),
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "nome": "Fulano",
                    "cpf": "12345678900",
                    "sistema": 1,
                    "roles": ["Admin"],
                },
                format="json",
            )

        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
        assert resp.json()["kc_user_id"] == "kc-1"
