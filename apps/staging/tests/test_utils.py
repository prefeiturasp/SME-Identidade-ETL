"""Testes para apps.staging.utils."""

from __future__ import annotations

import pytest

from apps.staging.utils import is_valid_cpf


@pytest.mark.parametrize(
    "cpf",
    [
        None,
        "",
        "abc",
        "123456789",
        "11111111111",
    ],
)
def test_rejeita_cpf_invalido(cpf: str | None) -> None:
    """Rejeita CPFs ausentes, incompletos ou com sequência repetida."""
    assert is_valid_cpf(cpf) is False


def test_aceita_cpf_formatado() -> None:
    """Aceita CPF válido com pontuação."""
    assert is_valid_cpf("529.982.247-25") is True


@pytest.mark.parametrize(
    "cpf",
    [
        "52998224715",
        "52998224726",
    ],
)
def test_rejeita_cpf_com_digito_verificador_invalido(cpf: str) -> None:
    """Rejeita CPF quando qualquer dígito verificador está incorreto."""
    assert is_valid_cpf(cpf) is False
