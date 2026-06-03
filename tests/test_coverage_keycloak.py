"""Testes de cobertura adicional para core/keycloak_client.py."""
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


class TestWithBackoff4xx:
    def test_raises_immediately_on_4xx_error(self):
        """Erros 4xx não devem fazer retry — devem ser levantados imediatamente."""
        from core.keycloak_client import _with_backoff

        class FakeKcError(ConnectionError):
            def __init__(self, msg, code):
                super().__init__(msg)
                self.response_code = code

        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise FakeKcError("bad request", 400)

        with pytest.raises(FakeKcError):
            _with_backoff(fn, max_retries=3, base_delay=0)

        # Deve ter sido chamado apenas 1 vez (sem retry)
        assert calls["n"] == 1

    def test_retries_on_5xx_error(self):
        """Erros 5xx devem ser retentados."""
        from core.keycloak_client import _with_backoff

        class FakeKcError(ConnectionError):
            def __init__(self, msg, code):
                super().__init__(msg)
                self.response_code = code

        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise FakeKcError("server error", 503)

        with pytest.raises(FakeKcError):
            _with_backoff(fn, max_retries=1, base_delay=0)

        # Deve ter tentado mais de 1 vez
        assert calls["n"] == 2


class TestEnsureRealmExists:
    @patch("core.keycloak_client.KeycloakAdmin")
    def test_returns_false_when_realm_already_exists(self, mock_kc_cls, settings):
        """Se o realm já existe, retorna False."""
        settings.KEYCLOAK_SERVER_URL = "http://keycloak:8080"
        settings.KEYCLOAK_ADMIN_USER = "admin"
        settings.KEYCLOAK_ADMIN_PAWD = "admin"
        settings.KEYCLOAK_VERIFY_SSL = False

        mock_admin_instance = MagicMock()
        mock_admin_instance.get_realms.return_value = [
            {"realm": "sme-apps"},
            {"realm": "master"},
        ]
        mock_kc_cls.return_value = mock_admin_instance

        from core.keycloak_client import ensure_realm_exists

        result = ensure_realm_exists("sme-apps")
        assert result is False
        mock_admin_instance.create_realm.assert_not_called()

    @patch("core.keycloak_client.KeycloakAdmin")
    def test_returns_true_and_creates_realm_when_not_found(self, mock_kc_cls, settings):
        """Se o realm não existe, cria-o e retorna True."""
        settings.KEYCLOAK_SERVER_URL = "http://keycloak:8080"
        settings.KEYCLOAK_ADMIN_USER = "admin"
        settings.KEYCLOAK_ADMIN_PAWD = "admin"
        settings.KEYCLOAK_VERIFY_SSL = False

        mock_admin_instance = MagicMock()
        mock_admin_instance.get_realms.return_value = [{"realm": "master"}]
        mock_kc_cls.return_value = mock_admin_instance

        from core.keycloak_client import ensure_realm_exists

        result = ensure_realm_exists("new-realm")
        assert result is True
        mock_admin_instance.create_realm.assert_called_once_with(
            {"realm": "new-realm", "enabled": True}
        )


class TestSearchKcCandidatesRfException:
    def test_rf_lookup_exception_is_silenced(self):
        """Exceção ao buscar por RF deve ser ignorada e retornar lista vazia."""
        from staging.models import StagingUsuarioServidor

        from core.models import ETLExecution
        from core.keycloak_client import _search_kc_candidates

        execution = ETLExecution.objects.create(source="all")
        usuario = StagingUsuarioServidor(
            execution_id=execution.id,
            rf="99999",
            cpf="52998224725",
            nome="Test",
            status="ready",
            source="se1426",
        )
        usuario.save()

        admin = MagicMock()
        # username lookup retorna vazio, email lookup retorna vazio,
        # rf lookup (rf="99999" != username="123") lança exceção
        admin.get_users.side_effect = [[], [], Exception("rf lookup failed")]

        result = _search_kc_candidates(
            admin,
            new_username="123",  # diferente de rf="99999"
            email="",
            usuario=usuario,
        )
        assert result == []


class TestHandleNewUpsert409Recovery:
    def test_409_conflict_recovers_via_get_users(self):
        """Conflito 409 em create_user deve tentar buscar e atualizar o usuário existente."""
        from staging.models import StagingUsuarioServidor

        from core.keycloak_client import UpsertControl, _handle_new_upsert

        class ConflictError(Exception):
            response_code = 409

        admin = MagicMock()
        admin.get_users.return_value = [{"id": "existing-kc-id", "username": "12345"}]

        with patch("core.keycloak_client._with_backoff") as mock_backoff:
            # create_user levanta 409
            mock_backoff.side_effect = [ConflictError("conflict"), None]

            usuario = StagingUsuarioServidor(
                rf="12345",
                cpf="52998224725",
                nome="Test",
                status="ready",
                source="se1426",
            )
            payload = {"username": "12345", "email": "test@sme.sp.gov.br"}
            upsert = MagicMock(spec=UpsertControl)
            upsert.target_id = None

            with patch("core.keycloak_client._find_existing_kc_user", return_value=None):
                kc_id, action = _handle_new_upsert(admin, upsert, usuario, payload)

        assert kc_id == "existing-kc-id"
        assert action == "updated"


class TestSanitizeRedirectUri:
    def test_returns_none_for_empty_after_strip(self):
        """String que resulta vazia após strip deve retornar None."""
        from core.keycloak_client import _sanitize_redirect_uri

        result = _sanitize_redirect_uri("''")
        assert result is None

    def test_returns_none_for_empty_string(self):
        from core.keycloak_client import _sanitize_redirect_uri

        result = _sanitize_redirect_uri("")
        assert result is None

    def test_returns_none_for_none(self):
        from core.keycloak_client import _sanitize_redirect_uri

        result = _sanitize_redirect_uri(None)
        assert result is None

    def test_returns_none_for_invalid_uri_scheme(self):
        """URI com esquema inválido (não http/https/*) deve retornar None."""
        from core.keycloak_client import _sanitize_redirect_uri

        result = _sanitize_redirect_uri("ftp://example.com/callback")
        assert result is None

    def test_returns_cleaned_uri_for_https(self):
        from core.keycloak_client import _sanitize_redirect_uri

        result = _sanitize_redirect_uri("https://example.com/callback")
        assert result == "https://example.com/callback"

    def test_strips_surrounding_quotes(self):
        from core.keycloak_client import _sanitize_redirect_uri

        result = _sanitize_redirect_uri("'https://example.com/callback'")
        assert result == "https://example.com/callback"
