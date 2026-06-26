"""Comando Django para conceder acesso a sistema e roles."""

from __future__ import annotations

from typing import Any

from apps.controle_etl.orquestrador_kc import (
    conceder_acesso_kc,
    obter_admin_keycloak,
)

from ._base import BaseUsuarioCommand


class Command(BaseUsuarioCommand):
    """Concede acesso a um sistema e roles no Keycloak."""

    help = (
        "Busca um usuário no CoreSSO por RF, CPF ou email,"
        " cria/atualiza no Keycloak e atribui os client"
        " roles informados para o sistema especificado."
    )

    def add_arguments(self, parser: Any) -> None:
        """Define os argumentos do comando."""
        self.add_arguments_base(parser)
        parser.add_argument(
            "--sistema",
            type=int,
            required=True,
            help="coresso_sis_id do sistema alvo.",
        )
        parser.add_argument(
            "--roles",
            nargs="+",
            required=True,
            help="Nomes dos perfis/roles a conceder.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Executa a concessão de acesso."""
        identificador = options["identificador"].strip()
        sis_id = options["sistema"]
        nomes_roles = options["roles"]
        realm = options["realm"]
        caminho = self.resolver_caminho(options, "acesso")

        dados = self.buscar_coresso(identificador, caminho)
        if not dados:
            return

        self.exibir_encontrado(dados)

        self.stdout.write(
            f"\nConcedendo acesso ao sistema {sis_id}"
            f" com roles: {', '.join(nomes_roles)}..."
        )
        admin = obter_admin_keycloak(realm=realm)
        resultado = conceder_acesso_kc(
            admin, dados, sis_id, nomes_roles, realm=realm
        )

        if resultado.get("erro"):
            self.stdout.write(self.style.ERROR(resultado["erro"]))
        else:
            self._exibir_sucesso(resultado)

        self.stdout.write(f"\nKeycloak: {resultado.get('kc_url', '—')}")
        self.salvar_resultado(caminho, resultado, dados)

    def _exibir_sucesso(self, resultado: dict) -> None:
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{resultado['acao'].upper()}"
                f" | username={resultado.get('username')}"
                f" | sistema={resultado.get('sistema')}"
                f" ({resultado.get('client_id')})"
            )
        )
        roles_ok = resultado.get("roles_atribuidos", [])
        if roles_ok:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Roles atribuídos: {', '.join(roles_ok)}"
                )
            )
        nao_enc = resultado.get("roles_nao_encontrados", [])
        if nao_enc:
            self.stdout.write(
                self.style.WARNING(
                    "  Roles não encontrados:" f" {', '.join(nao_enc)}"
                )
            )
        if resultado.get("erros"):
            self.stdout.write(
                self.style.ERROR(f"  Erros: {resultado['erros']}")
            )
