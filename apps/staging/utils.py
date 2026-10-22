"""Módulo para funções utilitárias."""


def is_valid_cpf(cpf: str | None) -> bool:
    """Valida CPF utilizando o algoritmo de validação oficial.

    Args:
        cpf: O CPF a ser validado.
    """
    import re

    if not cpf:
        return False

    cpf = re.sub(r"\D", "", cpf)

    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False

    for i in range(9, 11):
        soma = sum(int(cpf[j]) * (i + 1 - j) for j in range(i))
        digito = (soma * 10 % 11) % 10
        if digito != int(cpf[i]):
            return False

    return True
