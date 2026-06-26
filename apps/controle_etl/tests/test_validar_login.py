"""Testes para o management command validar_login."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

_CMD = "apps.controle_etl.management.commands" ".validar_login"


def _fake_admin(user: dict | None = None) -> MagicMock:
    admin = MagicMock()
    if user:
        admin.get_users.return_value = [user]
    else:
        admin.get_users.return_value = []
    return admin


@pytest.mark.django_db
class TestValidarLogin:
    def test_usuario_nao_encontrado(self, tmp_path: Any) -> None:
        saida = tmp_path / "out.json"
        admin = _fake_admin(None)
        with patch(
            f"{_CMD}.obter_admin_keycloak",
            return_value=admin,
        ):
            out = StringIO()
            call_command(
                "validar_login",
                "inexistente",
                f"--saida={saida}",
                stdout=out,
            )
        assert "não encontrado" in out.getvalue()
        assert saida.exists()

    def test_login_com_sucesso(self, tmp_path: Any, settings: Any) -> None:
        settings.KEYCLOAK_URL_SERVIDOR = "https://kc/"
        settings.KEYCLOAK_VERIFICAR_SSL = False
        saida = tmp_path / "out.json"
        user_kc = {
            "id": "kc-123",
            "username": "7777777",
            "email": "j@sme.sp",
            "firstName": "Joao",
            "lastName": "Silva",
            "enabled": True,
            "attributes": {"rf": ["7777777"]},
        }
        admin = _fake_admin(user_kc)
        token_data = {
            "access_token": "eyJxyz",
            "refresh_token": "eyJabc",
            "token_type": "Bearer",
            "expires_in": 300,
            "scope": "openid",
        }
        userinfo = {
            "sub": "kc-123",
            "name": "Joao Silva",
        }

        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.Command._fazer_login",
                return_value=token_data,
            ),
            patch(
                f"{_CMD}.Command._obter_userinfo",
                return_value=userinfo,
            ),
            patch(
                f"{_CMD}.Command._obter_roles",
                return_value={
                    "realm_roles": ["default"],
                    "client_roles": {},
                },
            ),
        ):
            out = StringIO()
            call_command(
                "validar_login",
                "7777777",
                "--senha=test123",
                f"--saida={saida}",
                stdout=out,
            )
        texto = out.getvalue()
        assert "Login OK" in texto
        assert saida.exists()
        import json

        dados = json.loads(saida.read_text())
        assert dados["login_sucesso"] is True
        assert dados["token"]["access_token"] == "eyJxyz"

    def test_login_falha(self, tmp_path: Any, settings: Any) -> None:
        settings.KEYCLOAK_URL_SERVIDOR = "https://kc/"
        settings.KEYCLOAK_VERIFICAR_SSL = False
        saida = tmp_path / "out.json"
        user_kc = {
            "id": "kc-1",
            "username": "u1",
            "email": "",
            "firstName": "X",
            "lastName": "Y",
            "enabled": True,
        }
        admin = _fake_admin(user_kc)

        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.Command._fazer_login",
                return_value={"error": "invalid_grant"},
            ),
        ):
            out = StringIO()
            call_command(
                "validar_login",
                "u1",
                f"--saida={saida}",
                stdout=out,
            )
        assert "falhou" in out.getvalue()
        import json

        dados = json.loads(saida.read_text())
        assert dados["login_sucesso"] is False

    def test_senha_padrao_do_env(
        self, tmp_path: Any, settings: Any, monkeypatch: Any
    ) -> None:
        settings.KEYCLOAK_URL_SERVIDOR = "https://kc/"
        settings.KEYCLOAK_VERIFICAR_SSL = False
        monkeypatch.setenv("VALIDAR_LOGIN_SENHA_PADRAO", "Sgp0001")
        saida = tmp_path / "out.json"
        admin = _fake_admin(None)

        with patch(
            f"{_CMD}.obter_admin_keycloak",
            return_value=admin,
        ):
            call_command(
                "validar_login",
                "u1",
                f"--saida={saida}",
                stdout=StringIO(),
            )
        assert saida.exists()

    def test_erro_ao_resetar_senha(self, tmp_path: Any, settings: Any) -> None:
        settings.KEYCLOAK_URL_SERVIDOR = "https://kc/"
        settings.KEYCLOAK_VERIFICAR_SSL = False
        saida = tmp_path / "out.json"
        user_kc = {
            "id": "kc-1",
            "username": "u1",
            "email": "",
            "firstName": "X",
            "lastName": "Y",
            "enabled": True,
        }
        admin = _fake_admin(user_kc)
        admin.set_user_password.side_effect = Exception("403")

        with patch(
            f"{_CMD}.obter_admin_keycloak",
            return_value=admin,
        ):
            out = StringIO()
            call_command(
                "validar_login",
                "u1",
                f"--saida={saida}",
                stdout=out,
            )
        import json

        dados = json.loads(saida.read_text())
        assert dados["senha_resetada"] is False

    def test_busca_por_atributo_rf(self, tmp_path: Any, settings: Any) -> None:
        settings.KEYCLOAK_URL_SERVIDOR = "https://kc/"
        settings.KEYCLOAK_VERIFICAR_SSL = False
        saida = tmp_path / "out.json"

        user_kc = {
            "id": "kc-2",
            "username": "11122233344",
            "email": "",
            "firstName": "A",
            "lastName": "B",
            "enabled": True,
        }

        def get_users_side(query: dict) -> list:
            if query.get("exact"):
                return []
            if "rf:" in str(query.get("q", "")):
                return [user_kc]
            return []

        admin = MagicMock()
        admin.get_users.side_effect = get_users_side

        with (
            patch(
                f"{_CMD}.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                f"{_CMD}.Command._fazer_login",
                return_value={"error": "test"},
            ),
        ):
            call_command(
                "validar_login",
                "7777777",
                f"--saida={saida}",
                stdout=StringIO(),
            )
        import json

        dados = json.loads(saida.read_text())
        assert dados["kc_user_id"] == "kc-2"
