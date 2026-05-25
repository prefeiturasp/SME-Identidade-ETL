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
    """Monta um mock do modulo pyodbc com um cursor que retorna `rows`."""
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




class TestExtractSe1426Api:
    def test_extracts_from_api(self, settings):
        settings.SE1426_API_URL = "http://se1426-api"
        settings.SE1426_API_TOKEN = "tok123"
        settings.SE1426_API_TIMEOUT = 5

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "results": [
                {"rf": "12345", "cpf": "52998224725", "nome": "JOAO SILVA",
                 "situacao": "Ativo", "email": "j@sme.sp"},
            ],
            "next": None,
        }
        resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = resp

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client
        mock_httpx.HTTPError = Exception  # permite checagens isinstance de excecao

        exec_id = uuid.uuid4()
        with patch("extract.tasks.httpx", mock_httpx):
            from extract.tasks import _extract_se1426_api
            total = _extract_se1426_api(str(exec_id))
        assert total == 1

        from staging.models import StagingUsuarioServidor
        assert StagingUsuarioServidor.objects.filter(execution_id=exec_id).exists()

    def test_stops_on_empty_results(self, settings):
        settings.SE1426_API_URL = "http://se1426-api"
        settings.SE1426_API_TOKEN = "tok123"
        settings.SE1426_API_TIMEOUT = 5

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": [], "next": None}
        resp.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = resp

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client
        mock_httpx.HTTPError = Exception

        with patch("extract.tasks.httpx", mock_httpx):
            from extract.tasks import _extract_se1426_api
            total = _extract_se1426_api(str(uuid.uuid4()))
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


class TestExtractStepReset:
    """Testa o branch 'not _created' em get_or_create — reseta step existente para RUNNING."""

    @patch("extract.tasks._extract_se1426_sql")
    def test_se1426_resets_existing_step(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"
        mock_sql.return_value = 10

        from core.models import ETLExecution, ETLStepLog
        from extract.tasks import extract_se1426

        execution = ETLExecution.objects.create(source="se1426")
        # Cria step prévio para forçar o branch 'not _created'
        ETLStepLog.objects.create(
            execution=execution,
            step_name=ETLStepLog.StepName.EXTRACT_SE1426,
            step_order=1,
            status="failed",
            error_detail="erro anterior",
        )

        result = extract_se1426(str(execution.id))
        assert result == 10

        step = ETLStepLog.objects.get(execution=execution, step_name=ETLStepLog.StepName.EXTRACT_SE1426)
        assert step.status == "success"
        assert step.error_detail is None

    @patch("extract.tasks._extract_eol_db_sql")
    def test_eol_db_resets_existing_step(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"
        mock_sql.return_value = 8

        from core.models import ETLExecution, ETLStepLog
        from extract.tasks import extract_eol_db

        execution = ETLExecution.objects.create(source="eol_db")
        ETLStepLog.objects.create(
            execution=execution,
            step_name=ETLStepLog.StepName.EXTRACT_EOL_DB,
            step_order=3,
            status="failed",
            error_detail="falha anterior",
        )

        result = extract_eol_db(str(execution.id))
        assert result == 8

        step = ETLStepLog.objects.get(execution=execution, step_name=ETLStepLog.StepName.EXTRACT_EOL_DB)
        assert step.status == "success"
        assert step.error_detail is None

    @patch("extract.tasks._extract_eol_alunos_sql")
    def test_eol_alunos_resets_existing_step(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"
        mock_sql.return_value = 5

        from core.models import ETLExecution, ETLStepLog
        from extract.tasks import extract_eol_alunos

        execution = ETLExecution.objects.create(source="eol_alunos")
        ETLStepLog.objects.create(
            execution=execution,
            step_name="extract_eol_alunos",
            step_order=4,
            status="failed",
            error_detail="falha anterior",
        )

        result = extract_eol_alunos(str(execution.id))
        assert result == 5

        step = ETLStepLog.objects.get(
            execution=execution, step_name="extract_eol_alunos"
        )
        assert step.status == "success"
        assert step.error_detail is None

    @patch("extract.tasks._extract_coresso_sql")
    def test_coresso_resets_existing_step(self, mock_sql, settings):
        settings.CORESSO_DB_SERVER = "coresso-host"
        mock_sql.return_value = 15

        from core.models import ETLExecution, ETLStepLog
        from extract.tasks import extract_coresso

        execution = ETLExecution.objects.create(source="coresso")
        ETLStepLog.objects.create(
            execution=execution,
            step_name=ETLStepLog.StepName.EXTRACT_CORESSO,
            step_order=2,
            status="failed",
            error_detail="falha anterior",
        )

        result = extract_coresso(str(execution.id))
        assert result == 15

        step = ETLStepLog.objects.get(execution=execution, step_name=ETLStepLog.StepName.EXTRACT_CORESSO)
        assert step.status == "success"
        assert step.error_detail is None


class TestRetryExhausted:
    """Quando retries >= max_retries, a task retorna 0 ao invés de re-lançar."""

    @patch("extract.tasks._extract_se1426_sql", side_effect=RuntimeError("db error"))
    def test_se1426_returns_zero_when_retries_exhausted(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"

        from core.models import ETLExecution, ETLStepLog
        from extract.tasks import extract_se1426

        execution = ETLExecution.objects.create(source="se1426")
        result = extract_se1426.apply(
            args=[str(execution.id)],
            retries=extract_se1426.max_retries,
        ).get()

        assert result == 0
        step = ETLStepLog.objects.get(execution=execution, step_name=ETLStepLog.StepName.EXTRACT_SE1426)
        assert step.status == "failed"

    @patch("extract.tasks._extract_eol_db_sql", side_effect=RuntimeError("db error"))
    def test_eol_db_returns_zero_when_retries_exhausted(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"

        from core.models import ETLExecution, ETLStepLog
        from extract.tasks import extract_eol_db

        execution = ETLExecution.objects.create(source="eol_db")
        result = extract_eol_db.apply(
            args=[str(execution.id)],
            retries=extract_eol_db.max_retries,
        ).get()

        assert result == 0
        step = ETLStepLog.objects.get(execution=execution, step_name=ETLStepLog.StepName.EXTRACT_EOL_DB)
        assert step.status == "failed"

    @patch("extract.tasks._extract_eol_alunos_sql", side_effect=RuntimeError("db error"))
    def test_eol_alunos_returns_zero_when_retries_exhausted(self, mock_sql, settings):
        settings.SE1426_DB_SERVER = "mock-se1426-server"

        from core.models import ETLExecution, ETLStepLog
        from extract.tasks import extract_eol_alunos

        execution = ETLExecution.objects.create(source="eol_alunos")
        result = extract_eol_alunos.apply(
            args=[str(execution.id)],
            retries=extract_eol_alunos.max_retries,
        ).get()

        assert result == 0
        step = ETLStepLog.objects.get(execution=execution, step_name="extract_eol_alunos")
        assert step.status == "failed"

    @patch("extract.tasks._extract_coresso_sql", side_effect=RuntimeError("db error"))
    def test_coresso_returns_zero_when_retries_exhausted(self, mock_sql, settings):
        settings.CORESSO_DB_SERVER = "coresso-host"

        from core.models import ETLExecution, ETLStepLog
        from extract.tasks import extract_coresso

        execution = ETLExecution.objects.create(source="coresso")
        result = extract_coresso.apply(
            args=[str(execution.id)],
            retries=extract_coresso.max_retries,
        ).get()

        assert result == 0
        step = ETLStepLog.objects.get(execution=execution, step_name=ETLStepLog.StepName.EXTRACT_CORESSO)
        assert step.status == "failed"


class TestExtractCoressoApi:
    def test_extracts_users_from_api(self, settings):
        settings.CORESSO_API_URL = "http://coresso-api"
        settings.CORESSO_API_TOKEN = "tok-abc"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [
            {"rf": "12345", "cpf": "52998224725", "nome": "Joao", "email": "j@sme.sp"},
            {"rf": None, "cpf": "39053344705", "nome": "Maria Terceiro", "email": "m@sme.sp"},
        ]
        resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = resp

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client

        import uuid
        exec_id = uuid.uuid4()

        with patch("extract.tasks.httpx", mock_httpx):
            from extract.tasks import _extract_coresso_api
            total = _extract_coresso_api(str(exec_id))

        assert total == 2

        from staging.models import StagingUsuarioServidor, StagingUsuarioTerceiro
        assert StagingUsuarioServidor.objects.filter(execution_id=exec_id, rf="12345").exists()
        assert StagingUsuarioTerceiro.objects.filter(execution_id=exec_id).exists()

    def test_handles_dict_response_with_results_key(self, settings):
        settings.CORESSO_API_URL = "http://coresso-api"
        settings.CORESSO_API_TOKEN = "tok-abc"

        resp = MagicMock()
        resp.json.return_value = {
            "results": [{"rf": "11111", "cpf": "52998224725", "nome": "Teste"}],
        }
        resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = resp

        mock_httpx = MagicMock()
        mock_httpx.Client.return_value = mock_client

        import uuid
        exec_id = uuid.uuid4()

        with patch("extract.tasks.httpx", mock_httpx):
            from extract.tasks import _extract_coresso_api
            total = _extract_coresso_api(str(exec_id))

        assert total == 1


class TestFetchCoressoGroupsForLogin:
    def test_returns_empty_when_no_server(self, settings):
        import sys
        settings.CORESSO_DB_SERVER = ""
        mock_pyodbc = MagicMock()
        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            result = et.fetch_coresso_groups_for_login("user@test.com")
        assert result == []

    def test_returns_empty_when_no_login(self, settings):
        import sys
        settings.CORESSO_DB_SERVER = "coresso-host"
        mock_pyodbc = MagicMock()
        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            result = et.fetch_coresso_groups_for_login("")
        assert result == []

    def test_returns_groups_from_db(self, settings):
        import sys

        settings.CORESSO_DB_SERVER = "coresso-host"
        settings.CORESSO_DB_NAME = "coreSSO"
        settings.CORESSO_DB_USER = "user"
        settings.CORESSO_DB_PASSWORD = "pass"
        settings.CORESSO_DB_TIMEOUT = 5

        Row = namedtuple("Row", ["gru_id", "gru_nome", "sis_id", "sis_nome"])
        mock_row = Row("GRU001", "Grupo Teste", 1, "Sistema Teste")

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [mock_row]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_pyodbc = MagicMock()
        mock_pyodbc.connect.return_value = mock_conn

        with patch.dict(sys.modules, {"pyodbc": mock_pyodbc}):
            from extract import tasks as et
            result = et.fetch_coresso_groups_for_login("user@test.com")

        assert len(result) == 1
        assert result[0]["gru_id"] == "GRU001"
        assert result[0]["sis_nome"] == "Sistema Teste"


class TestSlugifySigla:
    def test_removes_accents(self):
        from extract.tasks import _slugify_sigla
        assert _slugify_sigla("Ação Educação") == "acao-educacao"

    def test_returns_fallback_for_empty_string(self):
        from extract.tasks import _slugify_sigla
        result = _slugify_sigla("")
        assert result.startswith("sistema-")

    def test_replaces_spaces_with_hyphens(self):
        from extract.tasks import _slugify_sigla
        assert _slugify_sigla("Sistema ABC") == "sistema-abc"

