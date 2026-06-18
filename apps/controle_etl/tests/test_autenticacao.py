"""Testes para apps.controle_etl.autenticacao."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from apps.controle_etl.autenticacao import AutenticacaoApiKey


@pytest.fixture()
def factory() -> RequestFactory:
    """Fornece uma RequestFactory para construir requisições de teste."""
    return RequestFactory()


@pytest.fixture()
def auth() -> AutenticacaoApiKey:
    """Fornece uma instância de AutenticacaoApiKey para os testes."""
    return AutenticacaoApiKey()


@pytest.mark.django_db
class TestAutenticacaoApiKey:
    """Testa a classe de autenticação por API Key."""

    def _drf_request(
        self,
        factory: RequestFactory,
        headers: dict[str, str] | None = None,
    ) -> Request:
        rf_request = factory.get("/", **(headers or {}))  # type: ignore[arg-type]
        return Request(rf_request)

    def test_sem_header_retorna_none(
        self,
        factory: RequestFactory,
        auth: AutenticacaoApiKey,
        settings: Any,
    ) -> None:
        """Verifica que a ausência do header de API Key retorna None."""
        settings.API_KEY = "chave-secreta"
        settings.API_KEY_HEADER = "X-API-Key"
        req = self._drf_request(factory)
        assert auth.authenticate(req) is None

    def test_chave_correta_autentica(
        self,
        factory: RequestFactory,
        auth: AutenticacaoApiKey,
        settings: Any,
    ) -> None:
        """Verifica que a chave correta autentica e retorna usuário."""
        settings.API_KEY = "chave-secreta"
        settings.API_KEY_HEADER = "X-API-Key"
        req = self._drf_request(factory, {"HTTP_X_API_KEY": "chave-secreta"})
        resultado = auth.authenticate(req)
        assert resultado is not None
        usuario, _ = resultado
        assert usuario.is_authenticated is True

    def test_chave_incorreta_lanca_excecao(
        self,
        factory: RequestFactory,
        auth: AutenticacaoApiKey,
        settings: Any,
    ) -> None:
        """Verifica que uma chave incorreta lança AuthenticationFailed."""
        settings.API_KEY = "chave-correta"
        settings.API_KEY_HEADER = "X-API-Key"
        req = self._drf_request(factory, {"HTTP_X_API_KEY": "chave-errada"})
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(req)

    def test_authenticate_header_retorna_nome_do_header(
        self,
        factory: RequestFactory,
        auth: AutenticacaoApiKey,
        settings: Any,
    ) -> None:
        """Verifica que authenticate_header retorna o nome do header."""
        settings.API_KEY_HEADER = "X-API-Key"
        req = self._drf_request(factory)
        assert auth.authenticate_header(req) == "X-API-Key"

    def test_header_com_hifens_e_normalizado(
        self,
        factory: RequestFactory,
        auth: AutenticacaoApiKey,
        settings: Any,
    ) -> None:
        """Verifica que um header com hífens é normalizado e autentica."""
        settings.API_KEY = "tok123"
        settings.API_KEY_HEADER = "X-Internal-Token"
        req = self._drf_request(factory, {"HTTP_X_INTERNAL_TOKEN": "tok123"})
        resultado = auth.authenticate(req)
        assert resultado is not None
