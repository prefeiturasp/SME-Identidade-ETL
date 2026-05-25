from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


def _make_execution(load_keycloak=False):
    from core.models import ETLExecution
    return ETLExecution.objects.create(
        source="all",
        load_keycloak=load_keycloak,
        target_realm="sme-apps",
    )


def _make_servidor(execution_id, cpf="52998224725", rf="12345"):
    from staging.models import StagingUsuarioServidor
    return StagingUsuarioServidor.objects.create(
        execution_id=execution_id,
        rf=rf,
        cpf=cpf,
        nome="Joao Silva",
        status="ready",
        source="se1426",
    )


_UPSERT_RESULT = {"action": "created", "kc_user_id": "kc-abc", "content_hash": "hash123"}
_UPSERT_SKIPPED = {"action": "skipped", "kc_user_id": "kc-abc", "content_hash": "hash123"}


class TestKeycloakUpsertServiceExecute:
    @patch("core.service.send_batch", return_value={"sent": 1})
    @patch("core.service.assign_user_client_roles", return_value={"assigned": 1})
    @patch("core.service.emit_retroalim")
    @patch("core.service.upsert_user_to_keycloak", return_value=_UPSERT_RESULT)
    @patch("core.service.get_admin_client", return_value=MagicMock())
    def test_execute_success(self, mock_admin, mock_upsert, mock_retro, mock_roles, mock_send):
        from core.service import KeycloakUpsertService

        execution = _make_execution()
        _make_servidor(execution.id, cpf="52998224725")

        svc = KeycloakUpsertService(cpf="52998224725")
        result = svc.execute()

        assert result["action"] == "created"
        assert result["kc_user_id"] == "kc-abc"
        assert "realm" in result
        assert "kc_payload" in result
        assert "token_ms_payload" in result

    def test_execute_raises_when_user_not_found(self):
        from core.service import KeycloakUpsertService

        svc = KeycloakUpsertService(cpf="00000000000")

        with pytest.raises(ValueError, match="Nenhum usuário"):
            svc.execute()

    @patch("core.service.send_batch", return_value={"sent": 1})
    @patch("core.service.assign_user_client_roles", return_value={"assigned": 1})
    @patch("core.service.emit_retroalim")
    @patch("core.service.upsert_user_to_keycloak", return_value=_UPSERT_RESULT)
    @patch("core.service.get_admin_client", return_value=MagicMock())
    def test_execute_uses_fallback_realm_when_no_execution(
        self, mock_admin, mock_upsert, mock_retro, mock_roles, mock_send
    ):
        from core.service import KeycloakUpsertService

        # Cria servidor sem vinculo a execução
        from staging.models import StagingUsuarioServidor
        import uuid
        exec_id = uuid.uuid4()
        StagingUsuarioServidor.objects.create(
            execution_id=exec_id,
            rf="99999",
            cpf="52998224725",
            nome="Teste",
            status="ready",
            source="se1426",
        )

        # execution_id que não existe no banco
        svc = KeycloakUpsertService(cpf="52998224725", realm="custom-realm")
        result = svc.execute()

        assert result["realm"] == "custom-realm"

    @patch("core.service.send_batch", return_value={"sent": 1})
    @patch("core.service.assign_user_client_roles", return_value={"assigned": 1})
    @patch("core.service.emit_retroalim")
    @patch("core.service.upsert_user_to_keycloak", return_value=_UPSERT_SKIPPED)
    @patch("core.service.get_admin_client", return_value=MagicMock())
    def test_execute_skipped_action_sets_status_skipped(
        self, mock_admin, mock_upsert, mock_retro, mock_roles, mock_send
    ):
        from core.service import KeycloakUpsertService
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        srv = _make_servidor(execution.id, cpf="52998224725")

        svc = KeycloakUpsertService(cpf="52998224725")
        result = svc.execute()

        srv.refresh_from_db()
        assert srv.status == "skipped"
        assert result["action"] == "skipped"


class TestKeycloakUpsertServiceAssignRoles:
    def test_assign_roles_skipped_when_disabled(self):
        from core.service import KeycloakUpsertService

        svc = KeycloakUpsertService(cpf="52998224725", assign_roles=False)
        result = svc._assign_roles(MagicMock(), MagicMock(), {"kc_user_id": "kc-001"})
        assert result == {"skipped": True}

    def test_assign_roles_skipped_when_no_kc_user_id(self):
        from core.service import KeycloakUpsertService

        svc = KeycloakUpsertService(cpf="52998224725")
        result = svc._assign_roles(MagicMock(), MagicMock(), {"kc_user_id": None})
        assert result == {"skipped": True}

    @patch("core.service.assign_user_client_roles", side_effect=RuntimeError("role error"))
    def test_assign_roles_returns_error_on_exception(self, mock_roles):
        from core.service import KeycloakUpsertService
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        srv = _make_servidor(execution.id)

        svc = KeycloakUpsertService(cpf="52998224725")
        result = svc._assign_roles(MagicMock(), srv, {"kc_user_id": "kc-001"})

        assert "error" in result
        assert "role error" in result["error"]


class TestKeycloakUpsertServiceEmitRetroalim:
    @patch("core.service.emit_retroalim", side_effect=RuntimeError("retroalim error"))
    def test_emit_retroalim_handles_exception_silently(self, mock_emit):
        from core.service import KeycloakUpsertService
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        srv = _make_servidor(execution.id)

        svc = KeycloakUpsertService(cpf="52998224725")
        # Não deve lançar exceção
        svc._emit_retroalim(srv, {"action": "created", "kc_user_id": "kc-001"}, "sme-apps", {})


class TestKeycloakUpsertServiceGetEventType:
    def test_created_returns_user_created(self):
        from core.service import KeycloakUpsertService
        assert KeycloakUpsertService._get_event_type("created") == "user_created"

    def test_updated_returns_user_updated(self):
        from core.service import KeycloakUpsertService
        assert KeycloakUpsertService._get_event_type("updated") == "user_updated"

    def test_unknown_returns_role_assigned(self):
        from core.service import KeycloakUpsertService
        assert KeycloakUpsertService._get_event_type("skipped") == "role_assigned"


class TestKeycloakUpsertServicePushTokenMs:
    def test_push_token_ms_skipped_when_disabled(self):
        from core.service import KeycloakUpsertService
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        srv = _make_servidor(execution.id)

        svc = KeycloakUpsertService(cpf="52998224725", push_token_ms=False)
        result = svc._push_token_ms(srv)
        assert result == {"skipped": True}

    @patch("core.service.send_batch", side_effect=RuntimeError("token error"))
    def test_push_token_ms_returns_error_on_exception(self, mock_send):
        from core.service import KeycloakUpsertService
        from staging.models import StagingUsuarioServidor

        execution = _make_execution()
        srv = _make_servidor(execution.id)

        svc = KeycloakUpsertService(cpf="52998224725")
        result = svc._push_token_ms(srv)

        assert "error" in result
        assert "token error" in result["error"]


class TestKeycloakUpsertServiceFindUsuario:
    def test_finds_servidor_by_cpf(self):
        from core.service import KeycloakUpsertService

        execution = _make_execution()
        srv = _make_servidor(execution.id, cpf="52998224725")

        svc = KeycloakUpsertService(cpf="52998224725")
        found = svc._find_usuario()
        assert found is not None
        assert str(found.id) == str(srv.id)

    def test_finds_servidor_by_rf(self):
        from core.service import KeycloakUpsertService

        execution = _make_execution()
        srv = _make_servidor(execution.id, cpf="52998224725", rf="54321")

        svc = KeycloakUpsertService(rf="54321")
        found = svc._find_usuario()
        assert found is not None
        assert str(found.id) == str(srv.id)

    def test_returns_none_when_not_found(self):
        from core.service import KeycloakUpsertService

        svc = KeycloakUpsertService(cpf="00000000000")
        assert svc._find_usuario() is None

    def test_clean_cpf_none_returns_none(self):
        from core.service import KeycloakUpsertService
        assert KeycloakUpsertService._clean_cpf(None) is None

    def test_clean_cpf_strips_punctuation(self):
        from core.service import KeycloakUpsertService
        assert KeycloakUpsertService._clean_cpf("529.982.247-25") == "52998224725"
