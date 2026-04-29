import re


def normalize_cpf(cpf: str | None) -> str | None:
    if not cpf:
        return None
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11:
        return None
    return digits


def validate_cpf(cpf: str | None) -> bool:
    if not cpf:
        return False

    digits = re.sub(r"\D", "", cpf)

    if len(digits) != 11:
        return False

    if digits == digits[0] * 11:
        return False

    total = sum(int(digits[i]) * (10 - i) for i in range(9))
    remainder = total % 11
    dv1 = 0 if remainder < 2 else 11 - remainder

    if int(digits[9]) != dv1:
        return False

    total = sum(int(digits[i]) * (11 - i) for i in range(10))
    remainder = total % 11
    dv2 = 0 if remainder < 2 else 11 - remainder

    if int(digits[10]) != dv2:
        return False

    return True


def normalize_rf(rf: str | None) -> str | None:
    if not rf:
        return None
    cleaned = rf.strip()
    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        return cleaned  # RF alfanumérico (raro)
    return digits.lstrip("0") or digits


def build_dedup_key(cpf: str | None, rf: str | None) -> str | None:
    cpf_norm = normalize_cpf(cpf)
    if cpf_norm and validate_cpf(cpf_norm):
        return f"cpf:{cpf_norm}"

    rf_norm = normalize_rf(rf)
    if rf_norm:
        return f"rf:{rf_norm}"

    return None
