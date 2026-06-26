"""Comando Django para sincronizar um usuário no Keycloak."""

from __future__ import annotations

from typing import Any

from apps.controle_etl.orquestrador_kc import (
    obter_admin_keycloak,
    sincronizar_usuario_kc,
)

from ._base import BaseUsuarioCommand


class Command(BaseUsuarioCommand):
    """Sincroniza um usuário no Keycloak com todos os roles."""

    help = (
        "Busca um usuário no CoreSSO por RF, CPF ou email,"
        " cria/atualiza no Keycloak e atribui todos os"
        " client roles dos sistemas associados."
    )

    def add_arguments(self, parser: Any) -> None:
        """Define os argumentos do comando."""
        self.add_arguments_base(parser)

    def handle(self, *args: Any, **options: Any) -> None:
        """Executa a sincronização."""
        identificador = options["identificador"].strip()
        realm = options["realm"]
        caminho = self.resolver_caminho(options, "usuario")

        dados = self.buscar_coresso(identificador, caminho)
        if not dados:
            return

        self.exibir_encontrado(dados)
        self.stdout.write(f" | {len(dados['sistemas'])} sistemas")

        for sis in dados["sistemas"].values():
            grupos = ", ".join(g["nome"] for g in sis["grupos"])
            self.stdout.write(f"  {sis['nome']}: {grupos}")

        self.stdout.write("\nSincronizando no Keycloak...")
        admin = obter_admin_keycloak(realm=realm)
        resultado = sincronizar_usuario_kc(admin, dados, realm=realm)

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{resultado['acao'].upper()}"
                f" | username={resultado.get('username')}"
                f" | roles={resultado['roles_atribuidos']}"
                f" | erros={resultado['roles_erros']}"
            )
        )

        for sis in resultado.get("sistemas", []):
            roles = ", ".join(sis.get("roles", []))
            status_sis = sis.get("status", "")
            if status_sis:
                self.stdout.write(f"  {sis['sistema']}: {status_sis}")
            elif roles:
                self.stdout.write(
                    f"  {sis['sistema']}"
                    f" ({sis.get('client_id', '')})"
                    f": {roles}"
                )

        self.stdout.write(f"\nKeycloak: {resultado.get('kc_url', '—')}")
        self.salvar_resultado(caminho, resultado, dados)
