"""Autenticação interna via header X-Internal-Token para o ETL-MS."""
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class _InternalUser:
    """Representação mínima de usuário autenticado via token interno."""

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False

    def __str__(self):
        return "etl-internal"


class InternalTokenAuthentication(BaseAuthentication):
    """
    Autentica requisições pelo header `X-Internal-Token`.

    - Ausência do header → retorna None (sem autenticar, permite que outra
      classe tente ou que a permissão rejeite).
    - Header presente mas valor errado → 401 AuthenticationFailed.
    - Header correto → usuário interno autenticado.
    """

    def authenticate(self, request):
        token = request.headers.get("X-Internal-Token")
        if not token:
            return None

        expected = getattr(settings, "ETL_INTERNAL_TOKEN", "")
        if not expected or token != expected:
            raise AuthenticationFailed("Token interno inválido.")

        return (_InternalUser(), token)

    def authenticate_header(self, request):
        return "X-Internal-Token"


try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class InternalTokenScheme(OpenApiAuthenticationExtension):
        """Registra o security scheme no Swagger/OpenAPI gerado pelo drf-spectacular."""

        target_class = "core.authentication.InternalTokenAuthentication"
        name = "InternalToken"

        def get_security_definition(self, auto_schema):
            return {
                "type": "apiKey",
                "in": "header",
                "name": "X-Internal-Token",
                "description": "Token interno do ETL-MS. Valor local: `dev-etl-token`.",
            }

except ImportError:  # drf-spectacular não instalado
    pass
