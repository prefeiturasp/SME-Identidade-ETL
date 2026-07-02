"""Autenticação por API Key para os endpoints do ETL."""

from __future__ import annotations

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request


class _UsuarioApiKey:
    """Representação mínima de usuário autenticado por API Key."""

    is_authenticated = True


class AutenticacaoApiKey(BaseAuthentication):
    """Autentica requisições via API Key no header configurado.

    A chave é comparada com ``settings.API_KEY``.
    O header utilizado é definido por ``settings.API_KEY_HEADER``.
    """

    def authenticate(
        self, request: Request
    ) -> tuple[_UsuarioApiKey, None] | None:
        """Valida o API Key presente no header da requisição.

        Args:
            request: Requisição HTTP recebida.

        Returns:
            Tupla (usuário, None) se autenticado, None se header ausente.

        Raises:
            AuthenticationFailed: Se o API Key for inválido.
        """
        header = settings.API_KEY_HEADER.upper().replace("-", "_")
        chave = request.META.get(f"HTTP_{header}", "")
        if not chave:
            return None
        if chave != settings.API_KEY:
            raise AuthenticationFailed("API Key inválida.")
        return (_UsuarioApiKey(), None)

    def authenticate_header(self, request: Request) -> str:
        """Retorna o nome do header de autenticação esperado."""
        return settings.API_KEY_HEADER  # type: ignore[no-any-return]
