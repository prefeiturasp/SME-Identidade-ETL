import pytest

from staging.utils import (
    build_dedup_key,
    normalize_cpf,
    normalize_rf,
    validate_cpf,
)




class TestNormalizeCpf:
    def test_none_returns_none(self):
        assert normalize_cpf(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_cpf("") is None

    def test_strips_punctuation(self):
        assert normalize_cpf("123.456.789-09") == "12345678909"

    def test_already_digits(self):
        assert normalize_cpf("12345678909") == "12345678909"

    def test_too_short_returns_none(self):
        assert normalize_cpf("1234567890") is None

    def test_too_long_returns_none(self):
        assert normalize_cpf("123456789012") is None

    def test_only_letters_returns_none(self):
        assert normalize_cpf("abcdefghijk") is None
    
    def test_alpha_rf_returns_cleaned_value(self):
        assert normalize_rf("  ABCDEF  ") == "ABCDEF"




class TestValidateCpf:
    def test_none_returns_false(self):
        assert validate_cpf(None) is False

    def test_empty_returns_false(self):
        assert validate_cpf("") is False

    def test_all_same_digits_invalid(self):
        for d in "0123456789":
            assert validate_cpf(d * 11) is False

    def test_valid_cpf(self):

        assert validate_cpf("52998224725") is True

    def test_invalid_first_digit(self):

        assert validate_cpf("52998224715") is False

    def test_invalid_second_digit(self):
        assert validate_cpf("52998224724") is False

    def test_wrong_length_returns_false(self):
        assert validate_cpf("1234567890") is False

    def test_with_punctuation_valid(self):

        assert validate_cpf("529.982.247-25") is True

    def test_another_valid_cpf(self):
        assert validate_cpf("11144477735") is True




class TestNormalizeRf:
    def test_none_returns_none(self):
        assert normalize_rf(None) is None

    def test_empty_returns_none(self):
        assert normalize_rf("") is None

    def test_strips_leading_zeros(self):
        assert normalize_rf("007654") == "7654"

    def test_strips_whitespace(self):
        assert normalize_rf("  12345  ") == "12345"

    def test_all_zeros_returns_all_zeros(self):

        assert normalize_rf("000") == "000"

    def test_no_zeros_unchanged(self):
        assert normalize_rf("9999") == "9999"

    def test_alphanumeric_rf(self):

        result = normalize_rf("AB123")
        assert result is not None




class TestBuildDedupKey:
    def test_valid_cpf_priority(self):
        key = build_dedup_key("52998224725", "12345")
        assert key == "cpf:52998224725"

    def test_invalid_cpf_falls_back_to_rf(self):
        key = build_dedup_key("00000000000", "12345")
        assert key == "rf:12345"

    def test_none_cpf_uses_rf(self):
        key = build_dedup_key(None, "007654")
        assert key == "rf:7654"

    def test_both_none_returns_none(self):
        key = build_dedup_key(None, None)
        assert key is None

    def test_invalid_cpf_and_none_rf_returns_none(self):
        key = build_dedup_key("00000000000", None)
        assert key is None

    def test_cpf_with_punctuation(self):

        key = build_dedup_key("529.982.247-25", None)
        assert key == "cpf:52998224725"
