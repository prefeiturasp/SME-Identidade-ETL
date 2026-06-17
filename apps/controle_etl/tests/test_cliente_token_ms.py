"""Testes para apps.controle_etl.cliente_token_ms."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from apps.controle_etl.cliente_token_ms import enviar_lote, enviar_todos

# ---------------------------------------------------------------------------
# enviar_lote
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEnviarLote:
    """Testa o envio de um único lote de usuários ao token-ms."""

    def _mock_ok(self, json_body=None):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.content = b'{"situacao":"ok"}'
        resp.json.return_value = json_body or {"situacao": "ok"}
        resp.raise_for_status.return_value = None
        return resp

    def test_envia_post_com_payload_correto(self, settings):
        """Verifica que o POST é enviado com id_execucao e usuários."""
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TOKEN = "tok"
        settings.TOKEN_MS_TOKEN_INTERNO = ""
        settings.TOKEN_MS_TIMEOUT = 30

        resp = self._mock_ok()
        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = resp
            mock_cls.return_value = mock_client

            resultado = enviar_lote(
                [{"rf": "123", "cpf": "456"}], id_execucao="exec-1"
            )

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
        assert body["id_execucao"] == "exec-1"
        assert len(body["usuarios"]) == 1
        assert resultado == {"situacao": "ok"}

    def test_retorna_dict_ok_quando_resposta_vazia(self, settings):
        """Verifica que uma resposta 204 sem corpo retorna situação ok."""
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TOKEN = ""
        settings.TOKEN_MS_TOKEN_INTERNO = ""
        settings.TOKEN_MS_TIMEOUT = 30

        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 204
        resp.content = b""
        resp.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = resp
            mock_cls.return_value = mock_client

            resultado = enviar_lote([], id_execucao="exec-2")

        assert resultado == {"situacao": "ok"}

    def test_status_retriavel_levanta_excecao_apos_tentativas(self, settings):
        """Verifica que erro 503 persistente propaga HTTPStatusError."""
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TOKEN = ""
        settings.TOKEN_MS_TOKEN_INTERNO = ""
        settings.TOKEN_MS_TIMEOUT = 30

        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 503
        resp.request = MagicMock()

        def raise_503(*a, **kw):
            raise httpx.HTTPStatusError(
                "503", request=resp.request, response=resp
            )

        with patch("httpx.Client") as mock_cls, patch("time.sleep"):
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = raise_503
            mock_cls.return_value = mock_client

            with pytest.raises(httpx.HTTPStatusError):
                enviar_lote([], id_execucao="exec-3")

    def test_usa_token_interno_se_disponivel(self, settings):
        """Verifica que X-Internal-Token é usado quando configurado."""
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TOKEN = "ext-tok"
        settings.TOKEN_MS_TOKEN_INTERNO = "int-tok"
        settings.TOKEN_MS_TIMEOUT = 30

        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.content = b"{}"
        resp.json.return_value = {}
        resp.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = resp
            mock_cls.return_value = mock_client

            enviar_lote([], id_execucao="exec-4")

        headers = mock_client.post.call_args.kwargs.get("headers", {})
        assert headers.get("X-Internal-Token") == "int-tok"


# ---------------------------------------------------------------------------
# enviar_todos
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEnviarTodos:
    """Testa o envio de usuários em múltiplos lotes ao token-ms."""

    def test_divide_em_lotes_corretos(self, settings):
        """Verifica que os usuários são divididos no nº correto de lotes."""
        settings.TOKEN_MS_URL = "http://token-ms"
        settings.TOKEN_MS_TOKEN = ""
        settings.TOKEN_MS_TOKEN_INTERNO = ""
        settings.TOKEN_MS_TIMEOUT = 30
        settings.TOKEN_MS_TAMANHO_LOTE = 500

        usuarios = [{"rf": str(i)} for i in range(5)]

        with patch(
            "apps.controle_etl.cliente_token_ms.enviar_lote"
        ) as mock_lote:
            mock_lote.return_value = {"situacao": "ok"}
            resultado = enviar_todos(
                usuarios, id_execucao="exec-x", tamanho_lote=2
            )

        assert resultado["enviados"] == 5
        assert resultado["lotes"] == 3
        assert mock_lote.call_count == 3

    def test_iteravel_vazio_nao_chama_enviar_lote(self, settings):
        """Verifica que lista vazia de usuários não dispara envio."""
        settings.TOKEN_MS_TAMANHO_LOTE = 500

        with patch(
            "apps.controle_etl.cliente_token_ms.enviar_lote"
        ) as mock_lote:
            resultado = enviar_todos([], id_execucao="exec-y")

        mock_lote.assert_not_called()
        assert resultado["enviados"] == 0
        assert resultado["lotes"] == 0

    def test_usa_tamanho_lote_do_settings(self, settings):
        """Verifica que o tamanho de lote do settings é respeitado."""
        settings.TOKEN_MS_TAMANHO_LOTE = 3

        usuarios = [{"cpf": str(i)} for i in range(3)]
        with patch(
            "apps.controle_etl.cliente_token_ms.enviar_lote"
        ) as mock_lote:
            mock_lote.return_value = {"situacao": "ok"}
            resultado = enviar_todos(usuarios, id_execucao="exec-z")

        assert resultado["lotes"] == 1
