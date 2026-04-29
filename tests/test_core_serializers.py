import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _make_execution(**kwargs):
    from core.models import ETLExecution
    defaults = dict(
        trigger_type=ETLExecution.TriggerType.MANUAL,
        source="all",
        target_realm="sme-apps",
    )
    defaults.update(kwargs)
    return ETLExecution.objects.create(**defaults)


class TestETLExecutionCreateSerializer:
    def test_valid_default_values(self):
        from core.serializers import ETLExecutionCreateSerializer
        s = ETLExecutionCreateSerializer(data={})
        assert s.is_valid(), s.errors
        assert s.validated_data["source"] == "all"
        assert s.validated_data["target_realm"] == "sme-apps"

    def test_valid_custom_values(self):
        from core.serializers import ETLExecutionCreateSerializer
        s = ETLExecutionCreateSerializer(data={
            "source": "se1426",
            "target_realm": "sme-devops",
            "note": "test run",
        })
        assert s.is_valid(), s.errors
        assert s.validated_data["source"] == "se1426"
        assert s.validated_data["note"] == "test run"

    def test_invalid_source(self):
        from core.serializers import ETLExecutionCreateSerializer
        s = ETLExecutionCreateSerializer(data={"source": "invalid_source"})
        assert not s.is_valid()
        assert "source" in s.errors


class TestETLExecutionSerializer:
    def test_serializes_execution_fields(self):
        from core.serializers import ETLExecutionSerializer
        exec_ = _make_execution()
        s = ETLExecutionSerializer(exec_)
        data = s.data
        assert str(exec_.id) == data["id"]
        assert data["status"] == "pending"
        assert data["source"] == "all"
        assert data["target_realm"] == "sme-apps"
        assert "steps" in data

    def test_duration_seconds_none_when_not_started(self):
        from core.serializers import ETLExecutionSerializer
        exec_ = _make_execution()
        s = ETLExecutionSerializer(exec_)
        assert s.data["duration_seconds"] is None

    def test_duration_seconds_present_when_finished(self):
        from core.serializers import ETLExecutionSerializer
        exec_ = _make_execution()
        now = timezone.now()
        exec_.started_at = now - timedelta(seconds=60)
        exec_.finished_at = now
        exec_.status = "success"
        exec_.save()
        s = ETLExecutionSerializer(exec_)
        assert abs(s.data["duration_seconds"] - 60.0) < 1.0


class TestETLExecutionListSerializer:
    def test_list_serializer_fields(self):
        from core.serializers import ETLExecutionListSerializer
        exec_ = _make_execution()
        s = ETLExecutionListSerializer(exec_)
        data = s.data
        assert "id" in data
        assert "status" in data
        assert "source" in data
        # Should NOT include "steps" (list version is compact)
        assert "steps" not in data


class TestUpsertControlSerializer:
    def test_serializes_upsert_control(self):
        from core.models import UpsertControl
        from core.serializers import UpsertControlSerializer
        uc = UpsertControl.objects.create(
            entity_type=UpsertControl.EntityType.USER,
            source_id="cpf:52998224725",
            source_system="se1426",
            target_realm="sme-apps",
            content_hash="abc123",
        )
        s = UpsertControlSerializer(uc)
        data = s.data
        assert data["source_id"] == "cpf:52998224725"
        assert data["entity_type"] == "user"
