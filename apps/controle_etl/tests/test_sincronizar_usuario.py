"""Testes para o management command sincronizar_usuario."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

_BASE = "apps.controle_etl.management.commands._base"
_CMD = "apps.controle_etl.management.commands.sincronizar_usuario"


@pytest.mark.django_db
class TestSincronizarUsuario:
    def test_usuario_nao_encontrado(self, tmp_path: Any) -> None:
        saida_json = tmp_path / "out.json"
        with patch(
            f"{_BASE}.buscar_dados_usuario_coresso",
            return_value=None,
        ):
            out = StringIO()
            call_command(
                "sincronizar_usuario",
                "inexistente",
                f"--saida={saida_json}",
                stdout=out,
            )
        assert "não encontrado" in out.getvalue()
        assert saida_json.exists()

    def test_sincroniza_com_sucesso(self, tmp_path: Any) -> None:
        saida_json = tmp_path / "out.json"
        dados = {
            "login": "7777777",
            "cpf": "11122233344",
            "nome": "Joao Silva",
            "email": "j@sme.sp",
            "situacao": "ativo",
            "sistemas": {
                42: {
                    "sis_id": 42,
                    "nome": "SGP",
                    "grupos": [
                        {
                            "gru_id": "g1",
                            "nome": "Admin",
                        },
                    ],
                },
            },
        }
        resultado_kc = {
            "acao": "criado",
            "kc_user_id": "kc-1",
            "kc_url": "https://kc/users/kc-1/settings",
            "username": "11122233344",
            "nome": "Joao Silva",
            "roles_atribuidos": 1,
            "roles_erros": 0,
            "sistemas": [
                {
                    "sistema": "SGP",
                    "client_id": "sgp-qa",
                    "roles": ["Admin"],
                },
            ],
        }
        with (
            patch(
                f"{_BASE}.buscar_dados_usuario_coresso",
                return_value=dados,
            ),
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_CMD}.sincronizar_usuario_kc",
                return_value=resultado_kc,
            ),
        ):
            out = StringIO()
            call_command(
                "sincronizar_usuario",
                "7777777",
                f"--saida={saida_json}",
                stdout=out,
            )
        texto = out.getvalue()
        assert "CRIADO" in texto
        assert "roles=1" in texto
        assert saida_json.exists()
