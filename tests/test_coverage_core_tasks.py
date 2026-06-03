"""Testes de cobertura adicional para core/tasks.py."""
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


def _make_execution(source="all", **kwargs):
    from core.models import ETLExecution

    return ETLExecution.objects.create(source=source, target_realm="sme-apps", **kwargs)


def _make_servidor(execution_id, cpf="52998224725", rf="12345", status="ready"):
    from staging.models import StagingUsuarioServidor

    return StagingUsuarioServidor.objects.create(
        execution_id=execution_id,
        rf=rf,
        cpf=cpf,
        nome="Test User",
        status=status,
        source="se1426",
    )


class TestCheckCancelled:
    def test_raises_when_execution_cancelled(self):
        from core.tasks import ExecutionCancelledError, _check_cancelled

        execution = _make_execution(status="cancelled")
        with pytest.raises(ExecutionCancelledError):
            _check_cancelled(str(execution.id))

    def test_no_raise_when_running(self):
        from core.tasks import _check_cancelled

        execution = _make_execution(status="running")
        _check_cancelled(str(execution.id))  # should not raise


class TestResolveUserTypeModels:
    def test_all_returns_three_models(self):
        from staging.models import StagingUsuarioAluno, StagingUsuarioServidor, StagingUsuarioTerceiro

        from core.tasks import _resolve_user_type_models

        result = _resolve_user_type_models("all")
        classes = [cls for cls, _ in result]
        assert StagingUsuarioServidor in classes
        assert StagingUsuarioAluno in classes
        assert StagingUsuarioTerceiro in classes

    def test_servidor_returns_one_model(self):
        from staging.models import StagingUsuarioServidor

        from core.tasks import _resolve_user_type_models

        result = _resolve_user_type_models("servidor")
        assert len(result) == 1
        assert result[0][0] is StagingUsuarioServidor
        assert result[0][1]["rf_field"] is True

    def test_aluno_terceiro_returns_two_models(self):
        from staging.models import StagingUsuarioAluno, StagingUsuarioTerceiro

        from core.tasks import _resolve_user_type_models

        result = _resolve_user_type_models("aluno,terceiro")
        classes = [cls for cls, _ in result]
        assert StagingUsuarioAluno in classes
        assert StagingUsuarioTerceiro in classes
        assert len(result) == 2


class TestSyncCoressoCatalogoErrors:
    @patch("core.keycloak_client.upsert_kc_client", side_effect=RuntimeError("kc error"))
    @patch("core.keycloak_client.get_admin_client", return_value=MagicMock())
    @patch("extract.tasks.extract_coresso_perfis", return_value=0)
    @patch("extract.tasks.extract_coresso_sistemas", return_value=1)
    def test_upsert_kc_client_exception_increments_error_count(
        self, mock_sistemas, mock_perfis, mock_admin, mock_upsert, settings
    ):
        from staging.models import StagingSistema

        settings.CORESSO_DB_SERVER = "mock-coresso"
        StagingSistema.objects.create(
            coresso_sis_id=9901,
            nome="Test System",
            sigla="ts",
            situacao=1,
        )
        from core.models import ETLStepLog

        from core.tasks import _sync_coresso_catalogo

        execution = _make_execution()
        _sync_coresso_catalogo(str(execution.id), "sme-apps")

        step = ETLStepLog.objects.filter(
            execution=execution, step_name=ETLStepLog.StepName.SYNC_CATALOGO
        ).first()
        assert step is not None
        assert step.records_error >= 1

    @patch("core.keycloak_client.upsert_kc_client_role", side_effect=RuntimeError("role error"))
    @patch("core.keycloak_client.upsert_kc_client", return_value=None)
    @patch("core.keycloak_client.get_admin_client", return_value=MagicMock())
    @patch("extract.tasks.extract_coresso_perfis", return_value=1)
    @patch("extract.tasks.extract_coresso_sistemas", return_value=0)
    def test_upsert_kc_client_role_exception_increments_error_count(
        self, mock_sistemas, mock_perfis, mock_admin, mock_client, mock_role, settings
    ):
        from staging.models import StagingPerfilCoreSSO

        settings.CORESSO_DB_SERVER = "mock-coresso"
        StagingPerfilCoreSSO.objects.create(
            coresso_gru_id="grp-test-9901",
            nome="Test Group",
            coresso_sis_id=9901,
            kc_role_name="test-role",
        )
        from core.models import ETLStepLog

        from core.tasks import _sync_coresso_catalogo

        execution = _make_execution()
        _sync_coresso_catalogo(str(execution.id), "sme-apps")

        step = ETLStepLog.objects.filter(
            execution=execution, step_name=ETLStepLog.StepName.SYNC_CATALOGO
        ).first()
        assert step is not None
        assert step.records_error >= 1


class TestRunEtlPipelineCancelled:
    def test_returns_immediately_when_cancelled(self):
        from core.tasks import run_etl_pipeline

        execution = _make_execution(status="cancelled")
        result = run_etl_pipeline(str(execution.id))
        assert result is None


class TestRunEtlPipelineRealmCreated:
    @patch("core.tasks.chain")
    @patch("core.keycloak_client.ensure_realm_exists", return_value=True)
    def test_logs_when_realm_is_created(self, mock_ensure, mock_chain):
        from core.tasks import run_etl_pipeline

        execution = _make_execution(
            source="se1426",
            skip_steps=["sync_catalogo"],
        )
        run_etl_pipeline(str(execution.id))
        mock_ensure.assert_called_once_with("sme-apps")
        assert mock_chain.called


class TestRunEtlPipelineSkipCatalogo:
    @patch("core.tasks.chain")
    @patch("core.keycloak_client.ensure_realm_exists", return_value=False)
    def test_logs_skip_when_sync_catalogo_in_skip_steps(self, mock_ensure, mock_chain):
        from core.tasks import run_etl_pipeline

        execution = _make_execution(
            source="se1426",
            skip_steps=["sync_catalogo"],
        )
        run_etl_pipeline(str(execution.id))
        assert mock_chain.called


class TestLoadUsuariosFromModel:
    @patch("core.tasks._upsert_single_usuario", return_value=(1, 0, 0))
    def test_respects_remaining_limit(self, mock_upsert):
        from staging.models import StagingUsuarioServidor

        from core.tasks import _load_usuarios_from_model

        execution = _make_execution()
        _make_servidor(execution.id)

        admin = MagicMock()
        loaded, skipped, errors, total, remaining = _load_usuarios_from_model(
            admin,
            StagingUsuarioServidor,
            str(execution.id),
            "sme-apps",
            execution,
            remaining=1,
        )
        assert total >= 1
        assert remaining == 0


class TestGenerateTokenMsPayloads:
    def test_yields_payload_for_ready_record(self):
        from staging.models import StagingUsuarioServidor

        from core.tasks import _generate_token_ms_payloads

        execution = _make_execution()
        _make_servidor(execution.id, status="ready")

        mock_build = MagicMock(return_value={"user": "data"})
        results = list(
            _generate_token_ms_payloads(
                [StagingUsuarioServidor],
                str(execution.id),
                mock_build,
            )
        )
        assert len(results) == 1
        mock_build.assert_called_once()

    def test_yields_route_payload_when_present(self):
        from staging.models import StagingUsuarioServidor

        from core.tasks import _generate_token_ms_payloads

        execution = _make_execution()
        srv = _make_servidor(execution.id, status="ready")
        srv.raw_data = {"route": {"token_ms": {"pre": "built"}}}
        srv.save(update_fields=["raw_data"])

        mock_build = MagicMock()
        results = list(
            _generate_token_ms_payloads(
                [StagingUsuarioServidor],
                str(execution.id),
                mock_build,
            )
        )
        assert results[0] == {"pre": "built"}
        mock_build.assert_not_called()


class TestLoadKeycloakMaxRecords:
    @patch("core.tasks._upsert_single_usuario", return_value=(1, 0, 0))
    @patch("core.keycloak_client.get_admin_client", return_value=MagicMock())
    def test_max_records_triggers_mode_teste_log(self, mock_admin, mock_upsert):
        from core.tasks import load_keycloak

        execution = _make_execution(
            load_keycloak=True,
            max_records=1,
        )
        _make_servidor(execution.id)
        load_keycloak(str(execution.id))

    @patch("core.tasks._upsert_single_usuario", return_value=(1, 0, 0))
    @patch("core.keycloak_client.get_admin_client", return_value=MagicMock())
    def test_remaining_zero_breaks_model_loop(self, mock_admin, mock_upsert):
        """Verifica que remaining<=0 interrompe o loop de models (user_types='all')."""
        from core.tasks import load_keycloak

        execution = _make_execution(
            load_keycloak=True,
            max_records=1,
        )
        _make_servidor(execution.id)
        # Com user_types="all" há 3 models; após processar 1 registro de StagingUsuarioServidor,
        # remaining=0 deve interromper antes de StagingUsuarioAluno/Terceiro
        load_keycloak(str(execution.id))


class TestLoadKeycloakSoftTimeLimit:
    @patch("core.tasks._load_usuarios_from_model")
    @patch("core.keycloak_client.get_admin_client", return_value=MagicMock())
    def test_soft_time_limit_saves_failed_step_and_retries(self, mock_admin, mock_load):
        from celery.exceptions import Retry, SoftTimeLimitExceeded

        from core.models import ETLStepLog
        from core.tasks import load_keycloak

        mock_load.side_effect = SoftTimeLimitExceeded()
        execution = _make_execution(load_keycloak=True)

        with patch.object(load_keycloak, "retry", side_effect=Retry()):
            with pytest.raises(Retry):
                load_keycloak(str(execution.id))

        step = ETLStepLog.objects.get(
            execution=execution, step_name=ETLStepLog.StepName.LOAD_KEYCLOAK
        )
        assert step.status == ETLStepLog.StepStatus.FAILED
        assert "soft_time_limit" in step.error_detail


class TestLoadKeycloakCancelled:
    @patch("core.tasks._load_usuarios_from_model")
    @patch("core.keycloak_client.get_admin_client", return_value=MagicMock())
    def test_cancelled_saves_failed_step_without_retry(self, mock_admin, mock_load):
        from core.models import ETLStepLog
        from core.tasks import ExecutionCancelledError, load_keycloak

        mock_load.side_effect = ExecutionCancelledError("cancelled during load")
        execution = _make_execution(load_keycloak=True)

        result = load_keycloak(str(execution.id))
        assert result is None

        step = ETLStepLog.objects.get(
            execution=execution, step_name=ETLStepLog.StepName.LOAD_KEYCLOAK
        )
        assert step.status == ETLStepLog.StepStatus.FAILED
        assert step.error_detail == "Cancelado manualmente"


class TestLoadTokenMsCancelled:
    @patch("core.token_ms_client.send_all")
    def test_cancelled_saves_failed_step_without_retry(self, mock_send):
        from core.models import ETLStepLog
        from core.tasks import ExecutionCancelledError, load_token_ms

        mock_send.side_effect = ExecutionCancelledError("cancelled during token ms")
        execution = _make_execution(load_token_ms=True)

        result = load_token_ms(str(execution.id))
        assert result is None

        step = ETLStepLog.objects.get(
            execution=execution, step_name=ETLStepLog.StepName.LOAD_TOKEN_MS
        )
        assert step.status == ETLStepLog.StepStatus.FAILED
        assert step.error_detail == "Cancelado manualmente"
