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
        from core.tasks import trigger_scheduled_etl
        from core.models import ETLExecution

        mock_task = MagicMock()
        mock_task.id = "celery-beat-task-2"

        mock_delay.return_value = mock_task

        trigger_scheduled_etl()

        exec_ = ETLExecution.objects.first()

        assert exec_.source == "all"
        assert exec_.target_realm == "sme-apps"


class TestRunEtlPipeline:
    @patch("core.tasks.chord")
    def test_run_etl_pipeline_without_extract_tasks(self, mock_chord):
        from core.models import ETLExecution
        from core.tasks import run_etl_pipeline

        execution = ETLExecution.objects.create(
            source="invalid",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        run_etl_pipeline(str(execution.id))

        execution.refresh_from_db()

        assert execution.status == "failed"

        mock_chord.assert_not_called()

    @patch("core.tasks.chord")
    @patch("core.tasks.chain")
    def test_run_etl_pipeline_dispatches(
        self,
        mock_chain,
        mock_chord,
    ):
        from core.models import ETLExecution
        from core.tasks import run_etl_pipeline

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        fake_sig = MagicMock()

        with (
            patch("extract.tasks.extract_se1426.s", return_value=fake_sig),
            patch("extract.tasks.extract_eol_db.s", return_value=fake_sig),
            patch("extract.tasks.extract_eol_alunos.s", return_value=fake_sig),
            patch("extract.tasks.extract_coresso.s", return_value=fake_sig),
            patch("staging.tasks.transform_staging.si", return_value=fake_sig),
            patch("staging.tasks.crossref_dedup.si", return_value=fake_sig),
            patch("core.tasks.decide_target.si", return_value=fake_sig),
            patch("core.tasks.load_keycloak.si", return_value=fake_sig),
            patch("core.tasks.load_token_ms.si", return_value=fake_sig),
            patch("core.tasks.audit_etl.si", return_value=fake_sig),
        ):
            mock_chord.return_value = MagicMock()

            run_etl_pipeline(str(execution.id))

        mock_chord.assert_called_once()
        mock_chain.assert_called_once()

    @patch(
        "extract.tasks.extract_se1426.s",
        side_effect=Exception("boom"),
    )
    def test_run_etl_pipeline_exception(self, mock_extract):
        from core.models import ETLExecution
        from core.tasks import run_etl_pipeline

        execution = ETLExecution.objects.create(
            source="se1426",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        run_etl_pipeline(str(execution.id))

        execution.refresh_from_db()

        assert execution.status == "failed"
        assert execution.total_errors == 1


class TestDecideTargetTask:
    @patch("core.keycloak_client.build_kc_payload")
    @patch("core.keycloak_client.build_token_ms_payload")
    def test_decide_target_routes_records(
        self,
        mock_token,
        mock_kc,
    ):
        from core.models import ETLExecution, ETLStepLog
        from core.tasks import decide_target
        from staging.models import StagingUsuarioServidor

        mock_kc.return_value = {"username": "52998224725"}
        mock_token.return_value = {"cpf": "52998224725"}

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

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
        from staging.models import StagingUsuarioServidor

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        with patch(
            "core.keycloak_client.build_kc_payload",
            side_effect=RuntimeError("test error"),
        ):
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
    def test_load_keycloak_skipped_when_bulk_disabled(
        self,
        settings,
    ):
        settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED = False

        from core.models import ETLExecution, ETLStepLog
        from core.tasks import load_keycloak

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        load_keycloak(str(execution.id))

        step = ETLStepLog.objects.get(
            execution=execution,
            step_name="load_keycloak",
        )

        assert step.status == "skipped"

    @patch("core.keycloak_client.upsert_user_to_keycloak")
    @patch("core.keycloak_client.get_admin_client")
    def test_load_keycloak_success(
        self,
        mock_admin,
        mock_upsert,
        settings,
    ):
        settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED = True

        from core.models import ETLExecution, ETLStepLog
        from core.tasks import load_keycloak
        from staging.models import StagingUsuarioServidor

        mock_upsert.return_value = {"action": "created"}

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
            load_keycloak=True,
        )

        user = StagingUsuarioServidor.objects.create(
            cpf="123",
            source="se1426",
            execution_id=execution.id,
            status="ready",
        )

        load_keycloak(str(execution.id))

        user.refresh_from_db()

        assert user.status == "loaded"

        step = ETLStepLog.objects.get(execution=execution)

        assert step.status == "success"

    @patch("core.keycloak_client.upsert_user_to_keycloak")
    @patch("core.keycloak_client.get_admin_client")
    def test_load_keycloak_skipped(
        self,
        mock_admin,
        mock_upsert,
        settings,
    ):
        settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED = True

        from core.models import ETLExecution
        from core.tasks import load_keycloak
        from staging.models import StagingUsuarioServidor

        mock_upsert.return_value = {"action": "skipped"}

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
            load_keycloak=True,
        )

        user = StagingUsuarioServidor.objects.create(
            cpf="123",
            source="se1426",
            execution_id=execution.id,
            status="ready",
        )

        load_keycloak(str(execution.id))

        user.refresh_from_db()

        assert user.status == "skipped"

    @patch(
        "core.keycloak_client.get_admin_client",
        side_effect=Exception("kc error"),
    )
    def test_load_keycloak_retry(
        self,
        mock_admin,
        settings,
    ):
        from celery.exceptions import Retry

        from core.models import ETLExecution
        from core.tasks import load_keycloak

        settings.ETL_LOAD_KEYCLOAK_BULK_ENABLED = True

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
            load_keycloak=True,
        )

        with patch.object(
            load_keycloak,
            "retry",
            side_effect=Retry(),
        ) as mock_retry:

            with pytest.raises(Retry):
                load_keycloak(str(execution.id))

        mock_retry.assert_called_once()


class TestLoadTokenMsTask:
    @patch("core.token_ms_client.send_all")
    def test_load_token_ms_success(self, mock_send):
        from core.models import ETLExecution, ETLStepLog
        from core.tasks import load_token_ms

        mock_send.return_value = {
            "sent": 10,
            "batches": 2,
        }

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        load_token_ms(str(execution.id))

        step = ETLStepLog.objects.get(execution=execution)

        assert step.status == "success"
        assert step.records_out == 10

    @patch(
        "core.token_ms_client.send_all",
        side_effect=Exception("token error"),
    )
    def test_load_token_ms_retry(self, mock_send):
        from celery.exceptions import Retry

        from core.models import ETLExecution
        from core.tasks import load_token_ms

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        with patch.object(
            load_token_ms,
            "retry",
            side_effect=Retry(),
        ) as mock_retry:

            with pytest.raises(Retry):
                load_token_ms(str(execution.id))

        mock_retry.assert_called_once()


class TestAuditEtlTask:
    def test_audit_etl_success(self):
        from core.models import ETLExecution, ETLStepLog
        from core.tasks import audit_etl

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
            status="running",
        )

        ETLStepLog.objects.create(
            execution=execution,
            step_name="extract",
            step_order=1,
            status="success",
        )

        audit_etl(str(execution.id))

        execution.refresh_from_db()

        assert execution.status == "success"

    def test_audit_etl_partial(self):
        from core.models import ETLExecution, ETLStepLog
        from core.tasks import audit_etl

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
            status="running",
        )

        ETLStepLog.objects.create(
            execution=execution,
            step_name="extract",
            step_order=1,
            status="failed",
            records_error=1,
        )

        audit_etl(str(execution.id))

        execution.refresh_from_db()

        assert execution.status == "partial"


class TestCleanupOldStaging:
    def test_cleanup_old_staging(self):
        from core.models import ETLExecution
        from core.tasks import cleanup_old_staging
        from staging.models import StagingUsuarioServidor

        ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
            status="success",
        )

        old_execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
            status="failed",
        )

        StagingUsuarioServidor.objects.create(
            cpf="1",
            source="se1426",
            execution_id=old_execution.id,
        )

        cleanup_old_staging()

        assert (
            StagingUsuarioServidor.objects
            .filter(execution_id=old_execution.id)
            .count()
            == 0
        )


class TestDecideTargetBulkUpdate:
    @patch("core.keycloak_client.build_kc_payload")
    @patch("core.keycloak_client.build_token_ms_payload")
    def test_decide_target_bulk_update_flushes_at_500(
        self,
        mock_token,
        mock_kc,
    ):
        from core.models import ETLExecution
        from core.tasks import decide_target
        from staging.models import StagingUsuarioServidor

        mock_kc.return_value = {"username": "user"}
        mock_token.return_value = {"cpf": "123"}

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        usuarios = [
            StagingUsuarioServidor(
                cpf=f"{i}",
                source="se1426",
                execution_id=execution.id,
                status="ready",
            )
            for i in range(501)
        ]

        StagingUsuarioServidor.objects.bulk_create(usuarios)

        with patch.object(
            StagingUsuarioServidor.objects,
            "bulk_update",
            wraps=StagingUsuarioServidor.objects.bulk_update,
        ) as mock_bulk_update:

            decide_target(str(execution.id))

        assert mock_bulk_update.called
        assert mock_bulk_update.call_count >= 2


    @patch("core.token_ms_client.send_all")
    @patch("core.keycloak_client.build_token_ms_payload")
    def test_load_token_ms_builds_payload_when_route_missing(
        self,
        mock_build_payload,
        mock_send,
    ):
        from core.models import ETLExecution
        from core.tasks import load_token_ms
        from staging.models import StagingUsuarioServidor

        mock_build_payload.return_value = {
            "cpf": "123",
        }

        def fake_send(payloads, execution_id):
            list(payloads)
            return {
                "sent": 1,
                "batches": 1,
            }

        mock_send.side_effect = fake_send

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        StagingUsuarioServidor.objects.create(
            cpf="123",
            source="se1426",
            execution_id=execution.id,
            status="ready",
            raw_data={},
        )

        load_token_ms(str(execution.id))

        mock_build_payload.assert_called_once()

    @patch("core.token_ms_client.send_all")
    @patch("core.keycloak_client.build_token_ms_payload")
    def test_load_token_ms_uses_route_payload_when_available(
        self,
        mock_build_payload,
        mock_send,
    ):
        from core.models import ETLExecution
        from core.tasks import load_token_ms
        from staging.models import StagingUsuarioServidor

        captured_payloads = []

        def fake_send(payloads, execution_id):
            captured_payloads.extend(list(payloads))
            return {
                "sent": 1,
                "batches": 1,
            }

        mock_send.side_effect = fake_send

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
        )

        StagingUsuarioServidor.objects.create(
            cpf="123",
            source="se1426",
            execution_id=execution.id,
            status="loaded",
            raw_data={
                "route": {
                    "token_ms": {
                        "cpf": "payload-route",
                    }
                }
            },
        )

        load_token_ms(str(execution.id))

        assert captured_payloads[0]["cpf"] == "payload-route"

        mock_build_payload.assert_not_called()


class TestAuditEtlExceptionCoverage:
    def test_audit_etl_exception_branch(self):
        from core.models import ETLExecution, ETLStepLog
        from core.tasks import audit_etl

        execution = ETLExecution.objects.create(
            source="all",
            trigger_type=ETLExecution.TriggerType.MANUAL,
            status="running",
        )

        ETLStepLog.objects.create(
            execution=execution,
            step_name="extract",
            step_order=1,
            status="success",
        )

        with patch(
            "django.db.models.query.QuerySet.exists",
            side_effect=[False, Exception("audit failure")],
        ):
            audit_etl(str(execution.id))

        execution.refresh_from_db()

        assert execution.status == "failed"

        step = (
            ETLStepLog.objects
            .filter(
                execution=execution,
                step_name="audit",
            )
            .order_by("-id")
            .first()
        )

        assert step.status == "failed"
        assert "audit failure" in step.error_detail
        assert step.finished_at is not None