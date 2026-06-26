"""Classe base para commands de sincronização de usuário."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.extracao.tasks import buscar_dados_usuario_coresso


class BaseUsuarioCommand(BaseCommand):
    """Base com argumentos e helpers comuns."""

    def add_arguments_base(self, parser: Any) -> None:
        """Adiciona identificador, --realm e --saida."""
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

    def resolver_caminho(self, options: dict, prefixo: str) -> Path:
        """Resolve o caminho de saída do JSON."""
        if options["saida"]:
            return Path(options["saida"])
        pasta = Path("validacao_e2e")
        pasta.mkdir(exist_ok=True)
        ident = options["identificador"].strip()
        return pasta / f"{prefixo}_{ident}.json"

    def buscar_coresso(self, identificador: str, caminho: Path) -> dict | None:
        """Busca usuário no CoreSSO e trata não-encontrado."""
        self.stdout.write(f"Buscando '{identificador}' no CoreSSO...")
        dados = buscar_dados_usuario_coresso(identificador)
        if not dados:
            self.stdout.write(
                self.style.ERROR("Usuário não encontrado no CoreSSO.")
            )
            self.salvar_json(
                caminho,
                {
                    "identificador": identificador,
                    "erro": "não encontrado no CoreSSO",
                    "timestamp": timezone.now().isoformat(),
                },
            )
        return dados

    def exibir_encontrado(self, dados: dict) -> None:
        """Exibe resumo do usuário encontrado."""
        self.stdout.write(
            f"Encontrado: {dados['nome']}"
            f" | RF={dados['login']}"
            f" | CPF={dados['cpf'] or '—'}"
        )

    def salvar_resultado(
        self, caminho: Path, resultado: dict, dados: dict
    ) -> None:
        """Salva resultado final com dados CoreSSO e timestamp."""
        resultado["coresso"] = dados
        resultado["timestamp"] = timezone.now().isoformat()
        self.salvar_json(caminho, resultado)
        self.stdout.write(self.style.SUCCESS(f"Resultado salvo em {caminho}"))

    def salvar_json(self, caminho: Path, dados: dict) -> None:
        """Salva dicionário em arquivo JSON."""
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
