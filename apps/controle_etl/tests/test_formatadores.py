"""Testes dos filtros de template de controle do ETL."""

from django.test import SimpleTestCase

from apps.controle_etl.templatetags.formatadores import numero_br


class NumeroBrFilterTestCase(SimpleTestCase):
    """Valida formatação numérica em padrão brasileiro."""

    def test_formata_inteiros_com_separador_de_milhar(self) -> None:
        """Números inteiros devem usar ponto como separador de milhar."""
        self.assertEqual(numero_br(1000), "1.000")
        self.assertEqual(numero_br("1500000"), "1.500.000")

    def test_preserva_valores_nao_numericos(self) -> None:
        """Valores não numéricos não devem ser alterados."""
        self.assertEqual(numero_br("abc"), "abc")
        self.assertEqual(numero_br("10.5"), "10.5")

    def test_retorna_vazio_para_none(self) -> None:
        """None deve retornar string vazia."""
        self.assertEqual(numero_br(None), "")

    def test_retorna_vazio_para_string_vazia(self) -> None:
        """String vazia ou apenas espaços deve retornar string vazia."""
        self.assertEqual(numero_br(""), "")
        self.assertEqual(numero_br("   "), "")
