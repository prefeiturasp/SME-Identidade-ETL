"""Testes de cobertura adicional para extract/tasks.py."""
import sys
import uuid
from collections import namedtuple
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


def _setup_se1426_settings(settings):
    settings.SE1426_DB_SERVER = "mock-se1426"
    settings.SE1426_DB_NAME = "se1426"
    settings.SE1426_DB_USER = "user"
    settings.SE1426_DB_PASSWORD = "pass"
    settings.SE1426_DB_TIMEOUT = 5


def _setup_coresso_settings(settings):
    settings.CORESSO_DB_SERVER = "mock-coresso"
    settings.CORESSO_DB_NAME = "coresso"
    settings.CORESSO_DB_USER = "user"
    settings.CORESSO_DB_PASSWORD = "pass"
    settings.CORESSO_DB_TIMEOUT = 5


def _make_se1426_mock(rows):
    mock_cursor = MagicMock()
    mock_cursor.fetchmany.side_effect = [rows, []]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc = MagicMock()
    mock_pyodbc.connect.return_value = mock_conn
    return mock_pyodbc


def _make_coresso_mock(rows):
    """Cria mock pyodbc para _extract_coresso_sql — cursor retorna rows uma vez."""
    mock_cursor = MagicMock()
    mock_cursor.fetchmany.side_effect = [rows, []]
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc = MagicMock()
    mock_pyodbc.connect.return_value = mock_conn
    return mock_pyodbc


class TestExtractSe1426SqlMaxRecords:
    def test_stops_when_max_records_reached(self, settings):
        _setup_se1426_settings(settings)
        settings.ETL_EXTRACT_BATCH_SIZE = 1  # força flush a cada 1 registro

        Row = namedtuple("Row", ["rf", "nome", "cpf", "situacao", "email"])
        mock_row = Row("12345", "JOAO SILVA", "52998224725", "Ativo", "j@sme.sp")
        mock_pyodbc = _make_se1426_mock([mock_row])

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et

            exec_id = uuid.uuid4()
            total = et._extract_se1426_sql(str(exec_id), max_records=1)

        assert total == 1


class TestExtractEolDbSqlMaxRecords:
    def test_stops_when_max_records_reached(self, settings):
        _setup_se1426_settings(settings)
        settings.ETL_EXTRACT_BATCH_SIZE = 1

        row = ("12345", "52998224725", "JOAO SILVA", "Ativo", "PROF", "101", "100001", "10")
        mock_pyodbc = _make_se1426_mock([row])

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et

            exec_id = uuid.uuid4()
            total = et._extract_eol_db_sql(str(exec_id), max_records=1)

        assert total == 1


class TestExtractEolAlunosSqlMaxRecords:
    def test_stops_when_max_records_reached(self, settings):
        _setup_se1426_settings(settings)
        settings.ETL_EXTRACT_BATCH_SIZE = 1

        Row = namedtuple("AlRow", ["matricula", "nome", "data_nascimento", "cod_escola", "turma", "cod_dre"])
        mock_row = Row("9999", "MARIA SOUZA", "2010-05-15", "100001", "500", "10")
        mock_pyodbc = _make_se1426_mock([mock_row])

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et

            exec_id = uuid.uuid4()
            total = et._extract_eol_alunos_sql(str(exec_id), max_records=1)

        assert total == 1


class TestExtractCoressoSqlExcludeIds:
    def test_exclude_ids_filter_is_applied(self, settings):
        """Com CORESSO_EXCLUDE_SISTEMA_IDS configurado, o filtro SQL é gerado."""
        _setup_coresso_settings(settings)
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = [174]

        Row = namedtuple("CR", ["rf", "email", "nome", "cpf", "situacao", "data_alteracao"])
        mock_row = Row("12345", "j@sme.sp", "JOAO SILVA", "52998224725", 1, None)
        mock_pyodbc = _make_coresso_mock([mock_row])

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et

            exec_id = uuid.uuid4()
            total = et._extract_coresso_sql(str(exec_id))

        assert total == 1

    def test_stops_when_max_records_reached(self, settings):
        _setup_coresso_settings(settings)
        settings.ETL_EXTRACT_BATCH_SIZE = 1
        settings.CORESSO_EXCLUDE_SISTEMA_IDS = []

        Row = namedtuple("CR", ["rf", "email", "nome", "cpf", "situacao", "data_alteracao"])
        mock_row = Row("12345", "j@sme.sp", "JOAO SILVA", "52998224725", 1, None)
        mock_pyodbc = _make_coresso_mock([mock_row])

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et

            exec_id = uuid.uuid4()
            total = et._extract_coresso_sql(str(exec_id), max_records=1)

        assert total == 1


class TestExtractSe1426ApiPage2HttpError:
    def test_http_error_on_page_2_breaks_loop(self, settings):
        """HTTPError em página 2+ deve interromper silenciosamente (não re-raise)."""
        settings.SE1426_API_URL = "http://se1426-api"
        settings.SE1426_API_TOKEN = "tok123"
        settings.SE1426_API_TIMEOUT = 5

        resp_page1 = MagicMock()
        resp_page1.raise_for_status = MagicMock()
        resp_page1.json.return_value = {
            "results": [
                {
                    "rf": "12345",
                    "cpf": "52998224725",
                    "nome": "JOAO SILVA",
                    "situacao": "Ativo",
                    "email": "j@sme.sp",
                }
            ],
            "next": "http://se1426-api?page=2",  # força tentativa de página 2
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        # Página 1: sucesso; página 2: HTTPError
        mock_client.get.side_effect = [resp_page1, Exception("network error")]

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client
        mock_httpx.HTTPError = Exception  # isinstance check vai casar com Exception

        with patch("extract.tasks.httpx", mock_httpx):
            from extract.tasks import _extract_se1426_api

            total = _extract_se1426_api(str(uuid.uuid4()))

        assert total == 1  # apenas página 1 foi extraída

    def test_http_error_on_page_1_reraises(self, settings):
        """HTTPError na página 1 deve ser re-raised."""
        settings.SE1426_API_URL = "http://se1426-api"
        settings.SE1426_API_TOKEN = "tok123"
        settings.SE1426_API_TIMEOUT = 5

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("connection refused")

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client
        mock_httpx.HTTPError = Exception

        with patch("extract.tasks.httpx", mock_httpx):
            from extract.tasks import _extract_se1426_api

            with pytest.raises(Exception, match="connection refused"):
                _extract_se1426_api(str(uuid.uuid4()))


class TestParseDateStr:
    def test_returns_none_for_none_input(self):
        from extract.tasks import _parse_date_str

        assert _parse_date_str(None) is None

    def test_returns_none_for_empty_string(self):
        from extract.tasks import _parse_date_str

        assert _parse_date_str("") is None

    def test_returns_none_for_invalid_format(self):
        from extract.tasks import _parse_date_str

        assert _parse_date_str("not-a-date") is None

    def test_returns_date_for_valid_string(self):
        from datetime import date

        from extract.tasks import _parse_date_str

        result = _parse_date_str("2010-05-15")
        assert result == date(2010, 5, 15)


class TestExtractRetryNotExhausted:
    """Cobre o branch `raise self.retry(...)` (retries < max_retries).

    Com CELERY_TASK_EAGER_PROPAGATES=True, a exceção Retry é propagada
    diretamente em vez de ser re-executada. Por isso usamos pytest.raises.
    """

    @patch("extract.tasks._extract_se1426_sql", side_effect=RuntimeError("db error"))
    def test_se1426_retries_when_not_exhausted(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"

        from celery.exceptions import Retry

        from core.models import ETLExecution
        from extract.tasks import extract_se1426

        execution = ETLExecution.objects.create(source="se1426")
        with pytest.raises(Retry):
            extract_se1426.apply(args=[str(execution.id)], retries=0)

    @patch("extract.tasks._extract_eol_db_sql", side_effect=RuntimeError("db error"))
    def test_eol_db_retries_when_not_exhausted(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"

        from celery.exceptions import Retry

        from core.models import ETLExecution
        from extract.tasks import extract_eol_db

        execution = ETLExecution.objects.create(source="eol_db")
        with pytest.raises(Retry):
            extract_eol_db.apply(args=[str(execution.id)], retries=0)

    @patch("extract.tasks._extract_eol_alunos_sql", side_effect=RuntimeError("db error"))
    def test_eol_alunos_retries_when_not_exhausted(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"

        from celery.exceptions import Retry

        from core.models import ETLExecution
        from extract.tasks import extract_eol_alunos

        execution = ETLExecution.objects.create(source="eol_alunos")
        with pytest.raises(Retry):
            extract_eol_alunos.apply(args=[str(execution.id)], retries=0)

    @patch("extract.tasks._extract_coresso_sql", side_effect=RuntimeError("db error"))
    def test_coresso_retries_when_not_exhausted(self, mock_sql, settings):
        settings.CORESSO_DB_SERVER = "mock-coresso-server"

        from celery.exceptions import Retry

        from core.models import ETLExecution
        from extract.tasks import extract_coresso

        execution = ETLExecution.objects.create(source="coresso")
        with pytest.raises(Retry):
            extract_coresso.apply(args=[str(execution.id)], retries=0)
