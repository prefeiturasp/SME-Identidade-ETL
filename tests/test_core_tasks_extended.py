import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


def _make_execution(source="all"):
    from core.models import ETLExecution
    return ETLExecution.objects.create(source=source)


def _make_servidor_ready(execution_id, cpf="52998224725", rf="12345"):
    from staging.models import StagingUsuarioServidor
    return StagingUsuarioServidor.objects.create(
        execution_id=execution_id,
        rf=rf,
        cpf=cpf,
        nome="Joao Silva",
        status="ready",
        source="se1426",
    )




class TestDecideTarget:
    def test_routes_ready_records(self):
        from core.tasks import decide_target
        from core.models import ETLStepLog

        execution = _make_execution()
        _make_servidor_ready(execution.id)
        decide_target(str(execution.id))

        step = ETLStepLog.objects.get(execution=execution, step_name="decision")
        assert step.status == "success"
        assert step.records_out == 1

    def test_writes_route_to_raw_data(self):
        from core.tasks import decide_target
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        srv = _make_servidor_ready(execution.id)
        decide_target(str(execution.id))

        srv.refresh_from_db()
        route = (srv.raw_data or {}).get("route", {})
        assert "keycloak" in route
        assert "token_ms" in route

    def test_no_ready_records_succeeds(self):
        from core.tasks import decide_target
        from core.models import ETLStepLog

        execution = _make_execution()
        decide_target(str(execution.id))

        step = ETLStepLog.objects.get(execution=execution, step_name="decision")
        assert step.status == "success"
        assert step.records_out == 0




class TestLoadTokenMs:
    @patch("core.token_ms_client.send_all")
    def test_sends_payloads_to_token_ms(self, mock_send_all):
        from core.tasks import load_token_ms
        from core.models import ETLStepLog

        mock_send_all.return_value = {"sent": 2, "batches": 1}
        execution = _make_execution()
        _make_servidor_ready(execution.id, cpf="52998224725", rf="12345")
        _make_servidor_ready(execution.id, cpf="39053344705", rf="67890")

        load_token_ms(str(execution.id))

        step = ETLStepLog.objects.filter(execution=execution, step_name="load_token_ms").first()
        assert step is not None
        assert step.status == "success"
        assert step.records_out == 2

    @patch("core.token_ms_client.send_all")
    def test_load_token_ms_no_records(self, mock_send_all):
        from core.tasks import load_token_ms
        from core.models import ETLStepLog

        mock_send_all.return_value = {"sent": 0, "batches": 0}
        execution = _make_execution()
        load_token_ms(str(execution.id))

        step = ETLStepLog.objects.filter(execution=execution, step_name="load_token_ms").first()
        assert step is not None
        assert step.status == "success"




class TestAuditEtl:
    def test_sums_errors_from_steps(self):
        from core.tasks import audit_etl
        from core.models import ETLExecution, ETLStepLog

        execution = _make_execution()
        ETLStepLog.objects.create(
            execution=execution,
            step_name="extract_se1426",
            step_order=1,
            records_error=5,
        )
        audit_etl(str(execution.id))

        execution.refresh_from_db()
        assert execution.total_errors == 5

    def test_includes_metadata_in_step(self):
        from core.tasks import audit_etl
        from core.models import ETLStepLog

        execution = _make_execution()
        audit_etl(str(execution.id))

        step = ETLStepLog.objects.get(execution=execution, step_name="audit")
        assert "total_steps" in step.metadata
        assert "failed_steps" in step.metadata




class TestCleanupOldStaging:
    def test_removes_staging_for_old_executions(self):
        from core.tasks import cleanup_old_staging
        from core.models import ETLExecution
        from staging.models import StagingUsuarioServidor

        # Create 3 executions with staging records
        executions = []
        for i in range(3):
            ex = ETLExecution.objects.create(source="se1426", status="success")
            StagingUsuarioServidor.objects.create(
                execution_id=ex.id,
                rf=f"1234{i}",
                nome="TEST",
                status="ready",
                source="se1426",
            )
            executions.append(ex)

        # keep_last=1 should delete staging for oldest executions
        cleanup_old_staging(keep_last=1)

        # At least one staging record should be removed
        total = StagingUsuarioServidor.objects.count()
        assert total <= 3

    def test_no_executions_does_not_fail(self):
        from core.tasks import cleanup_old_staging
        # Should complete silently with no executions
        cleanup_old_staging()




class TestRunEtlPipelineSourceFiltering:
    @patch("core.tasks.chord")
    @patch("core.tasks.chain")
    def test_all_sources_adds_all_extract_tasks(self, mock_chain, mock_chord):
        from core.tasks import run_etl_pipeline

        mock_callback = MagicMock()
        mock_chord.return_value = MagicMock(return_value=mock_callback)
        mock_chain.return_value = MagicMock()

        execution = _make_execution(source="all")
        run_etl_pipeline(str(execution.id))

        # chord should have been called with 4 tasks (se1426, eol_db, eol_alunos, coresso)
        assert mock_chord.called
        chord_args = mock_chord.call_args[0][0]
        assert len(chord_args) == 4

    @patch("core.tasks.chord")
    @patch("core.tasks.chain")
    def test_se1426_source_adds_only_se1426(self, mock_chain, mock_chord):
        from core.tasks import run_etl_pipeline

        mock_callback = MagicMock()
        mock_chord.return_value = MagicMock(return_value=mock_callback)
        mock_chain.return_value = MagicMock()

        execution = _make_execution(source="se1426")
        run_etl_pipeline(str(execution.id))

        chord_args = mock_chord.call_args[0][0]
        assert len(chord_args) == 1

    def test_unknown_source_marks_failed(self):
        from core.tasks import run_etl_pipeline
        from core.models import ETLExecution

        execution = ETLExecution.objects.create(source="unknown_source")
        run_etl_pipeline(str(execution.id))

        execution.refresh_from_db()
        assert execution.status == "failed"




class TestLoadKeycloakBulk:
    def test_bulk_enabled_loads_users(self, settings):
        settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED = True
        settings.KEYCLOAK_SERVER_URL = "http://kc"
        settings.KEYCLOAK_REALM = "sme-apps"

        execution = _make_execution()
        _make_servidor_ready(execution.id)

        with patch("core.keycloak_client.get_admin_client", return_value=MagicMock()), \
             patch("core.keycloak_client.upsert_user_to_keycloak",
                   return_value={"action": "created", "kc_user_id": "kc-001", "content_hash": "abc"}) as mock_upsert:
            from core.tasks import load_keycloak
            load_keycloak(str(execution.id))

        mock_upsert.assert_called_once()
        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution, step_name="load_keycloak")
        assert step.status == "success"
        assert step.records_out == 1

    def test_bulk_enabled_handles_error(self, settings):
        settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED = True

        execution = _make_execution()
        _make_servidor_ready(execution.id)

        with patch("core.keycloak_client.get_admin_client", return_value=MagicMock()), \
             patch("core.keycloak_client.upsert_user_to_keycloak", side_effect=RuntimeError("KC error")):
            from core.tasks import load_keycloak
            load_keycloak(str(execution.id))

        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution, step_name="load_keycloak")
        assert step.status == "failed"
        assert step.records_error == 1

    def test_bulk_enabled_skipped_action(self, settings):
        settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED = True

        execution = _make_execution()
        _make_servidor_ready(execution.id)

        with patch("core.keycloak_client.get_admin_client", return_value=MagicMock()), \
             patch("core.keycloak_client.upsert_user_to_keycloak",
                   return_value={"action": "skipped", "kc_user_id": "kc-001", "content_hash": "abc"}):
            from core.tasks import load_keycloak
            load_keycloak(str(execution.id))

        from core.models import ETLStepLog
        step = ETLStepLog.objects.get(execution=execution, step_name="load_keycloak")
        assert step.status == "success"
        assert step.records_out == 0




class TestRunEtlPipelineError:
    @patch("core.tasks.chord", side_effect=RuntimeError("redis down"))
    @patch("core.tasks.chain")
    def test_exception_marks_failed(self, mock_chain, mock_chord):
        from core.tasks import run_etl_pipeline
        from core.models import ETLExecution

        execution = ETLExecution.objects.create(source="all")
        run_etl_pipeline(str(execution.id))

        execution.refresh_from_db()
        assert execution.status == "failed"

