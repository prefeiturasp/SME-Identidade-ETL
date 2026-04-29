import sys
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()




class TestCheckCoressoDb:
    def test_not_configured_when_no_server(self, settings):
        settings.CORESSO_DB_SERVER = ""
        from core.health import _check_coresso_db
        result = _check_coresso_db()
        assert result["status"] == "not_configured"

    def test_healthy_on_successful_connection(self, settings):
        settings.CORESSO_DB_SERVER = "coresso-host"
        settings.CORESSO_DB_NAME = "coreSSO"
        settings.CORESSO_DB_USER = "user"
        settings.CORESSO_DB_PASSWORD = "pass"
        settings.CORESSO_DB_TIMEOUT = 5

        mock_row = MagicMock()
        mock_row.version = "Microsoft SQL Server 2019\n..."
        mock_row.db_name = "coreSSO"
        mock_row.login_user = "user"

        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [mock_row, [5]]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from core import health
            result = health._check_coresso_db()

        assert result["status"] == "healthy"
        assert "response_time_ms" in result

    def test_unhealthy_on_connection_error(self, settings):
        settings.CORESSO_DB_SERVER = "coresso-host"
        settings.CORESSO_DB_NAME = "coreSSO"
        settings.CORESSO_DB_USER = "user"
        settings.CORESSO_DB_PASSWORD = "pass"
        settings.CORESSO_DB_TIMEOUT = 5

        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.side_effect = Exception("Connection refused")

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from core import health
            result = health._check_coresso_db()

        assert result["status"] == "unhealthy"
        assert "Connection refused" in result["detail"]




class TestCheckSmeIntegracao:
    def test_not_configured_when_no_url(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = ""
        from core.health import _check_sme_integracao
        result = _check_sme_integracao()
        assert result["status"] == "not_configured"

    def test_healthy_on_swagger_200(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-integracao"
        settings.SME_INTEGRACAO_TIMEOUT = 5
        settings.SME_INTEGRACAO_LOGIN = ""
        settings.SME_INTEGRACAO_PASSWORD = ""
        settings.SME_INTEGRACAO_API_KEY = ""

        resp_swagger = MagicMock()
        resp_swagger.status_code = 200

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = resp_swagger

        with patch("core.health.httpx.Client", return_value=mock_client):
            from core.health import _check_sme_integracao
            result = _check_sme_integracao()

        assert result["status"] == "healthy"
        assert result["swagger_available"] is True

    def test_unhealthy_on_connect_error(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-integracao"
        settings.SME_INTEGRACAO_TIMEOUT = 5
        settings.SME_INTEGRACAO_LOGIN = ""
        settings.SME_INTEGRACAO_PASSWORD = ""
        settings.SME_INTEGRACAO_API_KEY = ""

        import httpx as real_httpx

        with patch("core.health.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(side_effect=real_httpx.ConnectError("refused"))
            mock_cls.return_value = mock_client
            from core.health import _check_sme_integracao
            result = _check_sme_integracao()

        assert result["status"] == "unhealthy"




class TestHealthSources:
    @patch("core.health._check_coresso_db")
    @patch("core.health._check_sme_integracao")
    def test_returns_healthy_when_all_sources_healthy(self, mock_sme, mock_coresso, client):
        mock_coresso.return_value = {"status": "healthy", "source": "CoreSSO"}
        mock_sme.return_value = {"status": "healthy", "source": "SME"}

        response = client.get("/api/etl/health/sources/")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    @patch("core.health._check_coresso_db")
    @patch("core.health._check_sme_integracao")
    def test_returns_degraded_when_one_source_unhealthy(self, mock_sme, mock_coresso, client):
        mock_coresso.return_value = {"status": "unhealthy"}
        mock_sme.return_value = {"status": "healthy"}

        response = client.get("/api/etl/health/sources/")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"




class TestHealthCoresso:
    @patch("core.health._check_coresso_db")
    def test_200_when_healthy(self, mock_check, client):
        mock_check.return_value = {"status": "healthy"}
        response = client.get("/api/etl/health/sources/coresso/")
        assert response.status_code == 200

    @patch("core.health._check_coresso_db")
    def test_503_when_unhealthy(self, mock_check, client):
        mock_check.return_value = {"status": "unhealthy"}
        response = client.get("/api/etl/health/sources/coresso/")
        assert response.status_code == 503




class TestHealthSmeIntegracao:
    @patch("core.health._check_sme_integracao")
    def test_200_when_healthy(self, mock_check, client):
        mock_check.return_value = {"status": "healthy"}
        response = client.get("/api/etl/health/sources/sme-integracao/")
        assert response.status_code == 200

    @patch("core.health._check_sme_integracao")
    def test_503_when_unhealthy(self, mock_check, client):
        mock_check.return_value = {"status": "unhealthy"}
        response = client.get("/api/etl/health/sources/sme-integracao/")
        assert response.status_code == 503




class TestCheckCoressoImportError:
    def test_importerror_path(self, settings):
        import sys
        settings.CORESSO_DB_SERVER = "10.49.19.159\\SQLSERVERHOMOLOG"
        settings.CORESSO_DB_NAME = "CoreSSO"
        settings.CORESSO_DB_USER = "user"
        settings.CORESSO_DB_PASSWORD = "pass"
        settings.CORESSO_DB_TIMEOUT = 5

        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.side_effect = ImportError("libodbc not found")

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from core.health import _check_coresso_db
            result = _check_coresso_db()

        # Should be 'error' or 'unhealthy' — either is fine; just must not raise
        assert result["status"] in ("error", "unhealthy", "not_configured")




class TestCheckSmeIntegrazioneWithAuth:
    def test_auth_ok_path(self, settings):
        import httpx
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-api"
        settings.SME_INTEGRACAO_TIMEOUT = 5
        settings.SME_INTEGRACAO_LOGIN = "testuser"
        settings.SME_INTEGRACAO_PASSWORD = "testpass"
        settings.SME_INTEGRACAO_API_KEY = ""

        from unittest.mock import MagicMock, patch, AsyncMock

        swagger_resp = MagicMock()
        swagger_resp.status_code = 200

        auth_resp = MagicMock()
        auth_resp.status_code = 200
        auth_resp.json.return_value = {"token": "tok123"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = swagger_resp
        mock_client.post.return_value = auth_resp

        with patch("core.health.httpx.Client", return_value=mock_client):
            from core.health import _check_sme_integracao
            result = _check_sme_integracao()

        assert result["status"] == "healthy"
        assert result["authentication"] == "ok"
        assert result["auth_token_present"] is True

    def test_auth_with_api_key(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-api"
        settings.SME_INTEGRACAO_TIMEOUT = 5
        settings.SME_INTEGRACAO_LOGIN = ""
        settings.SME_INTEGRACAO_PASSWORD = ""
        settings.SME_INTEGRACAO_API_KEY = "apikey123"

        from unittest.mock import MagicMock, patch

        swagger_resp = MagicMock()
        swagger_resp.status_code = 200
        data_resp = MagicMock()
        data_resp.status_code = 200

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [swagger_resp, data_resp]
        mock_client.post.return_value = MagicMock()

        with patch("core.health.httpx.Client", return_value=mock_client):
            from core.health import _check_sme_integracao
            result = _check_sme_integracao()

        assert result["status"] == "healthy"
        assert result["data_access"] == "ok"




class TestCheckSmeIntegrazioneAuthOnly:
    def test_not_configured_no_url(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = ""
        settings.SME_INTEGRACAO_LOGIN = "user"
        settings.SME_INTEGRACAO_PASSWORD = "pass"
        from core.health import _check_sme_integracao_auth_only
        result = _check_sme_integracao_auth_only()
        assert result["status"] == "not_configured"

    def test_not_configured_no_credentials(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-api"
        settings.SME_INTEGRACAO_LOGIN = ""
        settings.SME_INTEGRACAO_PASSWORD = ""
        from core.health import _check_sme_integracao_auth_only
        result = _check_sme_integracao_auth_only()
        assert result["status"] == "not_configured"

    def test_healthy_auth(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-api"
        settings.SME_INTEGRACAO_TIMEOUT = 5
        settings.SME_INTEGRACAO_LOGIN = "user"
        settings.SME_INTEGRACAO_PASSWORD = "pass"

        from unittest.mock import MagicMock, patch

        auth_resp = MagicMock()
        auth_resp.status_code = 200
        auth_resp.json.return_value = {"token": "tok123"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = auth_resp

        with patch("core.health.httpx.Client", return_value=mock_client):
            from core.health import _check_sme_integracao_auth_only
            result = _check_sme_integracao_auth_only()

        assert result["status"] == "healthy"
        assert result["authentication"] == "ok"
        assert result["auth_token_present"] is True

    def test_unhealthy_auth(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-api"
        settings.SME_INTEGRACAO_TIMEOUT = 5
        settings.SME_INTEGRACAO_LOGIN = "user"
        settings.SME_INTEGRACAO_PASSWORD = "badpass"

        from unittest.mock import MagicMock, patch

        auth_resp = MagicMock()
        auth_resp.status_code = 401

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = auth_resp

        with patch("core.health.httpx.Client", return_value=mock_client):
            from core.health import _check_sme_integracao_auth_only
            result = _check_sme_integracao_auth_only()

        assert result["status"] == "unhealthy"
        assert result["authentication"] == "failed"

    def test_exception_path(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-api"
        settings.SME_INTEGRACAO_TIMEOUT = 5
        settings.SME_INTEGRACAO_LOGIN = "user"
        settings.SME_INTEGRACAO_PASSWORD = "pass"

        from unittest.mock import patch

        with patch("core.health.httpx.Client", side_effect=Exception("network error")):
            from core.health import _check_sme_integracao_auth_only
            result = _check_sme_integracao_auth_only()

        assert result["status"] == "unhealthy"




class TestHealthSmeIntegrazioneAuth:
    @patch("core.health._check_sme_integracao_auth_only")
    def test_200_when_healthy(self, mock_check, client):
        mock_check.return_value = {"status": "healthy"}
        response = client.get("/api/etl/health/sources/sme-integracao/auth/")
        assert response.status_code == 200

    @patch("core.health._check_sme_integracao_auth_only")
    def test_503_when_unhealthy(self, mock_check, client):
        mock_check.return_value = {"status": "unhealthy"}
        response = client.get("/api/etl/health/sources/sme-integracao/auth/")
        assert response.status_code == 503

