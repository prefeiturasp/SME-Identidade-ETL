"""Testes para apps.extracao.tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.extracao.tasks import (
    RegistroIdentidade,
    _atualizar_watermark,
    _iterar_com_watermark,
    _montar_resultado_usuario,
    _obter_watermark,
    _slugificar_role,
    _string_conexao_coresso,
    _string_conexao_se1426,
    buscar_dados_usuario_coresso,
    buscar_grupos_coresso_por_login,
    extrair_coresso,
    extrair_eol_alunos,
    extrair_perfis_coresso,
    extrair_se1426,
    extrair_sistemas_coresso,
    extrair_vinculos_usuario_grupo_coresso,
)

# ---------------------------------------------------------------------------
# RegistroIdentidade
# ---------------------------------------------------------------------------


class TestRegistroIdentidade:
    def test_campos_obrigatorios(self) -> None:
        reg = RegistroIdentidade(fonte="se1426", tipo="servidor")
        assert reg.fonte == "se1426"
        assert reg.tipo == "servidor"
        assert reg.rf is None
        assert reg.dados_extras == {}

    def test_campos_completos(self) -> None:
        reg = RegistroIdentidade(
            fonte="coresso",
            tipo="terceiro",
            cpf="12345678901",
            nome="João Silva",
            email="joao@sme.sp.gov.br",
            situacao="ativo",
            tipo_acesso="legado-coresso",
            dados_extras={"login": "joao"},
        )
        assert reg.cpf == "12345678901"
        assert reg.dados_extras["login"] == "joao"

    def test_defaults_isolados(self) -> None:
        r1 = RegistroIdentidade(fonte="a", tipo="b")
        r2 = RegistroIdentidade(fonte="c", tipo="d")
        r1.dados_extras["x"] = 1
        assert "x" not in r2.dados_extras


# ---------------------------------------------------------------------------
# _string_conexao_se1426 / _string_conexao_coresso
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStringConexao:
    def test_se1426_contem_driver(self, settings: Any) -> None:
        settings.SE1426_DB_SERVIDOR = "srv-se"
        settings.SE1426_DB_NOME = "se1426"
        settings.SE1426_DB_USUARIO = "usr"
        settings.SE1426_DB_SENHA = "pwd"
        conn = _string_conexao_se1426()
        assert "FreeTDS" in conn
        assert "srv-se" in conn
        assert "se1426" in conn

    def test_coresso_contem_driver(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv-core"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        conn = _string_conexao_coresso()
        assert "FreeTDS" in conn
        assert "srv-core" in conn


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWatermark:
    def test_obter_watermark_sem_registro_retorna_none(self) -> None:
        resultado = _obter_watermark("fonte_inexistente")
        assert resultado is None

    def test_atualizar_e_obter_watermark(self) -> None:
        agora = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
        _atualizar_watermark("se1426", agora, total_processado=100)
        recuperado = _obter_watermark("se1426")
        assert recuperado is not None

    def test_atualizar_watermark_acumula_total(self) -> None:
        from apps.controle_etl.models import MarcaDaguaExtracao

        _atualizar_watermark(
            "coresso",
            datetime(2026, 1, 1, tzinfo=UTC),
            total_processado=50,
        )
        _atualizar_watermark(
            "coresso",
            datetime(2026, 1, 2, tzinfo=UTC),
            total_processado=30,
        )
        marca = MarcaDaguaExtracao.objects.get(fonte="coresso")
        assert marca.total_processado == 80

    def test_atualizar_watermark_salva_pagina(self) -> None:
        from apps.controle_etl.models import MarcaDaguaExtracao

        _atualizar_watermark(
            "eol_alunos",
            datetime(2026, 1, 1, tzinfo=UTC),
            total_processado=200,
            ultima_pagina=4,
        )
        marca = MarcaDaguaExtracao.objects.get(fonte="eol_alunos")
        assert marca.ultima_pagina == 4


# ---------------------------------------------------------------------------
# _iterar_com_watermark
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIterarComWatermark:
    def test_repassa_registros_sem_consumir_tudo_antecipadamente(self) -> None:
        from apps.controle_etl.models import MarcaDaguaExtracao

        consumidos: list[int] = []

        def _fonte() -> Any:
            for n in range(3):
                consumidos.append(n)
                yield RegistroIdentidade(fonte="se1426", tipo="servidor")

        gerador = _iterar_com_watermark("se1426", _fonte())

        primeiro = next(gerador)
        assert consumidos == [0]
        assert primeiro is not None

        assert not MarcaDaguaExtracao.objects.filter(fonte="se1426").exists()

        list(gerador)

        assert consumidos == [0, 1, 2]
        marca = MarcaDaguaExtracao.objects.get(fonte="se1426")
        assert marca.total_processado == 3

    def test_nao_atualiza_watermark_quando_fonte_vazia(self) -> None:
        from apps.controle_etl.models import MarcaDaguaExtracao

        list(_iterar_com_watermark("coresso", iter([])))

        assert not MarcaDaguaExtracao.objects.filter(fonte="coresso").exists()


# ---------------------------------------------------------------------------
# extrair_se1426
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExtrairSe1426:
    def test_sem_configuracao_chama_api(self, settings: Any) -> None:
        settings.SE1426_DB_SERVIDOR = ""
        settings.SE1426_API_URL = "https://api.test"
        settings.SE1426_API_TOKEN = "tok"
        settings.SE1426_API_TIMEOUT = 30

        payload_api = {
            "results": [
                {
                    "rf": "1234567",
                    "nome": "Ana Lima",
                    "cpf": "11122233344",
                    "email": "ana@sme.sp.gov.br",
                    "situacao": "ativo",
                }
            ],
            "next": None,
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = payload_api
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            registros = list(extrair_se1426())

        assert len(registros) == 1
        assert registros[0].rf == "1234567"
        assert registros[0].fonte == "se1426"
        assert registros[0].tipo == "servidor"

    def test_retorna_lista_vazia_quando_api_falha(self, settings: Any) -> None:
        settings.SE1426_DB_SERVIDOR = ""
        settings.SE1426_API_URL = "https://api.test"
        settings.SE1426_API_TOKEN = "tok"
        settings.SE1426_API_TIMEOUT = 30

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = Exception("timeout")
            mock_client_cls.return_value = mock_client

            registros = list(extrair_se1426())

        assert registros == []

    def test_data_referencia_sobrepoe_watermark(self, settings: Any) -> None:
        settings.SE1426_DB_SERVIDOR = ""
        settings.SE1426_API_URL = "https://api.test"
        settings.SE1426_API_TOKEN = "tok"
        settings.SE1426_API_TIMEOUT = 30

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [], "next": None}
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            registros = list(extrair_se1426(data_referencia="2026-01-01"))

        assert registros == []
        call_kwargs = mock_client.get.call_args
        assert "data_alteracao_apos" in call_kwargs.kwargs.get("params", {})

    def test_api_paginacao_avanca_para_proxima_pagina(
        self,
        settings: Any,
    ) -> None:
        settings.SE1426_DB_SERVIDOR = ""
        settings.SE1426_API_URL = "https://api.test"
        settings.SE1426_API_TOKEN = "tok"
        settings.SE1426_API_TIMEOUT = 30

        respostas = [
            {
                "results": [{"rf": "1", "nome": "Um"}],
                "next": "pagina2",
            },
            {
                "results": [{"rf": "2", "nome": "Dois"}],
                "next": None,
            },
        ]
        iter_resp = iter(respostas)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = lambda: next(iter_resp)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            registros = list(extrair_se1426())

        assert len(registros) == 2

    def test_com_sql_server_extrai_servidores(self, settings: Any) -> None:
        settings.SE1426_DB_SERVIDOR = "srv-se"
        settings.SE1426_DB_NOME = "se1426"
        settings.SE1426_DB_USUARIO = "usr"
        settings.SE1426_DB_SENHA = "pwd"
        settings.SE1426_DB_TIMEOUT = 30

        linha = (
            "1234567",
            "Ana Lima",
            "11122233344",
            "ATIVO",
            "ana@sme.sp.gov.br",
        )
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("rf",),
            ("nome",),
            ("cpf",),
            ("situacao",),
            ("email",),
        ]
        mock_cursor.fetchmany.side_effect = [[linha], []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            registros = list(extrair_se1426())

        assert len(registros) == 1
        assert registros[0].rf == "1234567"
        assert registros[0].situacao == "ativo"
        mock_conn.close.assert_called_once()

    def test_sql_nao_aplica_filtro_de_data(self, settings: Any) -> None:
        """Confirma a ausência de filtro incremental no SE1426.

        A view v_servidor_sme_serap não expõe data de atualização —
        a extração SQL sempre traz a base completa, sem filtro
        incremental (ver apps/extracao/tasks.py:_extrair_se1426_sql).
        """
        settings.SE1426_DB_SERVIDOR = "srv-se"
        settings.SE1426_DB_NOME = "se1426"
        settings.SE1426_DB_USUARIO = "usr"
        settings.SE1426_DB_SENHA = "pwd"
        settings.SE1426_DB_TIMEOUT = 30

        mock_cursor = MagicMock()
        mock_cursor.description = [("rf",)]
        mock_cursor.fetchmany.side_effect = [[]]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn) as mock_connect:
            list(extrair_se1426(data_referencia="2026-01-01"))

        mock_connect.assert_called_once()
        consulta_executada = mock_cursor.execute.call_args[0][0]
        assert "WHERE" not in consulta_executada

    def test_atualiza_watermark_apos_extracao(self, settings: Any) -> None:
        from apps.controle_etl.models import MarcaDaguaExtracao

        settings.SE1426_DB_SERVIDOR = ""
        settings.SE1426_API_URL = "https://api.test"
        settings.SE1426_API_TOKEN = "tok"
        settings.SE1426_API_TIMEOUT = 30

        payload = {
            "results": [{"rf": "9999", "nome": "Teste", "situacao": "ativo"}],
            "next": None,
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            list(extrair_se1426())

        assert MarcaDaguaExtracao.objects.filter(fonte="se1426").exists()


# ---------------------------------------------------------------------------
# extrair_coresso
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExtrairCoresso:
    def test_sem_configuracao_retorna_lista_vazia(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        settings.CORESSO_API_URL = ""
        registros = list(extrair_coresso())
        assert registros == []

    def test_com_api_retorna_registros(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        settings.CORESSO_API_URL = "https://coresso.test"
        settings.CORESSO_API_TOKEN = "tok"
        settings.CORESSO_DB_TIMEOUT = 30

        payload = {
            "results": [
                {
                    "cpf": "98765432100",
                    "nome": "Maria Santos",
                    "email": "maria@edu.sp.gov.br",
                    "situacao": "1",
                }
            ],
            "next": None,
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            registros = list(extrair_coresso())

        assert len(registros) == 1
        assert registros[0].situacao == "ativo"
        assert registros[0].tipo_acesso == "legado-coresso"

    def test_situacao_inativo_quando_valor_diferente_de_1(
        self,
        settings: Any,
    ) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        settings.CORESSO_API_URL = "https://coresso.test"
        settings.CORESSO_API_TOKEN = "tok"
        settings.CORESSO_DB_TIMEOUT = 30

        payload = {
            "results": [{"cpf": "11111111111", "situacao": "0"}],
            "next": None,
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            registros = list(extrair_coresso())

        assert registros[0].situacao == "inativo"

    def test_api_paginacao_para_quando_sem_next(
        self,
        settings: Any,
    ) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        settings.CORESSO_API_URL = "https://coresso.test"
        settings.CORESSO_API_TOKEN = "tok"
        settings.CORESSO_DB_TIMEOUT = 30

        respostas = [
            {
                "results": [{"cpf": "11111111111", "situacao": "1"}],
                "next": "pagina2",
            },
            {
                "results": [{"cpf": "22222222222", "situacao": "1"}],
                "next": None,
            },
        ]
        iter_resp = iter(respostas)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = lambda: next(iter_resp)

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            registros = list(extrair_coresso())

        assert len(registros) == 2

    def test_api_erro_interrompe_paginacao(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        settings.CORESSO_API_URL = "https://coresso.test"
        settings.CORESSO_API_TOKEN = "tok"
        settings.CORESSO_DB_TIMEOUT = 30

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = Exception("falha de rede")
            mock_client_cls.return_value = mock_client

            registros = list(extrair_coresso())

        assert registros == []

    def test_data_referencia_repassada_para_api(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        settings.CORESSO_API_URL = "https://coresso.test"
        settings.CORESSO_API_TOKEN = "tok"
        settings.CORESSO_DB_TIMEOUT = 30

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [], "next": None}
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            list(extrair_coresso(data_referencia="2026-01-01"))

        call_kwargs = mock_client.get.call_args
        assert "data_alteracao_apos" in call_kwargs.kwargs.get("params", {})

    def test_com_sql_server_extrai_terceiros(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv-core"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30

        linha = (
            "joao.silva",
            "12345678901",
            "João Silva",
            "joao@sme.sp.gov.br",
            "1",
            None,
        )
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("login",),
            ("cpf",),
            ("nome",),
            ("email",),
            ("situacao",),
            ("dt_atualizacao",),
        ]
        mock_cursor.fetchmany.side_effect = [[linha], []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            registros = list(extrair_coresso())

        assert len(registros) == 1
        assert registros[0].situacao == "ativo"
        assert registros[0].tipo_acesso == "legado-coresso"
        mock_conn.close.assert_called_once()

    def test_sql_aplica_filtro_de_data_quando_ha_watermark(
        self,
        settings: Any,
    ) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv-core"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30

        mock_cursor = MagicMock()
        mock_cursor.description = [("login",)]
        mock_cursor.fetchmany.side_effect = [[]]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            list(extrair_coresso(data_referencia="2026-01-01"))

        consulta_executada = mock_cursor.execute.call_args[0][0]
        assert "WHERE" in consulta_executada


# ---------------------------------------------------------------------------
# extrair_eol_alunos
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExtrairEolAlunos:
    def test_sem_configuracao_retorna_lista_vazia(self, settings: Any) -> None:
        settings.EOL_DB_STRING_CONEXAO = ""
        settings.SE1426_DB_SERVIDOR = ""
        registros = list(extrair_eol_alunos())
        assert registros == []

    def test_com_sql_server_extrai_alunos(self, settings: Any) -> None:
        settings.EOL_DB_STRING_CONEXAO = ""
        settings.SE1426_DB_SERVIDOR = "srv-eol"
        settings.SE1426_DB_USUARIO = "usr"
        settings.SE1426_DB_SENHA = "pwd"
        settings.SE1426_DB_TIMEOUT = 60

        linha_aluno = (
            "MAT001",
            "Pedro Souza",
            "33344455566",
            "ativo",
            "EE001",
            "5A",
            "UE001",
            "DRE01",
        )
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("matricula",),
            ("nome",),
            ("cpf",),
            ("situacao",),
            ("cod_escola",),
            ("turma",),
            ("cod_ue",),
            ("cod_dre",),
        ]
        mock_cursor.fetchmany.side_effect = [[linha_aluno], []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            registros = list(extrair_eol_alunos())

        assert len(registros) == 1
        assert registros[0].tipo == "aluno"
        assert registros[0].matricula == "MAT001"

    def test_data_referencia_nao_aplica_filtro_de_data(
        self,
        settings: Any,
    ) -> None:
        """Confirma a ausência de filtro incremental no EOL_DB.

        As tabelas aluno/v_matricula_cotic/matricula_turma_escola não
        expõem data de atualização utilizável — a extração SQL sempre
        traz a base completa, sem filtro incremental (ver
        apps/extracao/tasks.py:_extrair_eol_alunos_sql).
        """
        settings.EOL_DB_STRING_CONEXAO = ""
        settings.SE1426_DB_SERVIDOR = "srv-eol"
        settings.SE1426_DB_USUARIO = "usr"
        settings.SE1426_DB_SENHA = "pwd"
        settings.SE1426_DB_TIMEOUT = 60

        mock_cursor = MagicMock()
        mock_cursor.description = [("matricula",)]
        mock_cursor.fetchmany.side_effect = [[]]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            list(extrair_eol_alunos(data_referencia="2026-01-01"))

        consulta_executada = mock_cursor.execute.call_args[0][0]
        assert "WHERE" not in consulta_executada

    def test_etl_lote_maximo_interrompe_extracao(self, settings: Any) -> None:
        settings.EOL_DB_STRING_CONEXAO = ""
        settings.SE1426_DB_SERVIDOR = "srv-eol"
        settings.SE1426_DB_USUARIO = "usr"
        settings.SE1426_DB_SENHA = "pwd"
        settings.SE1426_DB_TIMEOUT = 60
        settings.ETL_CHUNK_SIZE = 2
        settings.ETL_LOTE_MAXIMO = 3

        linhas = [
            (
                f"MAT{n:03d}",
                f"Aluno {n}",
                "33344455566",
                "ativo",
                "EE001",
                "5A",
                "UE001",
                "DRE01",
                None,
            )
            for n in range(10)
        ]
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("matricula",),
            ("nome",),
            ("cpf",),
            ("situacao",),
            ("cod_escola",),
            ("turma",),
            ("cod_ue",),
            ("cod_dre",),
            ("dt_atualizacao",),
        ]
        mock_cursor.fetchmany.side_effect = [
            linhas[0:2],
            linhas[2:4],
            linhas[4:6],
            [],
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            registros = list(extrair_eol_alunos())

        assert len(registros) == 3
        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# buscar_grupos_coresso_por_login
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuscarGruposCoresso:
    def test_sem_login_retorna_lista_vazia(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        assert buscar_grupos_coresso_por_login("") == []

    def test_sem_servidor_retorna_lista_vazia(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        assert buscar_grupos_coresso_por_login("1234567") == []

    def test_retorna_grupos(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, "Professores"),
            (2, "Diretores"),
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            grupos = buscar_grupos_coresso_por_login("1234567")

        assert len(grupos) == 2
        assert grupos[0]["gru_id"] == 1
        assert grupos[1]["nome"] == "Diretores"

    def test_erro_de_conexao_retorna_lista_vazia(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30

        with patch("pyodbc.connect", side_effect=Exception("conn error")):
            grupos = buscar_grupos_coresso_por_login("1234567")

        assert grupos == []


# ---------------------------------------------------------------------------
# extrair_sistemas_coresso
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExtrairSistemasCoresso:
    def test_sem_servidor_retorna_zero(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        total = extrair_sistemas_coresso()
        assert total == 0

    def test_persiste_sistemas(self, settings: Any) -> None:
        from apps.staging.models import SistemaStaging

        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30

        linha = (
            42,
            "Sistema Escolar",
            "Desc",
            "oauth2",
            "http://integracao",
            "http://logout",
            1,
        )
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("sis_id",),
            ("sis_nome",),
            ("sis_descricao",),
            ("sis_tipoAutenticacao",),
            ("sis_urlIntegracao",),
            ("sis_caminhoLogout",),
            ("sis_situacao",),
        ]
        mock_cursor.fetchall.return_value = [linha]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            total = extrair_sistemas_coresso()

        assert total == 1
        sistema = SistemaStaging.objects.get(coresso_sis_id=42)
        assert sistema.nome == "Sistema Escolar"
        assert sistema.url_callback == "http://integracao"
        assert sistema.url_logout == "http://logout"

    def test_aplica_filtro_de_exclusao_quando_configurado(
        self,
        settings: Any,
    ) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = [174]

        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("sis_id",),
            ("sis_nome",),
            ("sis_descricao",),
            ("sis_tipoAutenticacao",),
            ("sis_urlIntegracao",),
            ("sis_caminhoLogout",),
            ("sis_situacao",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            extrair_sistemas_coresso()

        consulta_executada = mock_cursor.execute.call_args[0][0]
        assert "WHERE sis_id NOT IN (174)" in consulta_executada

    def test_sem_filtro_quando_lista_de_exclusao_vazia(
        self,
        settings: Any,
    ) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = []

        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("sis_id",),
            ("sis_nome",),
            ("sis_descricao",),
            ("sis_tipoAutenticacao",),
            ("sis_urlIntegracao",),
            ("sis_caminhoLogout",),
            ("sis_situacao",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            extrair_sistemas_coresso()

        consulta_executada = mock_cursor.execute.call_args[0][0]
        assert "WHERE" not in consulta_executada


# ---------------------------------------------------------------------------
# extrair_perfis_coresso
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExtrairPerfisCoresso:
    def test_sem_servidor_retorna_zero(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        total = extrair_perfis_coresso()
        assert total == 0

    def test_persiste_perfis(self, settings: Any) -> None:
        from apps.staging.models import PerfilCoressoStaging

        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30

        linha = (101, "Professores", 42, 1, 1)
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("gru_id",),
            ("gru_nome",),
            ("sis_id",),
            ("vis_id",),
            ("gru_situacao",),
        ]
        mock_cursor.fetchall.return_value = [linha]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            total = extrair_perfis_coresso()

        assert total == 1
        perfil = PerfilCoressoStaging.objects.get(coresso_gru_id="101")
        assert perfil.nome == "Professores"

    def test_aplica_filtro_de_exclusao_quando_configurado(
        self,
        settings: Any,
    ) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = [174]

        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("gru_id",),
            ("gru_nome",),
            ("sis_id",),
            ("vis_id",),
            ("gru_situacao",),
        ]
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            extrair_perfis_coresso()

        consulta_executada = mock_cursor.execute.call_args[0][0]
        assert "WHERE sis_id NOT IN (174)" in consulta_executada


# ---------------------------------------------------------------------------
# _slugificar_role
# ---------------------------------------------------------------------------


class TestSlugificarRole:
    def test_nome_simples(self) -> None:
        assert _slugificar_role("Professores") == "Professores"

    def test_remove_acentos(self) -> None:
        slug = _slugificar_role("Direção")
        assert "ã" not in slug
        assert "o" in slug.lower()

    def test_substitui_espacos_por_underscore(self) -> None:
        assert _slugificar_role("Coord Pedagogico") == "Coord_Pedagogico"

    def test_string_vazia_retorna_fallback(self) -> None:
        assert _slugificar_role("") == "perfil_sem_nome"

    def test_caracteres_especiais(self) -> None:
        slug = _slugificar_role("Aux. Técnico (Educ.)")
        assert "(" not in slug
        assert "." not in slug


# ------------------------------------------------------------------
# extrair_vinculos_usuario_grupo_coresso
# ------------------------------------------------------------------


class TestExtrairVinculosUsuarioGrupoCoresso:
    def test_sem_servidor_retorna_vazio(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        resultado = list(extrair_vinculos_usuario_grupo_coresso())
        assert resultado == []

    def test_retorna_vinculos_em_streaming(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = []
        settings.ETL_CHUNK_SIZE = 500
        settings.ETL_LOTE_MAXIMO = 0

        linhas = [
            ("6913261", "11972201867", "gru-a", "ASCOM", 1008),
            ("6605656", "15143960843", "gru-b", "COPED", 1008),
        ]
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("login",),
            ("cpf",),
            ("gru_id",),
            ("gru_nome",),
            ("sis_id",),
        ]
        mock_cursor.fetchmany.side_effect = [linhas, []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            resultado = list(extrair_vinculos_usuario_grupo_coresso())

        assert len(resultado) == 2
        assert resultado[0]["login"] == "6913261"
        assert resultado[0]["sis_id"] == 1008
        assert resultado[1]["gru_nome"] == "COPED"

    def test_aplica_filtro_de_exclusao(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = [174]
        settings.ETL_CHUNK_SIZE = 500
        settings.ETL_LOTE_MAXIMO = 0

        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("login",),
            ("cpf",),
            ("gru_id",),
            ("gru_nome",),
            ("sis_id",),
        ]
        mock_cursor.fetchmany.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            list(extrair_vinculos_usuario_grupo_coresso())

        consulta = mock_cursor.execute.call_args[0][0]
        assert "sis_id NOT IN" in consulta
        assert "174" in consulta

    def test_usa_subquery_para_tdo_id_cpf(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = []
        settings.ETL_CHUNK_SIZE = 500
        settings.ETL_LOTE_MAXIMO = 0

        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("login",),
            ("cpf",),
            ("gru_id",),
            ("gru_nome",),
            ("sis_id",),
        ]
        mock_cursor.fetchmany.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            list(extrair_vinculos_usuario_grupo_coresso())

        consulta = mock_cursor.execute.call_args[0][0]
        assert "SYS_TipoDocumentacao" in consulta
        assert "CPF" in consulta

    def test_respeita_lote_maximo(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = []
        settings.ETL_CHUNK_SIZE = 500
        settings.ETL_LOTE_MAXIMO = 1

        linhas = [
            ("u1", "111", "g1", "A", 1),
            ("u2", "222", "g2", "B", 2),
            ("u3", "333", "g3", "C", 3),
        ]
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("login",),
            ("cpf",),
            ("gru_id",),
            ("gru_nome",),
            ("sis_id",),
        ]
        mock_cursor.fetchmany.side_effect = [linhas, []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            resultado = list(extrair_vinculos_usuario_grupo_coresso())

        assert len(resultado) == 1
        assert resultado[0]["login"] == "u1"


# ------------------------------------------------------------------
# _montar_resultado_usuario
# ------------------------------------------------------------------


class TestMontarResultadoUsuario:
    def test_monta_resultado_basico(self) -> None:
        rows = [
            (
                "7777777",
                "11122233344",
                "Joao",
                "j@sme",
                1,
                "g1",
                "Admin",
                42,
                "SGP",
            ),
        ]
        r = _montar_resultado_usuario(rows)
        assert r["login"] == "7777777"
        assert r["cpf"] == "11122233344"
        assert r["nome"] == "Joao"
        assert r["situacao"] == "ativo"
        assert 42 in r["sistemas"]
        assert r["sistemas"][42]["grupos"][0]["nome"] == "Admin"

    def test_usuario_sem_sistema(self) -> None:
        rows = [
            ("rf1", "", "Nome", "e@x", 1, None, None, None, None),
        ]
        r = _montar_resultado_usuario(rows)
        assert r["sistemas"] == {}

    def test_agrupa_por_sistema(self) -> None:
        rows = [
            ("rf", "cpf", "N", "e", 1, "g1", "Admin", 10, "SGP"),
            ("rf", "cpf", "N", "e", 1, "g2", "Editor", 10, "SGP"),
            ("rf", "cpf", "N", "e", 1, "g3", "Viewer", 20, "PTRF"),
        ]
        r = _montar_resultado_usuario(rows)
        assert len(r["sistemas"]) == 2
        assert len(r["sistemas"][10]["grupos"]) == 2
        assert len(r["sistemas"][20]["grupos"]) == 1


# ------------------------------------------------------------------
# buscar_dados_usuario_coresso
# ------------------------------------------------------------------


class TestBuscarDadosUsuarioCoresso:
    def test_sem_servidor_retorna_none(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        assert buscar_dados_usuario_coresso("123") is None

    def test_retorna_dados_por_rf(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30

        rows = [
            (
                "7777777",
                "11122233344",
                "Joao Silva",
                "j@sme",
                1,
                "g1",
                "Admin",
                42,
                "SGP",
            ),
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            r = buscar_dados_usuario_coresso("7777777")

        assert r is not None
        assert r["login"] == "7777777"
        assert r["cpf"] == "11122233344"
        assert 42 in r["sistemas"]

    def test_nao_encontrado_retorna_none(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("pyodbc.connect", return_value=mock_conn):
            r = buscar_dados_usuario_coresso("inexistente")

        assert r is None

    def test_complementa_email_do_se1426(self, settings: Any) -> None:
        settings.CORESSO_DB_SERVIDOR = "srv"
        settings.CORESSO_DB_NOME = "CoreSSO"
        settings.CORESSO_DB_USUARIO = "usr"
        settings.CORESSO_DB_SENHA = "pwd"
        settings.CORESSO_DB_TIMEOUT = 30
        settings.SE1426_DB_SERVIDOR = "srv2"
        settings.SE1426_DB_NOME = "se1426"
        settings.SE1426_DB_USUARIO = "usr2"
        settings.SE1426_DB_SENHA = "pwd2"
        settings.SE1426_DB_TIMEOUT = 30

        rows_coresso = [
            ("7777777", "", "Joao", "", 1, "g1", "Admin", 42, "SGP"),
        ]
        mock_cursor_coresso = MagicMock()
        mock_cursor_coresso.fetchall.return_value = rows_coresso
        mock_conn_coresso = MagicMock()
        mock_conn_coresso.cursor.return_value = mock_cursor_coresso

        mock_cursor_se = MagicMock()
        mock_cursor_se.fetchone.return_value = (
            "7777777",
            "Joao Silva",
            "11122233344",
            "joao@sme.sp",
        )
        mock_conn_se = MagicMock()
        mock_conn_se.cursor.return_value = mock_cursor_se

        def connect_side(cs: str, **kw: Any) -> MagicMock:
            if "CoreSSO" in cs:
                return mock_conn_coresso
            return mock_conn_se

        with patch("pyodbc.connect", side_effect=connect_side):
            r = buscar_dados_usuario_coresso("7777777")

        assert r is not None
        assert r["email"] == "joao@sme.sp"
        assert r["cpf"] == "11122233344"
