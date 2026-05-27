"""Testes de cobertura adicional para staging/tasks.py, core/health.py e core/service.py."""
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


def _make_execution(**kwargs):
    from core.models import ETLExecution

    return ETLExecution.objects.create(source="se1426", target_realm="sme-apps", **kwargs)


def _make_servidor(execution_id, rf="12345", cpf="52998224725", nome="JOAO SILVA",
                   status="transformed", source="se1426"):
    from staging.models import StagingUsuarioServidor

    return StagingUsuarioServidor.objects.create(
        execution_id=execution_id,
        rf=rf,
        cpf=cpf,
        nome=nome,
        status=status,
        source=source,
    )


# ---------------------------------------------------------------------------
# staging/tasks.py — SoftTimeLimitExceeded em transform_staging (linhas 204-209)
# ---------------------------------------------------------------------------
class TestTransformStagingSoftTimeLimit:
    def test_soft_time_limit_reraises_and_marks_step_failed(self):
        from celery.exceptions import SoftTimeLimitExceeded

        from core.models import ETLStepLog
        from staging.tasks import transform_staging

        execution = _make_execution()
        with patch("staging.tasks._transform_model", side_effect=SoftTimeLimitExceeded()):
            with pytest.raises(SoftTimeLimitExceeded):
                transform_staging.apply(args=[str(execution.id)]).get()

        step = ETLStepLog.objects.filter(execution=execution).first()
        assert step is not None
        assert step.status == ETLStepLog.StepStatus.FAILED


# ---------------------------------------------------------------------------
# staging/tasks.py — crossref_dedup com max_records_extract (linhas 408-412)
# ---------------------------------------------------------------------------
class TestCrossrefDedupMaxRecordsExtract:
    def test_limit_applied_when_max_records_extract_is_set(self):
        from core.models import ETLStepLog
        from staging.tasks import crossref_dedup

        execution = _make_execution(max_records_extract=1)
        _make_servidor(execution.id)

        crossref_dedup(str(execution.id))

        step = ETLStepLog.objects.filter(
            execution=execution, step_name="crossref_dedup"
        ).first()
        assert step is not None
        assert step.status == "success"


# ---------------------------------------------------------------------------
# core/health.py — except Exception genérico em _check_sme_integracao (linhas 217-221)
# ---------------------------------------------------------------------------
class TestCheckSmeIntegracoGenericException:
    def test_generic_exception_returns_unhealthy(self, settings):
        settings.SME_INTEGRACAO_BASE_URL = "http://sme-integracao"
        settings.SME_INTEGRACAO_TIMEOUT = 5

        with patch("core.health.httpx.Client") as mock_cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(side_effect=RuntimeError("unexpected error"))
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = mock_cm

            from core.health import _check_sme_integracao

            result = _check_sme_integracao()

        assert result["status"] == "unhealthy"
        assert "unexpected error" in result["detail"]


# ---------------------------------------------------------------------------
# core/service.py — _find_usuario filtra por execution_id (linha 129)
# ---------------------------------------------------------------------------
class TestKeycloakUpsertServiceFindUsuarioByExecutionId:
    def test_find_usuario_uses_execution_id_filter(self):
        from core.service import KeycloakUpsertService

        execution = _make_execution()
        _make_servidor(execution.id, cpf="52998224725", rf="12345", status="ready")

        svc = KeycloakUpsertService(cpf="52998224725", execution_id=str(execution.id))
        usuario = svc._find_usuario()

        assert usuario is not None
        assert str(usuario.execution_id) == str(execution.id)

    def test_find_usuario_returns_none_when_execution_id_mismatches(self):
        import uuid

        from core.service import KeycloakUpsertService

        execution = _make_execution()
        _make_servidor(execution.id, cpf="52998224725")

        # UUID diferente — não deve encontrar nada
        other_id = str(uuid.uuid4())
        svc = KeycloakUpsertService(cpf="52998224725", execution_id=other_id)
        usuario = svc._find_usuario()

        assert usuario is None


# ---------------------------------------------------------------------------
# core/service.py — _get_execution retorna None quando ocorre Exception (linhas 148-149)
# ---------------------------------------------------------------------------
class TestKeycloakUpsertServiceGetExecutionException:
    def test_returns_none_when_filter_raises_exception(self):
        from core.service import KeycloakUpsertService

        execution = _make_execution()
        usuario = _make_servidor(execution.id)

        with patch("core.service.ETLExecution.objects.filter", side_effect=Exception("db error")):
            result = KeycloakUpsertService._get_execution(usuario)

        assert result is None
