"""Comando Django para sincronizar um usuário no Keycloak."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.controle_etl.orquestrador_kc import (
    obter_admin_keycloak,
    sincronizar_usuario_kc,
)
from apps.extracao.tasks import buscar_dados_usuario_coresso


class Command(BaseCommand):
    """Sincroniza um usuário no Keycloak com todos os roles."""

    help = (
        "Busca um usuário no CoreSSO por RF, CPF ou email,"
        " cria/atualiza no Keycloak e atribui todos os"
        " client roles dos sistemas associados."
    )

    def add_arguments(self, parser: Any) -> None:
        """Define os argumentos do comando."""
        parser.add_argument(
            "identificador",
            type=str,
            help="RF, CPF ou email do usuário.",
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
        """Executa a sincronização."""
        identificador = options["identificador"].strip()
        realm = options["realm"]

        if options["saida"]:
            caminho = Path(options["saida"])
        else:
            pasta = Path("validacao_e2e")
            pasta.mkdir(exist_ok=True)
            caminho = pasta / f"usuario_{identificador}.json"

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
            f" | {len(dados['sistemas'])} sistemas"
        )

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
