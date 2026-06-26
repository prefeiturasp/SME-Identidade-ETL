"""Comando Django para conceder acesso a sistema e roles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.controle_etl.orquestrador_kc import (
    conceder_acesso_kc,
    obter_admin_keycloak,
)
from apps.extracao.tasks import buscar_dados_usuario_coresso


class Command(BaseCommand):
    """Concede acesso a um sistema e roles no Keycloak."""

    help = (
        "Busca um usuário no CoreSSO por RF, CPF ou email,"
        " cria/atualiza no Keycloak e atribui os client"
        " roles informados para o sistema especificado."
    )

    def add_arguments(self, parser: Any) -> None:
        """Define os argumentos do comando."""
        parser.add_argument(
            "identificador",
            type=str,
            help="RF, CPF ou email do usuário.",
        )
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
        parser.add_argument(
            "--realm",
            type=str,
            default="sme-apps",
            help="Realm Keycloak. Padrão: sme-apps.",
        )
        parser.add_argument(
            "--saida",
            type=str,
            default="",
            help=(
                "Caminho do JSON. Padrão:"
                " validacao_e2e/{identificador}.json"
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Executa a concessão de acesso."""
        identificador = options["identificador"].strip()
        sis_id = options["sistema"]
        nomes_roles = options["roles"]
        realm = options["realm"]

        if options["saida"]:
            caminho = Path(options["saida"])
        else:
            pasta = Path("validacao_e2e")
            pasta.mkdir(exist_ok=True)
            caminho = pasta / f"acesso_{identificador}.json"

        self.stdout.write(f"Buscando '{identificador}' no CoreSSO...")
        dados = buscar_dados_usuario_coresso(identificador)
        if not dados:
            self.stdout.write(
                self.style.ERROR("Usuário não encontrado no CoreSSO.")
            )
            self._salvar(
                caminho,
                {
                    "identificador": identificador,
                    "erro": "não encontrado no CoreSSO",
                    "timestamp": timezone.now().isoformat(),
                },
            )
            return

        self.stdout.write(
            f"Encontrado: {dados['nome']}"
            f" | RF={dados['login']}"
            f" | CPF={dados['cpf'] or '—'}"
        )

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
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n{resultado['acao'].upper()}"
                    f" | username="
                    f"{resultado.get('username')}"
                    f" | sistema="
                    f"{resultado.get('sistema')}"
                    f" ({resultado.get('client_id')})"
                )
            )
            roles_ok = resultado.get("roles_atribuidos", [])
            if roles_ok:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Roles atribuídos:" f" {', '.join(roles_ok)}"
                    )
                )
            nao_enc = resultado.get("roles_nao_encontrados", [])
            if nao_enc:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Roles não encontrados:" f" {', '.join(nao_enc)}"
                    )
                )
            if resultado.get("erros"):
                self.stdout.write(
                    self.style.ERROR(f"  Erros: {resultado['erros']}")
                )

        self.stdout.write(f"\nKeycloak: {resultado.get('kc_url', '—')}")

        resultado["coresso"] = dados
        resultado["timestamp"] = timezone.now().isoformat()
        self._salvar(caminho, resultado)
        self.stdout.write(self.style.SUCCESS(f"Resultado salvo em {caminho}"))

    def _salvar(self, caminho: Path, dados: dict) -> None:
        """Salva resultado em JSON."""
        caminho.parent.mkdir(exist_ok=True)
        caminho.write_text(
            json.dumps(
                dados,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
