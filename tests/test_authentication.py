"""Testes para core/authentication.py — cobre linhas não cobertas."""
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.django_db


class TestInternalUser:
    def test_str_returns_etl_internal(self):
        from core.authentication import _InternalUser

        assert str(_InternalUser()) == "etl-internal"


class TestInternalTokenAuthentication:
    def _make_request(self, token=None):
        request = MagicMock()
        if token is not None:
            request.headers = {"X-Internal-Token": token}
        else:
            request.headers = {}
        return request

    def test_returns_none_when_no_header(self, settings):
        from core.authentication import InternalTokenAuthentication

        settings.ETL_INTERNAL_TOKEN = "correct-token"
        result = InternalTokenAuthentication().authenticate(self._make_request())
        assert result is None

    def test_raises_when_token_wrong(self, settings):
        from rest_framework.exceptions import AuthenticationFailed

        from core.authentication import InternalTokenAuthentication

        settings.ETL_INTERNAL_TOKEN = "correct-token"
        with pytest.raises(AuthenticationFailed):
            InternalTokenAuthentication().authenticate(self._make_request("wrong-token"))

    def test_raises_when_expected_token_empty(self, settings):
        from rest_framework.exceptions import AuthenticationFailed

        from core.authentication import InternalTokenAuthentication

        settings.ETL_INTERNAL_TOKEN = ""
        with pytest.raises(AuthenticationFailed):
            InternalTokenAuthentication().authenticate(self._make_request("any-token"))

    def test_returns_user_and_token_on_success(self, settings):
        from core.authentication import InternalTokenAuthentication, _InternalUser

        settings.ETL_INTERNAL_TOKEN = "correct-token"
        user, tok = InternalTokenAuthentication().authenticate(
            self._make_request("correct-token")
        )
        assert isinstance(user, _InternalUser)
        assert tok == "correct-token"

    def test_authenticate_header(self):
        from core.authentication import InternalTokenAuthentication

        result = InternalTokenAuthentication().authenticate_header(MagicMock())
        assert result == "X-Internal-Token"


class TestInternalTokenScheme:
    def test_get_security_definition_returns_api_key_scheme(self):
        try:
            from core.authentication import InternalTokenScheme
        except ImportError:
            pytest.skip("drf-spectacular not installed")

        # OpenApiAuthenticationExtension.__init__ exige 'target'; passa MagicMock.
        defn = InternalTokenScheme(target=MagicMock()).get_security_definition(MagicMock())
        assert defn["type"] == "apiKey"
        assert defn["in"] == "header"
        assert defn["name"] == "X-Internal-Token"
