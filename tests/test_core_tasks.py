import uuid
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


class TestTriggerScheduledEtl:
    @patch("core.tasks.run_etl_pipeline.delay")
    def test_creates_execution_and_triggers_pipeline(self, mock_delay):
        from core.models import ETLExecution
        from core.tasks import trigger_scheduled_etl

        mock_task = MagicMock()
        mock_task.id = "celery-beat-task"
        mock_delay.return_value = mock_task

        result = trigger_scheduled_etl("all", "sme-apps")
        assert ETLExecution.objects.count() == 1
        exec_ = ETLExecution.objects.first()
        assert exec_.trigger_type == ETLExecution.TriggerType.SCHEDULED
        assert exec_.source == "all"
        assert exec_.executed_by == "celery-beat"
        mock_delay.assert_called_once_with(str(exec_.id))
        assert result == str(exec_.id)

    @patch("core.tasks.run_etl_pipeline.delay")
    def test_default_source_and_realm(self, mock_delay):
        from core.models import ETLExecution
        from core.tasks import trigger_scheduled_etl

        mock_task = MagicMock()
        mock_task.id = "celery-beat-task-2"
        mock_delay.return_value = mock_task

        trigger_scheduled_etl()
        exec_ = ETLExecution.objects.first()
        assert exec_.source == "all"
        assert exec_.target_realm == "sme-apps"


class TestDecideTargetTask:
    """Testa a task decide_target com dados de staging mockados."""

    @patch("core.keycloak_client.build_kc_payload")
    @patch("core.keycloak_client.build_token_ms_payload")
    def test_decide_target_routes_records(self, mock_token, mock_kc):
        from core.models import ETLExecution, ETLStepLog
        from core.tasks import decide_target
        from staging.models import StagingUsuarioServidor

        mock_kc.return_value = {"username": "52998224725"}
        mock_token.return_value = {"cpf": "52998224725"}

        exec_id = uuid.uuid4()
        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        # Cria registros em status ready
        u = StagingUsuarioServidor.objects.create(
            cpf="52998224725",
            rf="12345",
            nome="JOAO SILVA",
            source="se1426",
            execution_id=execution.id,
            status="ready",
        )

        decide_target(str(execution.id))

        u.refresh_from_db()
        assert "route" in u.raw_data
        assert "keycloak" in u.raw_data["route"]
        assert "token_ms" in u.raw_data["route"]

        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "success"

    def test_decide_target_marks_failed_on_exception(self):
        from core.models import ETLExecution, ETLStepLog
        from core.tasks import decide_target

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        # Monkeypatch em build_kc_payload para levantar excecao
        with patch("core.keycloak_client.build_kc_payload", side_effect=RuntimeError("test error")):
            from staging.models import StagingUsuarioServidor
            StagingUsuarioServidor.objects.create(
                cpf="52998224725",
                source="se1426",
                execution_id=execution.id,
                status="ready",
            )
            with pytest.raises(RuntimeError):
                decide_target(str(execution.id))

        step = ETLStepLog.objects.get(execution=execution)
        assert step.status == "failed"
        assert "test error" in step.error_detail


class TestLoadKeycloakTask:
    def test_load_keycloak_skipped_when_bulk_disabled(self, settings):
        """ETL_LOAD_KEYCLOAK_BULK_ENABLED=False deve registrar step SKIPPED."""
        settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED = False
        from core.models import ETLExecution, ETLStepLog
        from core.tasks import load_keycloak

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )
        load_keycloak(str(execution.id))
        step = ETLStepLog.objects.get(execution=execution, step_name="load_keycloak")
        assert step.status == "skipped"
