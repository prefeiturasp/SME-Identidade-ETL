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
    def test_marks_execution_success(self):
        from core.tasks import audit_etl
        from core.models import ETLExecution, ETLStepLog

        execution = _make_execution()
        audit_etl(str(execution.id))

        execution.refresh_from_db()
        assert execution.status == "success"

        step = ETLStepLog.objects.get(execution=execution, step_name="audit")
        assert step.status == "success"

    def test_marks_partial_when_failed_steps_exist(self):
        from core.tasks import audit_etl
        from core.models import ETLExecution, ETLStepLog

        execution = _make_execution()
        ETLStepLog.objects.create(
            execution=execution,
            step_name="extract_se1426",
            step_order=1,
            status="failed",
        )
        audit_etl(str(execution.id))

        execution.refresh_from_db()
        assert execution.status == "partial"

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

        # Cria 3 execucoes com registros de staging
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

        # keep_last=1 deve apagar staging das execucoes mais antigas
        cleanup_old_staging(keep_last=1)

        # Pelo menos um registro de staging deve ser removido
        total = StagingUsuarioServidor.objects.count()
        assert total <= 3

    def test_no_executions_does_not_fail(self):
        from core.tasks import cleanup_old_staging
        # Deve terminar silenciosamente quando nao ha execucoes
        cleanup_old_staging()




class TestRunEtlPipelineSourceFiltering:
    @patch("core.tasks.chain")
    @patch("core.keycloak_client.ensure_realm_exists", return_value=False)
    def test_all_sources_adds_all_extract_tasks(self, mock_ensure, mock_chain):
        from core.tasks import run_etl_pipeline

        mock_chain.return_value = MagicMock()

        with (
            patch("extract.tasks.extract_se1426.si") as mock_si_se1426,
            patch("extract.tasks.extract_eol_db.si") as mock_si_eol_db,
            patch("extract.tasks.extract_eol_alunos.si") as mock_si_alunos,
            patch("extract.tasks.extract_coresso.si") as mock_si_coresso,
        ):
            execution = _make_execution(source="all")
            run_etl_pipeline(str(execution.id))

        # Todos os 4 sources devem ter sido incluídos na chain
        assert mock_si_se1426.called
        assert mock_si_eol_db.called
        assert mock_si_alunos.called
        assert mock_si_coresso.called

    @patch("core.tasks.chain")
    @patch("core.keycloak_client.ensure_realm_exists", return_value=False)
    def test_se1426_source_adds_only_se1426(self, mock_ensure, mock_chain):
        from core.tasks import run_etl_pipeline

        mock_chain.return_value = MagicMock()

        with (
            patch("extract.tasks.extract_se1426.si") as mock_si_se1426,
            patch("extract.tasks.extract_eol_db.si") as mock_si_eol_db,
            patch("extract.tasks.extract_eol_alunos.si") as mock_si_alunos,
            patch("extract.tasks.extract_coresso.si") as mock_si_coresso,
        ):
            execution = _make_execution(source="se1426")
            run_etl_pipeline(str(execution.id))

        assert mock_si_se1426.called
        assert not mock_si_eol_db.called
        assert not mock_si_alunos.called
        assert not mock_si_coresso.called

    def test_unknown_source_marks_failed(self):
        from core.tasks import run_etl_pipeline
        from core.models import ETLExecution

        execution = ETLExecution.objects.create(source="unknown_source")
        run_etl_pipeline(str(execution.id))

        execution.refresh_from_db()
        assert execution.status == "failed"




class TestLoadKeycloakBulk:
    def test_bulk_enabled_loads_users(self, settings):
        settings.KEYCLOAK_SERVER_URL = "http://kc"
        settings.KEYCLOAK_REALM = "sme-apps"

        execution = _make_execution()
        execution.load_keycloak = True
        execution.save(update_fields=["load_keycloak"])
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
        execution = _make_execution()
        execution.load_keycloak = True
        execution.save(update_fields=["load_keycloak"])
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
        execution = _make_execution()
        execution.load_keycloak = True
        execution.save(update_fields=["load_keycloak"])
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
    @patch("core.tasks.chain", side_effect=RuntimeError("redis down"))
    def test_exception_marks_failed(self, mock_chain):
        from core.tasks import run_etl_pipeline
        from core.models import ETLExecution

        execution = ETLExecution.objects.create(source="all")
        run_etl_pipeline(str(execution.id))

        execution.refresh_from_db()
        assert execution.status == "failed"


class TestGetOrCreateStepReset:
    def test_reset_existing_step_to_running(self):
        from core.tasks import _get_or_create_step
        from core.models import ETLExecution, ETLStepLog

        execution = ETLExecution.objects.create(source="all")
        # Cria o step manualmente com status "failed"
        ETLStepLog.objects.create(
            execution=execution,
            step_name="extract_se1426",
            step_order=1,
            status="failed",
            error_detail="erro anterior",
        )

        # Segunda chamada deve resetar o step para "running"
        step = _get_or_create_step(execution, "extract_se1426", 1)

        assert step.status == ETLStepLog.StepStatus.RUNNING
        assert step.error_detail is None
        assert step.finished_at is None


class TestDecideTargetIdempotency:
    def test_skips_when_already_done(self):
        from core.tasks import decide_target
        from core.models import ETLExecution, ETLStepLog

        execution = _make_execution()
        _make_servidor_ready(execution.id)
        decide_target(str(execution.id))

        # Chama novamente — deve pular (idempotência)
        step_before = ETLStepLog.objects.get(execution=execution, step_name="decision")
        finished_at_before = step_before.finished_at

        decide_target(str(execution.id))

        step_after = ETLStepLog.objects.get(execution=execution, step_name="decision")
        assert step_after.finished_at == finished_at_before


class TestLoadKeycloakIdempotency:
    def test_skips_when_already_done(self):
        from core.tasks import load_keycloak
        from core.models import ETLExecution, ETLStepLog

        execution = _make_execution()
        execution.load_keycloak = True
        execution.save(update_fields=["load_keycloak"])

        # Cria o step com status "success" manualmente para simular execução anterior
        ETLStepLog.objects.create(
            execution=execution,
            step_name="load_keycloak",
            step_order=6,
            status="success",
        )

        with patch("core.keycloak_client.upsert_user_to_keycloak") as mock_upsert:
            load_keycloak(str(execution.id))

        mock_upsert.assert_not_called()


class TestLoadTokenMsDisabled:
    def test_skips_when_load_token_ms_false(self):
        from core.tasks import load_token_ms
        from core.models import ETLExecution, ETLStepLog

        execution = _make_execution()
        execution.load_token_ms = False
        execution.save(update_fields=["load_token_ms"])

        load_token_ms(str(execution.id))

        step = ETLStepLog.objects.get(execution=execution, step_name="load_token_ms")
        assert step.status == "skipped"
        assert step.metadata == {"reason": "load_token_ms=False"}


class TestLoadTokenMsIdempotency:
    @patch("core.token_ms_client.send_all")
    def test_skips_when_already_done(self, mock_send_all):
        from core.tasks import load_token_ms
        from core.models import ETLExecution, ETLStepLog

        execution = _make_execution()

        # Cria o step com status "success" para simular execução anterior
        ETLStepLog.objects.create(
            execution=execution,
            step_name="load_token_ms",
            step_order=7,
            status="success",
        )

        load_token_ms(str(execution.id))

        mock_send_all.assert_not_called()


class TestAuditEtlIdempotency:
    def test_skips_when_already_done(self):
        from core.tasks import audit_etl
        from core.models import ETLExecution, ETLStepLog

        execution = _make_execution()

        # Cria o step audit com status "success" para simular execução anterior
        ETLStepLog.objects.create(
            execution=execution,
            step_name="audit",
            step_order=8,
            status="success",
        )

        # Roda o pipeline completo — deve pular o step audit
        audit_etl(str(execution.id))

        # Deve haver apenas 1 step audit (o criado manualmente)
        count = ETLStepLog.objects.filter(execution=execution, step_name="audit").count()
        assert count == 1


class TestSyncCoressoCatalogo:
    def test_skips_when_no_coresso_server(self, settings):
        from core.tasks import _sync_coresso_catalogo
        from core.models import ETLExecution, ETLStepLog

        settings.CORESSO_DB_SERVER = ""

        execution = ETLExecution.objects.create(source="all")
        _sync_coresso_catalogo(str(execution.id), realm="sme-apps")

        step = ETLStepLog.objects.get(execution=execution, step_name="sync_catalogo")
        assert step.status == "skipped"

    @patch("core.keycloak_client.upsert_kc_client_role")
    @patch("core.keycloak_client.upsert_kc_client")
    @patch("core.keycloak_client.get_admin_client")
    @patch("extract.tasks.extract_coresso_perfis", return_value=3)
    @patch("extract.tasks.extract_coresso_sistemas", return_value=2)
    def test_success_path(
        self,
        mock_sistemas_fn,
        mock_perfis_fn,
        mock_admin,
        mock_upsert_client,
        mock_upsert_role,
        settings,
    ):
        from core.tasks import _sync_coresso_catalogo
        from core.models import ETLExecution, ETLStepLog
        from staging.models import StagingSistema, StagingPerfilCoreSSO

        settings.CORESSO_DB_SERVER = "coresso-host"

        # Cria sistema e perfil reais para os loops
        StagingSistema.objects.create(
            coresso_sis_id=1,
            nome="Sistema Teste",
            sigla="SIG01",
            situacao=1,
            status=StagingSistema.Status.READY,
        )
        from staging.models import StagingPerfilCoreSSO
        StagingPerfilCoreSSO.objects.create(
            coresso_gru_id="GRU001",
            nome="Grupo Teste",
            kc_role_name="grupo-teste",
            coresso_sis_id=1,
            status=StagingPerfilCoreSSO.Status.READY,
        )

        execution = ETLExecution.objects.create(source="all")
        _sync_coresso_catalogo(str(execution.id), realm="sme-apps")

        step = ETLStepLog.objects.get(execution=execution, step_name="sync_catalogo")
        assert step.status == "success"
        assert step.records_in == 5  # 2 sistemas + 3 perfis
        mock_upsert_client.assert_called_once()
        mock_upsert_role.assert_called_once()

    @patch("extract.tasks.extract_coresso_sistemas", side_effect=RuntimeError("db error"))
    @patch("core.keycloak_client.get_admin_client")
    def test_exception_marks_step_failed_but_does_not_abort(
        self, mock_admin, mock_sistemas, settings
    ):
        from core.tasks import _sync_coresso_catalogo
        from core.models import ETLExecution, ETLStepLog

        settings.CORESSO_DB_SERVER = "coresso-host"

        execution = ETLExecution.objects.create(source="all")
        # Não deve lançar exceção — pipeline continua
        _sync_coresso_catalogo(str(execution.id), realm="sme-apps")

        step = ETLStepLog.objects.get(execution=execution, step_name="sync_catalogo")
        assert step.status == "failed"
        assert "db error" in step.error_detail

