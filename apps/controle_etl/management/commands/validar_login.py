"""Comando Django para testar login de um usuário no Keycloak.

Reseta a senha do usuário para um valor conhecido, efetua login
via OpenID Connect, decodifica o token e salva o resultado em
``validacao_login/{username}.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.controle_etl.orquestrador_kc import (
    _com_reintento,
    obter_admin_keycloak,
)


def _url_usuario_kc(kc_user_id: str, realm: str) -> str:
    base = settings.KEYCLOAK_URL_SERVIDOR.rstrip("/")
    return (
        f"{base}/admin/master/console/"
        f"#/{realm}/users/{kc_user_id}/settings"
    )


class Command(BaseCommand):
    """Testa login de um usuário no Keycloak."""

    help = (
        "Reseta a senha de um usuário, faz login via"
        " OpenID Connect e exibe/salva o token e userinfo."
    )

    def add_arguments(self, parser: Any) -> None:
        """Define os argumentos do comando."""
        parser.add_argument(
            "username",
            type=str,
            help=("Username do usuário (CPF, RF ou login)."),
        )
        parser.add_argument(
            "--senha",
            type=str,
            default=None,
            help=(
                "Senha a definir. Padrão:"
                " VALIDAR_LOGIN_SENHA_PADRAO do .env"
                " ou mesmo valor do username."
            ),
        )
        parser.add_argument(
            "--realm",
            type=str,
            default="sme-apps",
            help="Realm Keycloak. Padrão: sme-apps.",
        )
        parser.add_argument(
            "--client-id",
            type=str,
            default="admin-cli",
            help=(
                "Client ID para login (deve ter Direct"
                " Access Grants). Padrão: admin-cli."
            ),
        )
        parser.add_argument(
            "--client-secret",
            type=str,
            default="",
            help=(
                "Client secret. Vazio para clients" " públicos como admin-cli."
            ),
        )
        parser.add_argument(
            "--saida",
            type=str,
            default="",
            help=(
                "Caminho do JSON. Padrão:" " validacao_login/{username}.json"
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Executa o teste de login."""
        import os

        username = options["username"]
        senha = (
            options["senha"]
            or os.getenv("VALIDAR_LOGIN_SENHA_PADRAO")
            or username
        )
        realm = options["realm"]
        client_id = options["client_id"]
        client_secret = options["client_secret"]

        if options["saida"]:
            caminho_saida = Path(options["saida"])
        else:
            pasta = Path("validacao_login")
            pasta.mkdir(exist_ok=True)
            caminho_saida = pasta / f"{username}.json"

        resultado: dict[str, Any] = {
            "username": username,
            "realm": realm,
            "client_id": client_id,
            "timestamp": timezone.now().isoformat(),
        }

        self.stdout.write(
            f"Usuário: {username} | realm={realm}" f" | client={client_id}"
        )

        admin = obter_admin_keycloak(realm=realm)
        kc_user = self._buscar_usuario(admin, username)
        if not kc_user:
            resultado["erro"] = "Usuário não encontrado"
            self._salvar(caminho_saida, resultado)
            self.stdout.write(
                self.style.ERROR("Usuário não encontrado no Keycloak")
            )
            return

        kc_user_id = str(kc_user["id"])
        resultado["kc_user_id"] = kc_user_id
        resultado["kc_url"] = _url_usuario_kc(kc_user_id, realm)
        resultado["usuario_kc"] = {
            "username": kc_user.get("username"),
            "email": kc_user.get("email"),
            "firstName": kc_user.get("firstName"),
            "lastName": kc_user.get("lastName"),
            "enabled": kc_user.get("enabled"),
            "attributes": kc_user.get("attributes", {}),
        }
        self.stdout.write(
            f"Encontrado: {kc_user.get('firstName')}"
            f" {kc_user.get('lastName')}"
            f" (id={kc_user_id})"
        )

        self.stdout.write("Preparando conta para login...")
        try:
            update_payload: dict[str, Any] = {
                "requiredActions": [],
                "emailVerified": True,
            }
            if not kc_user.get("email"):
                fallback_email = f"{username}@validacao.local"
                update_payload["email"] = fallback_email
                self.stdout.write(f"  Email ausente — usando {fallback_email}")
            _com_reintento(admin.update_user, kc_user_id, update_payload)
            _com_reintento(
                admin.set_user_password,
                kc_user_id,
                senha,
                temporary=False,
            )
            resultado["senha_resetada"] = True
            self.stdout.write("  Senha definida OK")
        except Exception as exc:
            resultado["senha_resetada"] = False
            resultado["erro_senha"] = str(exc)
            self.stdout.write(self.style.ERROR(f"  Erro: {exc}"))
            self._salvar(caminho_saida, resultado)
            return

        self.stdout.write("Efetuando login...")
        token_data = self._fazer_login(
            username=kc_user.get("username", username),
            senha=senha,
            realm=realm,
            client_id=client_id,
            client_secret=client_secret,
        )
        if "error" in token_data:
            resultado["login_sucesso"] = False
            resultado["erro_login"] = token_data
            self.stdout.write(
                self.style.ERROR(f"  Login falhou: {token_data}")
            )
            self._salvar(caminho_saida, resultado)
            return

        resultado["login_sucesso"] = True
        resultado["token"] = {
            "access_token": token_data.get("access_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "token_type": token_data.get("token_type"),
            "expires_in": token_data.get("expires_in"),
            "scope": token_data.get("scope"),
        }
        self.stdout.write(self.style.SUCCESS("  Login OK"))

        self.stdout.write("Obtendo userinfo...")
        userinfo = self._obter_userinfo(
            token_data.get("access_token", ""),
            realm=realm,
            client_id=client_id,
            client_secret=client_secret,
        )
        resultado["userinfo"] = userinfo
        self.stdout.write(
            f"  sub={userinfo.get('sub')}" f" | name={userinfo.get('name')}"
        )

        self.stdout.write("Obtendo roles...")
        roles_info = self._obter_roles(admin, kc_user_id)
        resultado["roles"] = roles_info

        self._salvar(caminho_saida, resultado)
        self.stdout.write(
            self.style.SUCCESS(f"\nResultado salvo em {caminho_saida}")
        )

    def _buscar_usuario(
        self, admin: Any, username: str
    ) -> dict[str, Any] | None:
        """Busca por username, atributo rf ou cpf."""
        for query in [
            {"username": username, "exact": True},
            {"q": f"rf:{username}"},
            {"q": f"cpf:{username}"},
        ]:
            users = admin.get_users(query=query)
            if users:
                result: dict[str, Any] = users[0]
                return result
        return None

    def _fazer_login(
        self,
        *,
        username: str,
        senha: str,
        realm: str,
        client_id: str,
        client_secret: str,
    ) -> dict:
        """Efetua login via OpenID Connect."""
        from keycloak import KeycloakOpenID

        kc_openid = KeycloakOpenID(
            server_url=settings.KEYCLOAK_URL_SERVIDOR,
            client_id=client_id,
            realm_name=realm,
            client_secret_key=client_secret,
            verify=settings.KEYCLOAK_VERIFICAR_SSL,
        )
        try:
            return kc_openid.token(username, senha)
        except Exception as exc:
            return {"error": str(exc)}

    def _obter_userinfo(
        self,
        access_token: str,
        *,
        realm: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        """Obtém userinfo do token."""
        from keycloak import KeycloakOpenID

        kc_openid = KeycloakOpenID(
            server_url=settings.KEYCLOAK_URL_SERVIDOR,
            client_id=client_id,
            realm_name=realm,
            client_secret_key=client_secret,
            verify=settings.KEYCLOAK_VERIFICAR_SSL,
        )
        try:
            info = kc_openid.userinfo(access_token)
            return info if isinstance(info, dict) else {}
        except Exception as exc:
            return {"error": str(exc)}

    def _obter_roles(self, admin: Any, kc_user_id: str) -> dict[str, Any]:
        """Obtém realm roles e client roles do usuário."""
        from apps.staging.models import SistemaStaging

        info: dict[str, Any] = {}

        try:
            realm_roles = admin.get_realm_roles_of_user(kc_user_id)
            info["realm_roles"] = [r["name"] for r in realm_roles]
        except Exception:
            info["realm_roles"] = []

        info["client_roles"] = {}
        for s in SistemaStaging.objects.filter(
            kc_client_uuid__isnull=False
        ).exclude(kc_client_uuid=""):
            try:
                croles = admin.get_client_roles_of_user(
                    kc_user_id, s.kc_client_uuid
                )
                if croles:
                    info["client_roles"][s.kc_client_id or s.nome] = [
                        r["name"] for r in croles
                    ]
            except Exception:
                pass

        return info

    def _salvar(self, caminho: Path, dados: dict) -> None:
        """Salva resultado em JSON."""
        caminho.parent.mkdir(exist_ok=True)
        caminho.write_text(
            json.dumps(dados, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
