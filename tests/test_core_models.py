import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db




class TestETLExecutionModel:
    def _make_execution(self, **kwargs):
        from core.models import ETLExecution
        defaults = dict(
            trigger_type=ETLExecution.TriggerType.MANUAL,
            source="all",
            target_realm="sme-apps",
        )
        defaults.update(kwargs)
        return ETLExecution.objects.create(**defaults)

    def test_create_execution_default_status(self):
        from core.models import ETLExecution
        exec_ = self._make_execution()
        assert exec_.status == ETLExecution.Status.PENDING

    def test_str_representation(self):
        from core.models import ETLExecution
        exec_ = self._make_execution()
        s = str(exec_)
        assert "pending" in s
        assert "all" in s

    def test_mark_running(self):
        from core.models import ETLExecution
        exec_ = self._make_execution()
        exec_.mark_running()
        exec_.refresh_from_db()
        assert exec_.status == ETLExecution.Status.RUNNING
        assert exec_.started_at is not None

    def test_mark_finished_success(self):
        from core.models import ETLExecution
        exec_ = self._make_execution()
        exec_.mark_running()
        exec_.mark_finished("success")
        exec_.refresh_from_db()
        assert exec_.status == ETLExecution.Status.SUCCESS
        assert exec_.finished_at is not None

    def test_mark_finished_failed(self):
        from core.models import ETLExecution
        exec_ = self._make_execution()
        exec_.mark_finished("failed")
        exec_.refresh_from_db()
        assert exec_.status == ETLExecution.Status.FAILED

    def test_duration_seconds_none_when_not_finished(self):
        exec_ = self._make_execution()
        assert exec_.duration_seconds is None

    def test_duration_seconds_calculated(self):
        exec_ = self._make_execution()
        now = timezone.now()
        exec_.started_at = now - timedelta(seconds=30)
        exec_.finished_at = now
        exec_.save()
        assert abs(exec_.duration_seconds - 30.0) < 1.0

    def test_uuid_primary_key(self):
        exec_ = self._make_execution()
        assert isinstance(exec_.id, uuid.UUID)

    def test_ordering_by_created_at_desc(self):
        from core.models import ETLExecution
        e1 = self._make_execution()
        e2 = self._make_execution()
        executions = list(ETLExecution.objects.all())
        assert executions[0].id == e2.id  # criada por ultimo aparece primeiro

    def test_trigger_types(self):
        from core.models import ETLExecution
        for tt in (ETLExecution.TriggerType.SCHEDULED,
                   ETLExecution.TriggerType.MANUAL,
                   ETLExecution.TriggerType.NIFI):
            e = self._make_execution(trigger_type=tt)
            assert e.trigger_type == tt




class TestETLStepLogModel:
    def _make_execution(self):
        from core.models import ETLExecution
        return ETLExecution.objects.create(
            trigger_type=ETLExecution.TriggerType.MANUAL,
            source="all",
        )

    def _make_step(self, execution, step_name=None, step_order=1):
        from core.models import ETLStepLog
        step_name = step_name or ETLStepLog.StepName.STAGING
        return ETLStepLog.objects.create(
            execution=execution,
            step_name=step_name,
            step_order=step_order,
        )

    def test_create_step_default_running(self):
        from core.models import ETLStepLog
        execution = self._make_execution()
        step = self._make_step(execution)
        assert step.status == ETLStepLog.StepStatus.RUNNING

    def test_str_representation(self):
        execution = self._make_execution()
        step = self._make_step(execution)
        s = str(step)
        assert "staging" in s
        assert "running" in s

    def test_step_uuid_primary_key(self):
        execution = self._make_execution()
        step = self._make_step(execution)
        assert isinstance(step.id, uuid.UUID)

    def test_step_fk_to_execution(self):
        execution = self._make_execution()
        step = self._make_step(execution)
        assert step.execution_id == execution.id

    def test_cascade_delete(self):
        from core.models import ETLStepLog
        execution = self._make_execution()
        self._make_step(execution)
        execution_id = execution.id
        execution.delete()
        assert ETLStepLog.objects.filter(execution_id=execution_id).count() == 0

    def test_unique_together_execution_step_name(self):
        from django.db import IntegrityError
        from core.models import ETLStepLog
        execution = self._make_execution()
        self._make_step(execution, ETLStepLog.StepName.STAGING, 1)
        with pytest.raises(IntegrityError):
            self._make_step(execution, ETLStepLog.StepName.STAGING, 1)




class TestUpsertControlModel:
    def _make_upsert(self, **kwargs):
        from core.models import UpsertControl
        defaults = dict(
            entity_type=UpsertControl.EntityType.USER,
            source_id="cpf:12345678900",
            source_system="se1426",
            target_realm="sme-apps",
            content_hash="abc123",
        )
        defaults.update(kwargs)
        return UpsertControl.objects.create(**defaults)

    def test_create_upsert_control(self):
        from core.models import UpsertControl
        uc = self._make_upsert()
        assert uc.is_active is True
        assert uc.entity_type == UpsertControl.EntityType.USER

    def test_str_representation(self):
        uc = self._make_upsert()
        s = str(uc)
        assert "user" in s.lower() or "cpf" in s.lower()

    def test_primary_key_auto_increment(self):
        uc = self._make_upsert()
        assert isinstance(uc.id, int)
        assert uc.id > 0
