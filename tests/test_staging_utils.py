import pytest

from staging.utils import (
    build_dedup_key,
    normalize_cpf,
    normalize_rf,
    validate_cpf,
)

pytestmark = pytest.mark.django_db

class TestNormalizeCpf:
    """Testes para normalização de CPF."""

    def test_none_returns_none(self):
        """Deve retornar None quando o CPF for None."""
        assert normalize_cpf(None) is None

    def test_empty_string_returns_none(self):
        """Deve retornar None quando o CPF for vazio."""
        assert normalize_cpf("") is None

    def test_strips_punctuation(self):
        """Deve remover caracteres de pontuação do CPF."""
        assert normalize_cpf("123.456.789-09") == "12345678909"

    def test_already_digits(self):
        """Deve manter CPFs já normalizados."""
        assert normalize_cpf("12345678909") == "12345678909"

    def test_too_short_returns_none(self):
        """Deve retornar None para CPF com menos de 11 dígitos."""
        assert normalize_cpf("1234567890") is None

    def test_too_long_returns_none(self):
        """Deve retornar None para CPF com mais de 11 dígitos."""
        assert normalize_cpf("123456789012") is None

    def test_only_letters_returns_none(self):
        """Deve retornar None para valores sem dígitos."""
        assert normalize_cpf("abcdefghijk") is None


class TestValidateCpf:
    """Testes para validação de CPF."""

    def test_none_returns_false(self):
        """Deve retornar False quando o CPF for None."""
        assert validate_cpf(None) is False

    def test_empty_returns_false(self):
        """Deve retornar False quando o CPF for vazio."""
        assert validate_cpf("") is False

    def test_all_same_digits_invalid(self):
        """Deve considerar inválidos CPFs compostos pelo mesmo dígito."""
        for d in "0123456789":
            assert validate_cpf(d * 11) is False

    def test_valid_cpf(self):
        """Deve validar corretamente um CPF válido."""
        assert validate_cpf("52998224725") is True

    def test_invalid_first_digit(self):
        """Deve rejeitar CPF com primeiro dígito verificador inválido."""
        assert validate_cpf("52998224715") is False

    def test_invalid_second_digit(self):
        """Deve rejeitar CPF com segundo dígito verificador inválido."""
        assert validate_cpf("52998224724") is False

    def test_wrong_length_returns_false(self):
        """Deve rejeitar CPF com tamanho inválido."""
        assert validate_cpf("1234567890") is False

    def test_with_punctuation_valid(self):
        """Deve validar CPF mesmo contendo pontuação."""
        assert validate_cpf("529.982.247-25") is True

    def test_another_valid_cpf(self):
        """Deve validar corretamente outro CPF conhecido."""
        assert validate_cpf("11144477735") is True


class TestNormalizeRf:
    """Testes para normalização de RF."""

    def test_none_returns_none(self):
        """Deve retornar None quando o RF for None."""
        assert normalize_rf(None) is None

    def test_empty_returns_none(self):
        """Deve retornar None quando o RF for vazio."""
        assert normalize_rf("") is None

    def test_strips_leading_zeros(self):
        """Deve remover zeros à esquerda."""
        assert normalize_rf("007654") == "7654"

    def test_strips_whitespace(self):
        """Deve remover espaços em branco nas extremidades."""
        assert normalize_rf("  12345  ") == "12345"

    def test_all_zeros_returns_all_zeros(self):
        """Deve preservar RF composto apenas por zeros."""
        assert normalize_rf("000") == "000"

    def test_no_zeros_unchanged(self):
        """Deve manter RF sem zeros à esquerda."""
        assert normalize_rf("9999") == "9999"

    def test_alphanumeric_rf(self):
        """Deve aceitar RF alfanumérico."""
        result = normalize_rf("AB123")
        assert result is not None

    def test_alpha_rf_returns_cleaned_value(self):
        """Deve retornar RF textual sem espaços extras."""
        assert normalize_rf("  ABCDEF  ") == "ABCDEF"


class TestBuildDedupKey:
    """Testes para geração de chave de deduplicação."""

    def test_valid_cpf_priority(self):
        """Deve priorizar CPF válido na geração da chave."""
        key = build_dedup_key("52998224725", "12345")
        assert key == "cpf:52998224725"

    def test_invalid_cpf_falls_back_to_rf(self):
        """Deve utilizar RF quando o CPF for inválido."""
        key = build_dedup_key("00000000000", "12345")
        assert key == "rf:12345"

    def test_none_cpf_uses_rf(self):
        """Deve utilizar RF quando CPF não for informado."""
        key = build_dedup_key(None, "007654")
        assert key == "rf:7654"

    def test_both_none_returns_none(self):
        """Deve retornar None quando CPF e RF forem ausentes."""
        key = build_dedup_key(None, None)
        assert key is None

    def test_invalid_cpf_and_none_rf_returns_none(self):
        """Deve retornar None quando CPF for inválido e RF ausente."""
        key = build_dedup_key("00000000000", None)
        assert key is None

    def test_cpf_with_punctuation(self):
        """Deve gerar chave utilizando CPF normalizado."""
        key = build_dedup_key("529.982.247-25", None)
        assert key == "cpf:52998224725"
