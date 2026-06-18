"""Comando Django para provisionar perfis CoreSSO como roles no Keycloak."""

from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand

from apps.controle_etl.orquestrador_kc import (
    obter_admin_keycloak,
    provisionar_role_client_kc,
)
from apps.staging.models import PerfilCoressoStaging


class Command(BaseCommand):
    """Provisiona perfis CoreSSO staging como client roles no Keycloak."""

    help = (
        "Provisiona perfis CoreSSO (PerfilCoressoStaging)"
        " como client roles no Keycloak."
    )

    def add_arguments(self, parser: Any) -> None:
        """Define os argumentos do comando."""
        parser.add_argument(
            "--sis-id",
            type=int,
            default=None,
            help="ID do sistema CoreSSO (coresso_sis_id). Omitir = todos.",
        )
        parser.add_argument(
            "--realm",
            type=str,
            default=None,
            help="Realm Keycloak. Omitir = valor de KEYCLOAK_REALM.",
        )

    def _provisionar_perfil(
        self,
        admin: Any,
        perfil: Any,
        contadores: dict[str, int],
        erros: list[tuple[str, str]],
    ) -> None:
        try:
            resultado = provisionar_role_client_kc(admin, perfil)
            acao = resultado["acao"]
            if acao == "criado":
                contadores["criados"] += 1
            elif acao == "atualizado":
                contadores["atualizados"] += 1
            else:
                contadores["ignorados"] += 1
        except Exception as exc:
            contadores["erros"] += 1
            if len(erros) < 10:
                erros.append(
                    (
                        perfil.kc_role_nome or perfil.nome,
                        str(exc)[:140],
                    )
                )

    def handle(self, *args: Any, **options: Any) -> None:
        """Executa o provisionamento de roles."""
        sis_id = options["sis_id"]
        realm = options["realm"]

        qs = PerfilCoressoStaging.objects.select_related("sistema").all()
        if sis_id is not None:
            qs = qs.filter(coresso_sis_id=sis_id)

        total = qs.count()
        self.stdout.write(
            f"Total de perfis: {total} | realm={realm or 'padrão'}"
        )

        admin = obter_admin_keycloak(realm=realm)
        contadores = {
            "criados": 0,
            "atualizados": 0,
            "ignorados": 0,
            "erros": 0,
        }
        primeiros_erros: list[tuple[str, str]] = []
        t0 = time.time()

        for idx, perfil in enumerate(qs.iterator()):
            if idx and idx % 50 == 0:
                self.stdout.write(
                    f"  [{idx}/{total}] {time.time() - t0:.1f}s"
                    f" | criados={contadores['criados']}"
                    f" atualizados={contadores['atualizados']}"
                    f" ignorados={contadores['ignorados']}"
                    f" erros={contadores['erros']}"
                )
                admin = obter_admin_keycloak(realm=realm)

            self._provisionar_perfil(
                admin, perfil, contadores, primeiros_erros
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nConcluído em {time.time() - t0:.1f}s:"
                f" criados={contadores['criados']}"
                f" atualizados={contadores['atualizados']}"
                f" ignorados={contadores['ignorados']}"
                f" erros={contadores['erros']}"
            )
        )

        if primeiros_erros:
            self.stdout.write(self.style.ERROR("\nPrimeiros erros:"))
            for nome, msg in primeiros_erros:
                self.stdout.write(f"  - {nome}: {msg}")
