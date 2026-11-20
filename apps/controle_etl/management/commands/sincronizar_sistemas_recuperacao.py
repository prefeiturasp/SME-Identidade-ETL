"""Sincroniza o cadastro de recuperação no MS-Identidade."""

from typing import Any

from django.core.management.base import BaseCommand

from apps.controle_etl.tasks import (
    sincronizar_sistemas_recuperacao,
)


class Command(BaseCommand):
    """Publica códigos, nomes e URLs, sem acessar o banco do MS-Token."""

    help = (
        "Sincroniza configurações de recuperação de senha "
        "do EOL DB APOIO para o MS-Identidade."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        """Executa uma carga idempotente do cadastro de sistemas."""
        resultado = sincronizar_sistemas_recuperacao()
        self.stdout.write(self.style.SUCCESS(str(resultado)))
