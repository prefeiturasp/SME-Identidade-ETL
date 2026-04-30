import pytest
from unittest.mock import MagicMock, patch


class TestSendBatch:

    def _make_response(self, status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.content = b'{"status": "ok"}' if json_data is None else b"content"
        resp.json.return_value = json_data or {"status": "ok"}
        resp.request = MagicMock()
        resp.response = resp
        return resp

    @patch("core.token_ms_client.httpx.Client")
    def test_success_200(self, mock_client_cls, settings):
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TIMEOUT = 5
        settings.TOKEN_MS_INTERNAL_TOKEN = "tok"
        settings.TOKEN_MS_TOKEN = "tok"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = self._make_response(200, {"status": "ok"})
        mock_client_cls.return_value = mock_client

        from core.token_ms_client import send_batch
        result = send_batch([{"cpf": "123"}], execution_id="exec-1")
        assert result["status"] == "ok"

    @patch("core.token_ms_client.httpx.Client")
    def test_empty_content_returns_ok(self, mock_client_cls, settings):
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TIMEOUT = 5
        settings.TOKEN_MS_INTERNAL_TOKEN = "tok"
        settings.TOKEN_MS_TOKEN = "tok"

        resp = MagicMock()
        resp.status_code = 200
        resp.content = b""
        resp.raise_for_status = MagicMock()
        resp.request = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = resp
        mock_client_cls.return_value = mock_client

        from core.token_ms_client import send_batch
        result = send_batch([{"cpf": "123"}], execution_id="exec-1")
        assert result == {"status": "ok"}

    @patch("core.token_ms_client.time.sleep", return_value=None)
    @patch("core.token_ms_client.httpx.Client")
    def test_retryable_status_raises_after_retries(self, mock_client_cls, mock_sleep, settings):
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TIMEOUT = 5
        settings.TOKEN_MS_INTERNAL_TOKEN = "tok"
        settings.TOKEN_MS_TOKEN = "tok"

        import httpx

        resp = MagicMock()
        resp.status_code = 503
        resp.request = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = resp
        mock_client_cls.return_value = mock_client

        from core.token_ms_client import send_batch
        with pytest.raises(httpx.HTTPStatusError):
            send_batch([{"cpf": "123"}], execution_id="exec-1", max_retries=2, base_delay=0.001)

    @patch("core.token_ms_client.time.sleep", return_value=None)
    @patch("core.token_ms_client.httpx.Client")
    def test_transport_error_retries(self, mock_client_cls, mock_sleep, settings):
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TIMEOUT = 5
        settings.TOKEN_MS_INTERNAL_TOKEN = "tok"
        settings.TOKEN_MS_TOKEN = "tok"

        import httpx

        call_count = {"n": 0}

        def post_side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise httpx.TransportError("connection refused")
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b'{"ok": 1}'
            resp.json.return_value = {"ok": 1}
            resp.raise_for_status = MagicMock()
            return resp

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = post_side_effect
        mock_client_cls.return_value = mock_client

        from core.token_ms_client import send_batch
        result = send_batch([{"cpf": "123"}], execution_id="exec-1", max_retries=5, base_delay=0.001)
        assert result["ok"] == 1


class TestSendAll:
    @patch("core.token_ms_client.send_batch")
    def test_sends_in_batches(self, mock_send_batch, settings):
        settings.TOKEN_MS_BATCH_SIZE = 3
        mock_send_batch.return_value = {"status": "ok"}

        from core.token_ms_client import send_all
        users = [{"cpf": str(i)} for i in range(7)]
        result = send_all(users, execution_id="exec-1", batch_size=3)
        assert mock_send_batch.call_count == 3  # 3 + 3 + 1
        assert result["sent"] == 7
        assert result["batches"] == 3

    @patch("core.token_ms_client.send_batch")
    def test_empty_users(self, mock_send_batch, settings):
        settings.TOKEN_MS_BATCH_SIZE = 100
        from core.token_ms_client import send_all
        result = send_all([], execution_id="exec-1")
        mock_send_batch.assert_not_called()
        assert result["sent"] == 0

    @patch("core.token_ms_client.send_batch")
    def test_single_batch(self, mock_send_batch, settings):
        settings.TOKEN_MS_BATCH_SIZE = 100
        mock_send_batch.return_value = {"status": "ok"}
        from core.token_ms_client import send_all
        users = [{"cpf": "123"}, {"cpf": "456"}]
        result = send_all(users, execution_id="exec-1", batch_size=100)
        assert mock_send_batch.call_count == 1
        assert result["sent"] == 2


class TestHeaders:
    def test_headers_with_internal_token(self, settings):
        settings.TOKEN_MS_INTERNAL_TOKEN = "my-internal-token"
        settings.TOKEN_MS_TOKEN = "fallback"
        from core.token_ms_client import _headers
        h = _headers()
        assert h["X-Internal-Token"] == "my-internal-token"

    def test_headers_fallback_token(self, settings):
        settings.TOKEN_MS_INTERNAL_TOKEN = ""
        settings.TOKEN_MS_TOKEN = "fallback-token"
        from core.token_ms_client import _headers
        h = _headers()
        assert h["X-Internal-Token"] == "fallback-token"

    def test_headers_no_token(self, settings):
        settings.TOKEN_MS_INTERNAL_TOKEN = ""
        settings.TOKEN_MS_TOKEN = ""
        from core.token_ms_client import _headers
        h = _headers()
        assert "X-Internal-Token" not in h
