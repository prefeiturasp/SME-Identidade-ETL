import uuid
from collections import namedtuple
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

pytestmark = pytest.mark.django_db




class TestBuildSe1426ConnStr:
    def test_conn_str_contains_server(self, settings):
        settings.SE1426_DB_SERVER = "10.49.16.136"
        settings.SE1426_DB_NAME = "se1426"
        settings.SE1426_DB_USER = "user"
        settings.SE1426_DB_PASSWORD = "pass"
        from extract.tasks import _build_se1426_conn_str
        conn_str = _build_se1426_conn_str()
        assert "10.49.16.136" in conn_str
        assert "se1426" in conn_str
        assert "FreeTDS" in conn_str




class TestExtractSe1426Task:
    def _make_execution(self):
        from core.models import ETLExecution
        return ETLExecution.objects.create(source="se1426")

    def test_skip_when_no_server_no_token(self, settings):
        settings.SE1426_DB_SERVER = ""
        settings.SE1426_API_TOKEN = ""
        from extract.tasks import extract_se1426
        execution = self._make_execution()
        result = extract_se1426(str(execution.id))
        assert result == 0

        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "skipped"

    @patch("extract.tasks._extract_se1426_sql")
    def test_direct_sql_path(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "10.49.16.136"
        mock_sql.return_value = 50
        from extract.tasks import extract_se1426
        execution = self._make_execution()
        result = extract_se1426(str(execution.id))
        assert result == 50
        mock_sql.assert_called_once()

        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "success"
        assert step.records_out == 50

    @patch("extract.tasks._extract_se1426_api")
    def test_api_fallback_path(self, mock_api, settings):
        settings.SE1426_DB_SERVER = ""
        settings.SE1426_API_TOKEN = "tok123"
        settings.SE1426_API_URL = "http://se1426-api"
        settings.SE1426_API_TIMEOUT = 5
        mock_api.return_value = 30
        from extract.tasks import extract_se1426
        execution = self._make_execution()
        result = extract_se1426(str(execution.id))
        assert result == 30
        mock_api.assert_called_once()




class TestExtractEolDbTask:
    def _make_execution(self):
        from core.models import ETLExecution
        return ETLExecution.objects.create(source="eol_db")

    def test_skip_when_no_server(self, settings):
        settings.SE1426_DB_SERVER = ""
        from extract.tasks import extract_eol_db
        execution = self._make_execution()
        result = extract_eol_db(str(execution.id))
        assert result == 0

    @patch("extract.tasks._extract_eol_db_sql")
    def test_direct_sql_path(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "10.49.16.136"
        mock_sql.return_value = 20
        from extract.tasks import extract_eol_db
        execution = self._make_execution()
        result = extract_eol_db(str(execution.id))
        assert result == 20

        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "success"




class TestExtractEolAlunosTask:
    def _make_execution(self):
        from core.models import ETLExecution
        return ETLExecution.objects.create(source="eol_alunos")

    def test_skip_when_no_server(self, settings):
        settings.SE1426_DB_SERVER = ""
        from extract.tasks import extract_eol_alunos
        execution = self._make_execution()
        result = extract_eol_alunos(str(execution.id))
        assert result == 0

    @patch("extract.tasks._extract_eol_alunos_sql")
    def test_direct_sql_path(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "10.49.16.136"
        mock_sql.return_value = 100
        from extract.tasks import extract_eol_alunos
        execution = self._make_execution()
        result = extract_eol_alunos(str(execution.id))
        assert result == 100

        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "success"




class TestExtractCoressoTask:
    def _make_execution(self):
        from core.models import ETLExecution
        return ETLExecution.objects.create(source="coresso")

    def test_skip_when_no_server(self, settings):
        settings.CORESSO_DB_SERVER = ""
        settings.CORESSO_API_TOKEN = ""
        from extract.tasks import extract_coresso
        execution = self._make_execution()
        result = extract_coresso(str(execution.id))
        assert result == 0

        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "skipped"

    @patch("extract.tasks._extract_coresso_sql")
    def test_direct_sql_path(self, mock_sql, settings):
        settings.CORESSO_DB_SERVER = "coresso-server"
        settings.CORESSO_DB_NAME = "coresso"
        settings.CORESSO_DB_USER = "user"
        settings.CORESSO_DB_PASSWORD = "pass"
        settings.CORESSO_DB_TIMEOUT = 5
        mock_sql.return_value = 75
        from extract.tasks import extract_coresso
        execution = self._make_execution()
        result = extract_coresso(str(execution.id))
        assert result == 75

        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "success"




def _make_pyodbc_mock(rows, description):
    """Build a pyodbc module mock with a cursor returning `rows`."""
    import sys
    mock_pyodbc = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchmany.side_effect = [rows, []]
    mock_cursor.description = description
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_pyodbc.connect.return_value = mock_conn
    return mock_pyodbc


class TestExtractSe1426Sql:
    def test_extracts_records_from_cursor(self, settings):
        import sys
        settings.SE1426_DB_SERVER = "10.49.16.136"
        settings.SE1426_DB_NAME = "se1426"
        settings.SE1426_DB_USER = "user"
        settings.SE1426_DB_PASSWORD = "pass"
        settings.SE1426_DB_TIMEOUT = 5

        Row = namedtuple("Row", ["rf", "nome", "cpf", "situacao", "email"])
        mock_pyodbc = _make_pyodbc_mock(
            [Row("12345", "JOAO SILVA", "52998224725", "Ativo", "j@sme.sp")],
            [("rf",), ("nome",), ("cpf",), ("situacao",), ("email",)],
        )
        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            exec_id = uuid.uuid4()
            total = et._extract_se1426_sql(str(exec_id))
        assert total == 1

        from staging.models import StagingUsuarioServidor
        assert StagingUsuarioServidor.objects.filter(
            execution_id=exec_id, rf="12345"
        ).exists()

    def test_handles_empty_result(self, settings):
        import sys
        settings.SE1426_DB_SERVER = "10.49.16.136"
        settings.SE1426_DB_NAME = "se1426"
        settings.SE1426_DB_USER = "user"
        settings.SE1426_DB_PASSWORD = "pass"
        settings.SE1426_DB_TIMEOUT = 5

        mock_pyodbc = _make_pyodbc_mock([], [("rf",), ("nome",), ("cpf",), ("situacao",), ("email",)])
        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            total = et._extract_se1426_sql(str(uuid.uuid4()))
        assert total == 0


class TestExtractEolDbSql:
    def _setup_settings(self, settings):
        settings.SE1426_DB_SERVER = "10.49.16.136"
        settings.SE1426_DB_NAME = "se1426"
        settings.SE1426_DB_USER = "user"
        settings.SE1426_DB_PASSWORD = "pass"
        settings.SE1426_DB_TIMEOUT = 5

    def test_extracts_records_with_lotacao(self, settings):
        import sys
        self._setup_settings(settings)

        row = ("12345", "52998224725", "JOAO SILVA", "Ativo", "PROF", "101", "100001", "10")
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.side_effect = [[row], []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            exec_id = uuid.uuid4()
            total = et._extract_eol_db_sql(str(exec_id))

        assert total == 1
        from staging.models import StagingUsuarioServidor
        srv = StagingUsuarioServidor.objects.get(execution_id=exec_id, rf="12345")
        assert srv.lotacao == "100001"
        assert srv.dre == "10"

    def test_handles_empty_result(self, settings):
        import sys
        self._setup_settings(settings)
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            total = et._extract_eol_db_sql(str(uuid.uuid4()))
        assert total == 0

    def test_aggregates_multiple_lotacoes_per_rf(self, settings):
        import sys
        self._setup_settings(settings)

        rows = [
            ("12345", "52998224725", "JOAO SILVA", "Ativo", "PROF", "101", "100001", "10"),
            ("12345", "52998224725", "JOAO SILVA", "Ativo", "PROF", "101", "100002", "10"),
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.side_effect = [rows, []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            exec_id = uuid.uuid4()
            total = et._extract_eol_db_sql(str(exec_id))

        assert total == 1
        from staging.models import StagingUsuarioServidor
        srv = StagingUsuarioServidor.objects.get(execution_id=exec_id)
        assert "100001" in srv.raw_data["unidades"]
        assert "100002" in srv.raw_data["unidades"]




class TestExtractEolAlunosSql:
    def _setup_settings(self, settings):
        settings.SE1426_DB_SERVER = "10.49.16.136"
        settings.SE1426_DB_NAME = "se1426"
        settings.SE1426_DB_USER = "user"
        settings.SE1426_DB_PASSWORD = "pass"
        settings.SE1426_DB_TIMEOUT = 5

    def test_extracts_aluno_records(self, settings):
        import sys
        self._setup_settings(settings)

        Row = namedtuple("AlRow", ["matricula", "nome", "data_nascimento", "cod_escola", "turma", "cod_dre"])
        mock_row = Row("9999", "MARIA SOUZA", "2010-05-15", "100001", "500", "10")
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.side_effect = [[mock_row], []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            exec_id = uuid.uuid4()
            total = et._extract_eol_alunos_sql(str(exec_id))

        assert total == 1
        from staging.models import StagingUsuarioAluno
        aluno = StagingUsuarioAluno.objects.get(execution_id=exec_id)
        assert aluno.matricula == "9999"

    def test_handles_null_dob(self, settings):
        import sys
        self._setup_settings(settings)

        Row = namedtuple("AlRow", ["matricula", "nome", "data_nascimento", "cod_escola", "turma", "cod_dre"])
        mock_row = Row("8888", "PEDRO LIMA", None, "100001", "500", "10")
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.side_effect = [[mock_row], []]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            exec_id = uuid.uuid4()
            total = et._extract_eol_alunos_sql(str(exec_id))

        assert total == 1
        from staging.models import StagingUsuarioAluno
        aluno = StagingUsuarioAluno.objects.get(execution_id=exec_id)
        assert aluno.data_nascimento is None




class TestExtractCoressloSql:
    def _setup_settings(self, settings):
        settings.CORESSO_DB_SERVER = "coresso-server"
        settings.CORESSO_DB_NAME = "coreSSO"
        settings.CORESSO_DB_USER = "user"
        settings.CORESSO_DB_PASSWORD = "pass"
        settings.CORESSO_DB_TIMEOUT = 5

    def test_extracts_servidor(self, settings):
        import sys
        self._setup_settings(settings)

        Row = namedtuple("CR", ["rf", "email", "nome", "cpf", "situacao", "data_alteracao"])
        mock_row = Row("12345", "j@sme.sp", "JOAO SILVA", "52998224725", 1, None)
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.side_effect = [[mock_row], []]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            exec_id = uuid.uuid4()
            total = et._extract_coresso_sql(str(exec_id))

        assert total == 1
        from staging.models import StagingUsuarioServidor
        assert StagingUsuarioServidor.objects.filter(execution_id=exec_id, rf="12345").exists()

    def test_extracts_terceiro_when_no_rf(self, settings):
        import sys
        self._setup_settings(settings)

        Row = namedtuple("CR", ["rf", "email", "nome", "cpf", "situacao", "data_alteracao"])
        mock_row = Row(None, "j@gmail.com", "JOAO EXTERNO", "52998224725", 1, None)
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.side_effect = [[mock_row], []]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            exec_id = uuid.uuid4()
            total = et._extract_coresso_sql(str(exec_id))

        assert total == 1
        from staging.models import StagingUsuarioTerceiro
        assert StagingUsuarioTerceiro.objects.filter(execution_id=exec_id).exists()




class TestExtractCoressloSistemas:
    def _setup_settings(self, settings):
        settings.CORESSO_DB_SERVER = "coresso-server"
        settings.CORESSO_DB_NAME = "coreSSO"
        settings.CORESSO_DB_USER = "user"
        settings.CORESSO_DB_PASSWORD = "pass"
        settings.CORESSO_DB_TIMEOUT = 5

    def test_skip_when_no_server(self, settings):
        import sys
        settings.CORESSO_DB_SERVER = ""
        mock_pyodbc = MagicMock()
        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract.tasks import extract_coresso_sistemas
            result = extract_coresso_sistemas(execution_id=None)
        assert result == 0

    def test_creates_staging_sistemas(self, settings):
        import sys
        self._setup_settings(settings)

        Row = namedtuple("SR", ["sis_id", "sis_nome", "sis_descricao", "url_callback",
                                 "url_logout", "sis_tipoAutenticacao", "sis_situacao"])
        mock_row = Row(1, "Sistema SGP", "Sistema de Gestao Pedagogica",
                       "http://sgp.sme.sp.gov.br", None, 1, 1)
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract.tasks import extract_coresso_sistemas
            total = extract_coresso_sistemas(execution_id=None)

        assert total == 1
        from staging.models import StagingSistema
        assert StagingSistema.objects.filter(coresso_sis_id=1).exists()




class TestExtractCoressoPerfis:
    def _setup_settings(self, settings):
        settings.CORESSO_DB_SERVER = "coresso-server"
        settings.CORESSO_DB_NAME = "coreSSO"
        settings.CORESSO_DB_USER = "user"
        settings.CORESSO_DB_PASSWORD = "pass"
        settings.CORESSO_DB_TIMEOUT = 5

    def test_skip_when_no_server(self, settings):
        import sys
        settings.CORESSO_DB_SERVER = ""
        mock_pyodbc = MagicMock()
        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract.tasks import extract_coresso_perfis
            result = extract_coresso_perfis(execution_id=None)
        assert result == 0

    def test_creates_staging_perfis(self, settings):
        import sys
        from staging.models import StagingSistema
        self._setup_settings(settings)
        StagingSistema.objects.create(nome="SGP", sigla="sgp", coresso_sis_id=1)

        Row = namedtuple("GR", ["gru_id", "gru_nome", "sis_id", "vis_id", "gru_situacao"])
        mock_row = Row("GUID-001", "Administrador SGP", 1, None, 1)
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract.tasks import extract_coresso_perfis
            total = extract_coresso_perfis(execution_id=None)

        assert total == 1
        from staging.models import StagingPerfilCoreSSO
        assert StagingPerfilCoreSSO.objects.filter(coresso_gru_id="GUID-001").exists()




class TestExtractCoressoApiPath:
    def test_api_fallback_path(self, settings):
        settings.CORESSO_DB_SERVER = ""
        settings.CORESSO_API_URL = "http://coresso-api"
        settings.CORESSO_API_TOKEN = "tok"

        from extract.tasks import extract_coresso
        from core.models import ETLExecution
        from unittest.mock import patch as mock_patch

        execution = ETLExecution.objects.create(source="coresso")
        with mock_patch("extract.tasks._extract_coresso_api", return_value=5) as mock_api:
            result = extract_coresso(str(execution.id))
        assert result == 5
        mock_api.assert_called_once()

