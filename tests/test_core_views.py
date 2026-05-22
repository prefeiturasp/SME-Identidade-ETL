import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.test import APIRequestFactory

pytestmark = pytest.mark.django_db




@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def execution():
    from core.models import ETLExecution
    return ETLExecution.objects.create(
        trigger_type=ETLExecution.TriggerType.MANUAL,
        source="all",
        target_realm="sme-apps",
    )


@pytest.fixture
def running_execution():
    from core.models import ETLExecution
    e = ETLExecution.objects.create(
        trigger_type=ETLExecution.TriggerType.MANUAL,
        source="se1426",
        target_realm="sme-apps",
    )
    e.mark_running()
    return e




class TestETLExecutionList:
    def test_list_empty(self, api_client):
        url = reverse("etl-execution-list")
        resp = api_client.get(url)
        assert resp.status_code == 200
        assert resp.data["count"] == 0

    def test_list_returns_executions(self, api_client, execution):
        url = reverse("etl-execution-list")
        resp = api_client.get(url)
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_list_pagination(self, api_client):
        from core.models import ETLExecution
        for _ in range(5):
            ETLExecution.objects.create(source="all")
        url = reverse("etl-execution-list")
        resp = api_client.get(url)
        assert resp.status_code == 200
        assert resp.data["count"] == 5




class TestETLExecutionRetrieve:
    def test_retrieve_existing(self, api_client, execution):
        url = reverse("etl-execution-detail", kwargs={"id": execution.id})
        resp = api_client.get(url)
        assert resp.status_code == 200
        assert resp.data["id"] == str(execution.id)
        assert resp.data["status"] == "pending"

    def test_retrieve_not_found(self, api_client):
        url = reverse("etl-execution-detail", kwargs={"id": uuid.uuid4()})
        resp = api_client.get(url)
        assert resp.status_code == 404




class TestETLExecutionCreate:
    @patch("core.tasks.run_etl_pipeline.delay")
    def test_create_triggers_pipeline(self, mock_delay, api_client):
        mock_task = MagicMock()
        mock_task.id = "celery-task-id-123"
        mock_delay.return_value = mock_task

        url = reverse("etl-execution-list")
        resp = api_client.post(url, {"source": "se1426", "target_realm": "sme-apps"}, format="json")
        assert resp.status_code == 201
        assert resp.data["source"] == "se1426"
        mock_delay.assert_called_once()

    @patch("core.tasks.run_etl_pipeline.delay")
    def test_create_default_source_all(self, mock_delay, api_client):
        mock_task = MagicMock()
        mock_task.id = "celery-task-id-456"
        mock_delay.return_value = mock_task

        url = reverse("etl-execution-list")
        resp = api_client.post(url, {}, format="json")
        assert resp.status_code == 201
        assert resp.data["source"] == "all"

    def test_create_invalid_source(self, api_client):
        url = reverse("etl-execution-list")
        resp = api_client.post(url, {"source": "invalid_xyz"}, format="json")
        assert resp.status_code == 400




class TestETLExecutionCancel:
    def test_cancel_pending_execution(self, api_client, execution):
        url = reverse("etl-execution-cancel", kwargs={"id": execution.id})
        resp = api_client.post(url)
        assert resp.status_code == 200
        assert resp.data["status"] == "cancelled"

    def test_cancel_running_execution(self, api_client, running_execution):
        url = reverse("etl-execution-cancel", kwargs={"id": running_execution.id})
        resp = api_client.post(url)
        assert resp.status_code == 200
        assert resp.data["status"] == "cancelled"

    def test_cancel_already_finished_returns_400(self, api_client):
        from core.models import ETLExecution
        e = ETLExecution.objects.create(source="all", status=ETLExecution.Status.SUCCESS)
        url = reverse("etl-execution-cancel", kwargs={"id": e.id})
        resp = api_client.post(url)
        assert resp.status_code == 400

    @patch("etl_ms.celery.app.control.revoke")
    def test_cancel_with_celery_task_revokes(self, mock_revoke, api_client, running_execution):
        running_execution.celery_task_id = "celery-abc-123"
        running_execution.save(update_fields=["celery_task_id"])
        url = reverse("etl-execution-cancel", kwargs={"id": running_execution.id})
        resp = api_client.post(url)
        assert resp.status_code == 200




class TestEtlStats:
    def test_stats_empty(self, api_client):
        url = reverse("etl-stats")
        resp = api_client.get(url)
        assert resp.status_code == 200
        assert "executions" in resp.data
        assert "upsert_control" in resp.data
        assert "period" in resp.data

    def test_stats_with_executions(self, api_client, execution):
        from core.models import ETLExecution
        ETLExecution.objects.create(source="se1426", status=ETLExecution.Status.SUCCESS,
                                    total_loaded=10)
        url = reverse("etl-stats")
        resp = api_client.get(url)
        assert resp.status_code == 200
        data = resp.data
        assert data["executions"]["total"] >= 1




class TestUpsertControlViewSet:
    def _make_upsert(self, **kwargs):
        from core.models import UpsertControl
        defaults = {
            "entity_type": UpsertControl.EntityType.USER,
            "source_id": "cpf:52998224725",
            "source_system": "se1426",
            "target_realm": "sme-apps",
            "content_hash": "abc123",
        }
        defaults.update(kwargs)
        return UpsertControl.objects.create(**defaults)

    def test_list_upsert_control(self, api_client):
        self._make_upsert()
        url = reverse("upsert-control-list")
        resp = api_client.get(url)
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_retrieve_upsert_control(self, api_client):
        uc = self._make_upsert()
        url = reverse("upsert-control-detail", kwargs={"pk": uc.pk})
        resp = api_client.get(url)
        assert resp.status_code == 200
        assert resp.data["source_id"] == "cpf:52998224725"

    def test_filter_by_entity_type(self, api_client):
        self._make_upsert(entity_type="user")
        self._make_upsert(entity_type="group", source_id="grp:sme")
        url = reverse("upsert-control-list") + "?entity_type=user"
        resp = api_client.get(url)
        assert resp.status_code == 200
        assert resp.data["count"] == 1




class TestKcUpsert:
    def test_no_cpf_or_rf_returns_400(self, api_client):
        url = reverse("etl-test-kc-upsert")
        resp = api_client.post(url, {}, format="json")
        assert resp.status_code == 400

    def test_not_found_cpf_returns_404(self, api_client):
        url = reverse("etl-test-kc-upsert")
        resp = api_client.post(url, {"cpf": "52998224725"}, format="json")
        assert resp.status_code == 404

    @patch("core.service.get_admin_client")
    @patch("core.service.upsert_user_to_keycloak")
    def test_success_with_cpf(self, mock_upsert, mock_admin, api_client):
        from staging.models import StagingUsuarioServidor
        import uuid

        exec_id = uuid.uuid4()
        StagingUsuarioServidor.objects.create(
            cpf="52998224725",
            nome="JOAO SILVA",
            source="se1426",
            execution_id=exec_id,
        )
        mock_admin.return_value = MagicMock()
        mock_upsert.return_value = {
            "action": "created",
            "kc_user_id": "kc-uuid-abc",
            "content_hash": "abc123hash",
        }

        url = reverse("etl-test-kc-upsert")
        resp = api_client.post(url, {
            "cpf": "52998224725",
            "assign_roles": False,
            "push_token_ms": False,
        }, format="json")
        assert resp.status_code == 200

    @patch("core.service.get_admin_client")
    @patch("core.service.upsert_user_to_keycloak")
    def test_keycloak_error_returns_502(self, mock_upsert, mock_admin, api_client):
        from staging.models import StagingUsuarioServidor
        import uuid

        exec_id = uuid.uuid4()
        StagingUsuarioServidor.objects.create(
            cpf="11144477735",
            nome="MARIA JOSE",
            source="se1426",
            execution_id=exec_id,
        )
        mock_admin.return_value = MagicMock()
        mock_upsert.side_effect = Exception("KC connection failed")

        url = reverse("etl-test-kc-upsert")
        resp = api_client.post(url, {
            "cpf": "11144477735",
            "assign_roles": False,
            "push_token_ms": False,
        }, format="json")
        assert resp.status_code == 502


class TestSistemasLoadKeycloak:
    @patch("core.keycloak_client.get_admin_client")
    @patch("core.keycloak_client.upsert_kc_client")
    def test_filter_by_sigla(
        self,
        mock_upsert,
        mock_admin,
    ):
        from core.views import sistemas_load_keycloak
        from staging.models import StagingSistema

        factory = APIRequestFactory()

        mock_admin.return_value = MagicMock()

        mock_upsert.return_value = {
            "action": "created",
        }

        StagingSistema.objects.create(
            coresso_sis_id=1,
            nome="Sistema A",
            sigla="SIGA",
            situacao=1,
        )

        StagingSistema.objects.create(
            coresso_sis_id=2,
            nome="Sistema B",
            sigla="OUTRO",
            situacao=1,
        )

        request = factory.post(
            "/fake-url/",
            {"sigla": "SIGA"},
            format="json",
        )

        resp = sistemas_load_keycloak(request)

        assert resp.status_code == 200

        assert mock_upsert.call_count == 1


class TestPerfisLoadKeycloak:
    @patch("core.keycloak_client.get_admin_client")
    @patch("core.keycloak_client.upsert_kc_client_role")
    def test_filter_by_coresso_sis_id(
        self,
        mock_upsert,
        mock_admin,
    ):
        from core.views import perfis_load_keycloak
        from staging.models import (
            StagingPerfilCoreSSO,
            StagingSistema,
        )

        factory = APIRequestFactory()

        mock_admin.return_value = MagicMock()

        mock_upsert.return_value = {
            "action": "created",
        }

        sistema1 = StagingSistema.objects.create(
            coresso_sis_id=1,
            nome="Sistema A",
            sigla="AAA",
            situacao=1,
        )

        sistema2 = StagingSistema.objects.create(
            coresso_sis_id=2,
            nome="Sistema B",
            sigla="BBB",
            situacao=1,
        )

        StagingPerfilCoreSSO.objects.create(
            coresso_gru_id=100,
            nome="Perfil A",
            sistema=sistema1,
            coresso_sis_id=1,
        )

        StagingPerfilCoreSSO.objects.create(
            coresso_gru_id=200,
            nome="Perfil B",
            sistema=sistema2,
            coresso_sis_id=2,
        )

        request = factory.post(
            "/fake-url/",
            {"coresso_sis_id": 1},
            format="json",
        )

        resp = perfis_load_keycloak(request)

        assert resp.status_code == 200

        assert mock_upsert.call_count == 1

    @patch("core.keycloak_client.get_admin_client")
    @patch("core.keycloak_client.upsert_kc_client_role")
    def test_refresh_admin_client_exception_is_ignored(
        self,
        mock_upsert,
        mock_admin,
    ):
        from core.views import perfis_load_keycloak
        from staging.models import (
            StagingPerfilCoreSSO,
            StagingSistema,
        )

        factory = APIRequestFactory()

        admin_instance = MagicMock()

        mock_admin.side_effect = [
            admin_instance,
            Exception("refresh failed"),
        ]

        mock_upsert.return_value = {
            "action": "created",
        }

        sistema = StagingSistema.objects.create(
            coresso_sis_id=1,
            nome="Sistema A",
            sigla="AAA",
            situacao=1,
        )

        for i in range(51):
            StagingPerfilCoreSSO.objects.create(
                coresso_gru_id=1000 + i,
                nome=f"Perfil {i}",
                sistema=sistema,
                coresso_sis_id=1,
            )

        request = factory.post(
            "/fake-url/",
            {},
            format="json",
        )

        resp = perfis_load_keycloak(request)

        assert resp.status_code == 200

        assert resp.data["created"] == 51

    @patch("core.keycloak_client.get_admin_client")
    @patch("core.keycloak_client.upsert_kc_client_role")
    def test_updated_and_skipped_actions(
        self,
        mock_upsert,
        mock_admin,
    ):
        from core.views import perfis_load_keycloak
        from staging.models import (
            StagingPerfilCoreSSO,
            StagingSistema,
        )

        factory = APIRequestFactory()

        mock_admin.return_value = MagicMock()

        mock_upsert.side_effect = [
            {"action": "updated"},
            {"action": "skipped"},
        ]

        sistema = StagingSistema.objects.create(
            coresso_sis_id=1,
            nome="Sistema A",
            sigla="AAA",
            situacao=1,
        )

        StagingPerfilCoreSSO.objects.create(
            coresso_gru_id=10,
            nome="Perfil Updated",
            sistema=sistema,
            coresso_sis_id=1,
        )

        StagingPerfilCoreSSO.objects.create(
            coresso_gru_id=20,
            nome="Perfil Skipped",
            sistema=sistema,
            coresso_sis_id=1,
        )

        request = factory.post(
            "/fake-url/",
            {},
            format="json",
        )

        resp = perfis_load_keycloak(request)

        assert resp.status_code == 200
        assert resp.data["updated"] == 1
        assert resp.data["skipped"] == 1


class TestETLExecutionViewSetSerializer:
    def test_get_serializer_class_for_create_action(self):
        from core.serializers import ETLExecutionCreateSerializer
        from core.views import ETLExecutionViewSet

        viewset = ETLExecutionViewSet()
        viewset.action = "create"

        serializer = viewset.get_serializer_class()

        assert serializer == ETLExecutionCreateSerializer