"""Testes para o management command conceder_acesso."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

_BASE = "apps.controle_etl.management.commands._base"
_CMD = "apps.controle_etl.management.commands.conceder_acesso"


@pytest.mark.django_db
class TestConcederAcesso:
    def test_usuario_nao_encontrado(self, tmp_path: Any) -> None:
        saida_json = tmp_path / "out.json"
        with patch(
            f"{_BASE}.buscar_dados_usuario_coresso",
            return_value=None,
        ):
            out = StringIO()
            call_command(
                "conceder_acesso",
                "inexistente",
                "--sistema=1008",
                "--roles",
                "ASCOM",
                f"--saida={saida_json}",
                stdout=out,
            )
        assert "não encontrado" in out.getvalue()
        assert saida_json.exists()

    def test_concede_com_sucesso(self, tmp_path: Any) -> None:
        saida_json = tmp_path / "out.json"
        dados = {
            "login": "7777777",
            "cpf": "11122233344",
            "nome": "Joao Silva",
            "email": "j@sme.sp",
            "situacao": "ativo",
            "sistemas": {},
        }
        resultado_kc = {
            "acao": "atualizado",
            "kc_user_id": "kc-1",
            "kc_url": "https://kc/users/kc-1/settings",
            "username": "7777777",
            "nome": "Joao Silva",
            "sistema": "SIGPAE",
            "client_id": "sigpae-qa",
            "roles_atribuidos": ["ASCOM"],
            "roles_nao_encontrados": [],
            "erros": 0,
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
                f"{_CMD}.conceder_acesso_kc",
                return_value=resultado_kc,
            ),
        ):
            out = StringIO()
            call_command(
                "conceder_acesso",
                "7777777",
                "--sistema=1008",
                "--roles",
                "ASCOM",
                f"--saida={saida_json}",
                stdout=out,
            )
        texto = out.getvalue()
        assert "ATUALIZADO" in texto
        assert "ASCOM" in texto
        assert saida_json.exists()

    def test_roles_nao_encontrados(self, tmp_path: Any) -> None:
        saida_json = tmp_path / "out.json"
        dados = {
            "login": "7777777",
            "cpf": "",
            "nome": "Maria",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        resultado_kc = {
            "acao": "criado",
            "kc_user_id": "kc-2",
            "kc_url": "https://kc/users/kc-2/settings",
            "username": "7777777",
            "nome": "Maria",
            "sistema": "SIGPAE",
            "client_id": "sigpae-qa",
            "roles_atribuidos": [],
            "roles_nao_encontrados": ["INEXISTENTE"],
            "erros": 0,
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
                f"{_CMD}.conceder_acesso_kc",
                return_value=resultado_kc,
            ),
        ):
            out = StringIO()
            call_command(
                "conceder_acesso",
                "7777777",
                "--sistema=1008",
                "--roles",
                "INEXISTENTE",
                f"--saida={saida_json}",
                stdout=out,
            )
        texto = out.getvalue()
        assert "não encontrados" in texto.lower()

    def test_erro_sistema_sem_client(self, tmp_path: Any) -> None:
        saida_json = tmp_path / "out.json"
        dados = {
            "login": "7777777",
            "cpf": "",
            "nome": "Carlos",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        resultado_kc = {
            "acao": "criado",
            "kc_user_id": "kc-3",
            "erro": "Sistema sis_id=9999 não encontrado"
            " ou sem client no Keycloak.",
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
                f"{_CMD}.conceder_acesso_kc",
                return_value=resultado_kc,
            ),
        ):
            out = StringIO()
            call_command(
                "conceder_acesso",
                "7777777",
                "--sistema=9999",
                "--roles",
                "X",
                f"--saida={saida_json}",
                stdout=out,
            )
        texto = out.getvalue()
        assert "não encontrado" in texto.lower()
