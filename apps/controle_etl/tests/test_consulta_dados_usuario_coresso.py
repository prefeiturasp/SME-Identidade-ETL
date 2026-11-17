
"""Testes da consulta HTTP de dados do usuário no CoreSSO."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.extracao.consultas_coresso import ConsultaCoreSSOError

_URL = "/identidade-etl/api/v1/etl/coresso/usuarios/{login}/dados/"
_CONSULTA = "apps.controle_etl.views.selecionar_dados_usuario_por_login"


@pytest.fixture
def cliente(settings: Any) -> APIClient:
    """Fornece um cliente HTTP com API Key de teste."""
    settings.API_KEY = "chave-teste"
    settings.API_KEY_HEADER = "X-API-Key"
    cliente_api = APIClient()
    cliente_api.credentials(HTTP_X_API_KEY="chave-teste")
    return cliente_api


class TestConsultaDadosUsuarioCoreSSOView:
    def test_rota_registrada(self) -> None:
        url = reverse(
            "etl-coresso-usuarios-dados",
            kwargs={"login": "1234567"},
        )
        assert url == _URL.format(login="1234567")

    def test_encontrado_retorna_200_com_quatro_campos(
        self,
        cliente: APIClient,
    ) -> None:
        dados = {
            "nome": "USUARIO TESTE",
            "cpf": "00123456789",
            "email": "email-invalido",
            "codigo_rf": "0001234567",
        }
        with patch(_CONSULTA, return_value=dados) as consulta:
            resposta = cliente.get(_URL.format(login="0001234567"))

        assert resposta.status_code == status.HTTP_200_OK
        assert resposta.json() == dados
        consulta.assert_called_once_with("0001234567")

    @pytest.mark.parametrize("email", [None, "", "sem-arroba"])
    def test_preserva_email_sem_validar_formato(
        self,
        cliente: APIClient,
        email: str | None,
    ) -> None:
        dados = {
            "nome": "USUARIO TESTE",
            "cpf": "12345678901",
            "email": email,
            "codigo_rf": "1234567",
        }
        with patch(_CONSULTA, return_value=dados):
            resposta = cliente.get(_URL.format(login="1234567"))

        assert resposta.status_code == status.HTTP_200_OK
        assert resposta.json()["email"] == email

    def test_nao_encontrado_retorna_204_sem_corpo(
        self,
        cliente: APIClient,
    ) -> None:
        with patch(_CONSULTA, return_value=None) as consulta:
            resposta = cliente.get(_URL.format(login="1234567"))

        assert resposta.status_code == status.HTTP_204_NO_CONTENT
        assert resposta.content == b""
        consulta.assert_called_once_with("1234567")

    def test_erro_de_consulta_retorna_503(
        self,
        cliente: APIClient,
    ) -> None:
        with patch(
            _CONSULTA,
            side_effect=ConsultaCoreSSOError("Falha no CoreSSO"),
        ):
            resposta = cliente.get(_URL.format(login="1234567"))

        assert resposta.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert resposta.json() == {"codigo": "ERRO_CONSULTA_CORESSO"}

    def test_sem_api_key_retorna_401_sem_consultar_coresso(
        self,
        cliente: APIClient,
    ) -> None:
        cliente.credentials()
        with patch(_CONSULTA) as consulta:
            resposta = cliente.get(_URL.format(login="1234567"))

        assert resposta.status_code == status.HTTP_401_UNAUTHORIZED
        consulta.assert_not_called()

    def test_api_key_invalida_retorna_401_sem_consultar_coresso(
        self,
        cliente: APIClient,
    ) -> None:
        cliente.credentials(HTTP_X_API_KEY="chave-invalida")
        with patch(_CONSULTA) as consulta:
            resposta = cliente.get(_URL.format(login="1234567"))

        assert resposta.status_code == status.HTTP_401_UNAUTHORIZED
        consulta.assert_not_called()

    def test_post_nao_permitido_nao_consulta_coresso(
        self,
        cliente: APIClient,
    ) -> None:
        with patch(_CONSULTA) as consulta:
            resposta = cliente.post(
                _URL.format(login="1234567"),
                {},
                format="json",
            )

        assert resposta.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
        consulta.assert_not_called()
