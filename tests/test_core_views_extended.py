from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()




class TestSistemasExtract:
    @patch("extract.tasks.extract_coresso_sistemas")
    def test_returns_total_extracted(self, mock_extract, client):
        mock_extract.return_value = 42
        response = client.post("/api/etl/sistemas/extract/")
        assert response.status_code == 200
        assert response.json()["total_extracted"] == 42

    @patch("extract.tasks.extract_coresso_sistemas")
    def test_returns_502_on_error(self, mock_extract, client):
        mock_extract.side_effect = Exception("Connection error")
        response = client.post("/api/etl/sistemas/extract/")
        assert response.status_code == 502


class TestSistemasLoadKeycloak:
    @patch("core.keycloak_client.get_admin_client")
    @patch("core.keycloak_client.upsert_kc_client")
    def test_loads_sistemas_to_keycloak(self, mock_upsert, mock_admin, client):
        from staging.models import StagingSistema
        StagingSistema.objects.create(
            nome="Sistema Teste",
            sigla="SIS_TEST",
            coresso_sis_id=1,
            situacao=1,
        )
        mock_admin.return_value = MagicMock()
        mock_upsert.return_value = {"action": "created", "client_id": "sys-test"}

        response = client.post("/api/etl/sistemas/load-keycloak/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["created"]) == 1

    @patch("core.keycloak_client.get_admin_client")
    def test_returns_empty_when_no_sistemas(self, mock_admin, client):
        mock_admin.return_value = MagicMock()
        response = client.post("/api/etl/sistemas/load-keycloak/")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    @patch("core.keycloak_client.get_admin_client")
    @patch("core.keycloak_client.upsert_kc_client")
    def test_handles_upsert_error_gracefully(self, mock_upsert, mock_admin, client):
        from staging.models import StagingSistema
        StagingSistema.objects.create(
            nome="Sistema Erro",
            sigla="SIS_ERR",
            coresso_sis_id=2,
            situacao=1,
        )
        mock_admin.return_value = MagicMock()
        mock_upsert.side_effect = Exception("KC error")

        response = client.post("/api/etl/sistemas/load-keycloak/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["errors"]) == 1


class TestSistemasList:
    def test_returns_empty_list_when_none(self, client):
        response = client.get("/api/etl/sistemas/")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_sistema_details(self, client):
        from staging.models import StagingSistema
        StagingSistema.objects.create(
            nome="Sistema Teste",
            sigla="SIS_TEST",
            coresso_sis_id=1,
        )
        response = client.get("/api/etl/sistemas/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["sigla"] == "SIS_TEST"




class TestPerfisExtract:
    @patch("extract.tasks.extract_coresso_perfis")
    def test_returns_total_extracted(self, mock_extract, client):
        mock_extract.return_value = 10
        response = client.post("/api/etl/perfis/extract/")
        assert response.status_code == 200
        assert response.json()["total_extracted"] == 10

    @patch("extract.tasks.extract_coresso_perfis")
    def test_returns_502_on_error(self, mock_extract, client):
        mock_extract.side_effect = Exception("DB error")
        response = client.post("/api/etl/perfis/extract/")
        assert response.status_code == 502


class TestPerfisLoadKeycloak:
    @patch("core.keycloak_client.get_admin_client")
    def test_returns_empty_when_no_perfis(self, mock_admin, client):
        mock_admin.return_value = MagicMock()
        response = client.post("/api/etl/perfis/load-keycloak/")
        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 0
        assert data["errors"] == 0


class TestPerfisList:
    def test_returns_empty_list_when_none(self, client):
        response = client.get("/api/etl/perfis/")
        assert response.status_code == 200
        assert response.json() == []

    def test_filters_by_coresso_sis_id(self, client):
        from staging.models import StagingSistema, StagingPerfilCoreSSO
        sistema = StagingSistema.objects.create(
            nome="Sistema Test", sigla="SIS", coresso_sis_id=5,
        )
        StagingPerfilCoreSSO.objects.create(
            nome="Perfil A",
            coresso_gru_id=1,
            coresso_sis_id=5,
            sistema=sistema,
        )
        response = client.get("/api/etl/perfis/?coresso_sis_id=5")
        assert response.status_code == 200
        assert len(response.json()) == 1




class TestRetroalimList:
    def test_returns_empty_list(self, client):
        response = client.get("/api/etl/retroalim/")
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_retroalim_events(self, client):
        from staging.models import RetroalimentacaoCoreSSO
        RetroalimentacaoCoreSSO.objects.create(
            tipo="user_created",
            rf="12345",
            cpf="52998224725",
            status="pending",
            payload={"key": "value"},
        )
        response = client.get("/api/etl/retroalim/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["tipo"] == "user_created"

    def test_filters_by_status(self, client):
        from staging.models import RetroalimentacaoCoreSSO
        RetroalimentacaoCoreSSO.objects.create(
            tipo="user_created", status="pending", payload={}
        )
        RetroalimentacaoCoreSSO.objects.create(
            tipo="user_updated", status="sent", payload={}
        )
        response = client.get("/api/etl/retroalim/?status=pending")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "pending"




class TestPerfisLoadKeycloakWithData:
    def test_loads_perfil_created(self, client, settings):
        settings.KEYCLOAK_REALM = "sme-apps"
        settings.KEYCLOAK_SERVER_URL = "http://kc"
        from staging.models import StagingSistema, StagingPerfilCoreSSO
        from unittest.mock import MagicMock, patch

        sistema = StagingSistema.objects.create(
            nome="SGP", sigla="sgp", coresso_sis_id=20,
            kc_client_uuid="kc-uuid", kc_client_id="sgp-prod",
        )
        perfil = StagingPerfilCoreSSO.objects.create(
            nome="Admin SGP", coresso_gru_id="GUID-100",
            sistema=sistema, coresso_sis_id=20,
        )

        mock_admin = MagicMock()
        mock_result = {"action": "created", "role_id": "role-uuid-100", "role_name": "admin-sgp"}

        with patch("core.keycloak_client.get_admin_client", return_value=mock_admin), \
             patch("core.keycloak_client.upsert_kc_client_role", return_value=mock_result):
            response = client.post("/api/etl/perfis/load-keycloak/", {}, content_type="application/json")

        assert response.status_code == 200
        assert response.data["created"] == 1

    def test_loads_perfil_error_handled(self, client, settings):
        settings.KEYCLOAK_REALM = "sme-apps"
        settings.KEYCLOAK_SERVER_URL = "http://kc"
        from staging.models import StagingSistema, StagingPerfilCoreSSO
        from unittest.mock import MagicMock, patch

        sistema = StagingSistema.objects.create(
            nome="SGP2", sigla="sgp2", coresso_sis_id=21,
            kc_client_uuid="kc-uuid-2", kc_client_id="sgp2-prod",
        )
        StagingPerfilCoreSSO.objects.create(
            nome="Gestor SGP2", coresso_gru_id="GUID-200",
            sistema=sistema, coresso_sis_id=21,
        )

        mock_admin = MagicMock()
        with patch("core.keycloak_client.get_admin_client", return_value=mock_admin), \
             patch("core.keycloak_client.upsert_kc_client_role", side_effect=RuntimeError("KC fail")):
            response = client.post("/api/etl/perfis/load-keycloak/", {}, content_type="application/json")

        assert response.status_code == 200
        assert response.data["errors"] == 1




class TestSistemasLoadKeycloakWithData:
    def test_loads_sistema_via_loop(self, client, settings):
        settings.KEYCLOAK_REALM = "sme-apps"
        settings.KEYCLOAK_SERVER_URL = "http://kc"
        from staging.models import StagingSistema
        from unittest.mock import MagicMock, patch

        StagingSistema.objects.create(
            nome="Sistema Teste Loop", sigla="stl",
            coresso_sis_id=30, situacao=1,
        )
        mock_admin = MagicMock()
        mock_result = {"action": "created", "client_id": "stl-prod", "kc_uuid": "uuid-stl"}

        with patch("core.keycloak_client.get_admin_client", return_value=mock_admin), \
             patch("core.keycloak_client.upsert_kc_client", return_value=mock_result):
            response = client.post("/api/etl/sistemas/load-keycloak/", {}, content_type="application/json")

        assert response.status_code == 200
        assert len(response.data["created"]) == 1




class TestAssignUserClientRoles:
    def test_returns_empty_when_no_login(self):
        from core.keycloak_client import assign_user_client_roles
        admin = MagicMock()
        result = assign_user_client_roles(admin, "kc-user-001", "")
        assert result["assigned"] == 0

    def test_returns_empty_when_no_grupos(self):
        from unittest.mock import patch
        admin = MagicMock()
        with patch("extract.tasks.fetch_coresso_groups_for_login", return_value=[]):
            from core.keycloak_client import assign_user_client_roles
            result = assign_user_client_roles(admin, "kc-user-001", "12345")
        assert result["assigned"] == 0

    def test_assigns_roles_for_resolved_perfis(self):
        from unittest.mock import patch
        from staging.models import StagingSistema, StagingPerfilCoreSSO

        sistema = StagingSistema.objects.create(
            nome="SGP Roles", sigla="sgpr", coresso_sis_id=40,
            kc_client_uuid="kc-client-uuid-40",
        )
        perfil = StagingPerfilCoreSSO.objects.create(
            nome="Admin SGPR", coresso_gru_id="GUID-400",
            sistema=sistema, coresso_sis_id=40,
            kc_role_id="role-id-400",
        )

        grupos = [{"gru_id": "GUID-400", "gru_nome": "Admin SGPR", "sis_id": 40, "sis_nome": "SGP Roles"}]

        admin = MagicMock()
        admin.assign_client_role = MagicMock()

        with patch("extract.tasks.fetch_coresso_groups_for_login", return_value=grupos):
            from core.keycloak_client import assign_user_client_roles
            result = assign_user_client_roles(admin, "kc-user-400", "12345")

        assert result["assigned"] == 1
        assert result["groups_in_coresso"] == 1

    def test_handles_role_assignment_error(self):
        from unittest.mock import patch
        from staging.models import StagingSistema, StagingPerfilCoreSSO

        sistema = StagingSistema.objects.create(
            nome="SGP Err", sigla="sgpe", coresso_sis_id=41,
            kc_client_uuid="kc-client-uuid-41",
        )
        StagingPerfilCoreSSO.objects.create(
            nome="Admin SGPE", coresso_gru_id="GUID-410",
            sistema=sistema, coresso_sis_id=41,
            kc_role_id="role-id-410",
        )

        grupos = [{"gru_id": "GUID-410", "gru_nome": "Admin SGPE", "sis_id": 41, "sis_nome": "SGP Err"}]

        admin = MagicMock()
        admin.assign_client_role.side_effect = RuntimeError("KC assign failed")

        with patch("extract.tasks.fetch_coresso_groups_for_login", return_value=grupos):
            from core.keycloak_client import assign_user_client_roles
            result = assign_user_client_roles(admin, "kc-user-410", "12345")

        assert result["assigned"] == 0
        assert any("error" in d for d in result["details"])

