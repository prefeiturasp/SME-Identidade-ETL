
"""Testes das consultas de dados de usuários no CoreSSO."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.extracao import consultas_coresso


@pytest.fixture
def conexao_mocada(
    settings: Any,
) -> Generator[tuple[MagicMock, MagicMock, MagicMock], None, None]:
    """Fornece conexão e cursor ODBC simulados."""
    settings.CORESSO_DB_SERVIDOR = "coresso-teste"
    settings.CORESSO_DB_TIMEOUT = 15

    cursor = MagicMock()
    conexao = MagicMock()
    conexao.cursor.return_value = cursor

    with (
        patch(
            "apps.extracao.consultas_coresso._string_conexao_coresso",
            return_value="CONEXAO_TESTE",
        ),
        patch(
            "apps.extracao.consultas_coresso.pyodbc.connect",
            return_value=conexao,
        ) as conectar,
    ):
        yield cursor, conexao, conectar


class TestSelecionarDadosUsuarioPorLogin:
    def test_retorna_dados_sem_normalizacao(
        self,
        conexao_mocada: tuple[MagicMock, MagicMock, MagicMock],
    ) -> None:
        cursor, conexao, conectar = conexao_mocada
        cursor.description = [
            ("Nome",),
            ("CPF",),
            ("Email",),
            ("CodigoRf",),
        ]
        cursor.fetchone.return_value = (
            "USUARIO TESTE",
            "00123456789",
            "email-invalido",
            "0001234567",
        )

        dados = consultas_coresso.selecionar_dados_usuario_por_login(
            "0001234567"
        )

        assert dados == {
            "nome": "USUARIO TESTE",
            "cpf": "00123456789",
            "email": "email-invalido",
            "codigo_rf": "0001234567",
        }
        conectar.assert_called_once_with("CONEXAO_TESTE", timeout=15)
        conexao.close.assert_called_once_with()
        cursor.close.assert_called_once_with()
        cursor.fetchall.assert_not_called()

    def test_consulta_usa_login_parametrizado_e_filtros_originais(
        self,
        conexao_mocada: tuple[MagicMock, MagicMock, MagicMock],
    ) -> None:
        cursor, _, _ = conexao_mocada
        cursor.fetchone.return_value = None

        consultas_coresso.selecionar_dados_usuario_por_login("1234567")

        sql, parametros = cursor.execute.call_args.args
        assert parametros == ("1234567",)
        assert "usr.usu_login = ?" in sql
        assert "usr.usu_situacao <> 3" in sql
        assert "INNER JOIN PES_Pessoa" in sql
        assert "INNER JOIN PES_PessoaDocumento" in sql
        assert "ORDER BY" not in sql.upper()
        assert "LEFT JOIN" not in sql.upper()

    def test_retorna_none_quando_nao_encontra_usuario(
        self,
        conexao_mocada: tuple[MagicMock, MagicMock, MagicMock],
    ) -> None:
        cursor, _, _ = conexao_mocada
        cursor.fetchone.return_value = None

        resultado = consultas_coresso.selecionar_dados_usuario_por_login(
            "inexistente"
        )

        assert resultado is None
        cursor.fetchone.assert_called_once_with()

    def test_preserva_campos_nulos(
        self,
        conexao_mocada: tuple[MagicMock, MagicMock, MagicMock],
    ) -> None:
        cursor, _, _ = conexao_mocada
        cursor.description = [
            ("Nome",),
            ("CPF",),
            ("Email",),
            ("CodigoRf",),
        ]
        cursor.fetchone.return_value = ("USUARIO", None, None, "1234567")

        dados = consultas_coresso.selecionar_dados_usuario_por_login(
            "1234567"
        )

        assert dados == {
            "nome": "USUARIO",
            "cpf": None,
            "email": None,
            "codigo_rf": "1234567",
        }

    def test_sem_configuracao_nao_abre_conexao(
        self,
        conexao_mocada: tuple[MagicMock, MagicMock, MagicMock],
        settings: Any,
    ) -> None:
        _, _, conectar = conexao_mocada
        settings.CORESSO_DB_SERVIDOR = ""

        with pytest.raises(consultas_coresso.ConsultaCoreSSOError):
            consultas_coresso.selecionar_dados_usuario_por_login("1234567")

        conectar.assert_not_called()

    def test_falha_de_conexao_lanca_excecao_especifica(
        self,
        conexao_mocada: tuple[MagicMock, MagicMock, MagicMock],
    ) -> None:
        _, _, conectar = conexao_mocada
        conectar.side_effect = consultas_coresso.pyodbc.Error(
            "falha de conexão"
        )

        with pytest.raises(consultas_coresso.ConsultaCoreSSOError):
            consultas_coresso.selecionar_dados_usuario_por_login("1234567")

    def test_falha_sql_lanca_excecao_e_fecha_recursos(
        self,
        conexao_mocada: tuple[MagicMock, MagicMock, MagicMock],
    ) -> None:
        cursor, conexao, _ = conexao_mocada
        cursor.execute.side_effect = consultas_coresso.pyodbc.Error(
            "erro de SQL"
        )

        with pytest.raises(consultas_coresso.ConsultaCoreSSOError):
            consultas_coresso.selecionar_dados_usuario_por_login("1234567")

        cursor.close.assert_called_once_with()
        conexao.close.assert_called_once_with()
