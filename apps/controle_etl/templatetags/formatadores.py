"""Filtros de formatacao para templates de controle do ETL."""

from decimal import Decimal, InvalidOperation
from typing import Any

from django import template

register = template.Library()

_NOMES_FONTES = {
    "todos": "Todas as fontes",
    "se1426": "SE1426",
    "coresso": "CoreSSO",
    "eol_alunos": "EOL DB",
    "eol_db_apoio": "EOL DB APOIO",
}


@register.filter(name="nome_fonte")
def nome_fonte(value: str) -> str:
    """Exiba o nome da base mantendo seu identificador interno."""
    return _NOMES_FONTES.get(value, value)


@register.filter(name="numero_br")
def numero_br(value: Any) -> Any:
    """Formata numeros com separador de milhar no padrao brasileiro."""
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return value

    if number != number.to_integral_value():
        return value

    return f"{int(number):,}".replace(",", ".")
