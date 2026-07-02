"""Ponto de entrada Django para linha de comando."""

import os
import sys


def main() -> None:
    """Executa comandos Django via linha de comando."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Não foi possível importar o Django. Verifique se está instalado "
            "e disponível na variável PYTHONPATH."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
