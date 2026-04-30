from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.django_db


def _make_usuario(rf="12345", cpf="52998224725", nome="Joao Silva",
                  source="se1426", email="j@sme.sp"):
    from staging.models import StagingUsuarioServidor
    from core.models import ETLExecution
    execution = ETLExecution.objects.create(source=source)
    return StagingUsuarioServidor.objects.create(
        execution_id=execution.id,
        rf=rf,
        cpf=cpf,
        nome=nome,
        email=email,
        status="ready",
        source=source,
    )


def _make_admin_mock(kc_user_id="kc-user-123"):
    admin = MagicMock()
    admin.create_user.return_value = kc_user_id
    admin.update_user.return_value = None
    admin.get_users.return_value = []
    admin.get_realm_role.return_value = {"id": "role-id", "name": "role"}
    admin.assign_realm_roles.return_value = None
    admin.get_group_by_path.return_value = {"id": "group-id"}
    admin.group_user_add.return_value = None
    return admin




class TestUpsertUserToKeycloak:
    def test_creates_new_user(self):
        from core.keycloak_client import upsert_user_to_keycloak
        usuario = _make_usuario()
        admin = _make_admin_mock(kc_user_id="kc-new-user")
        result = upsert_user_to_keycloak(admin, usuario, realm="sme-apps")
        assert result["action"] == "created"
        assert result["kc_user_id"] == "kc-new-user"
        assert "content_hash" in result

    def test_updates_existing_user(self):
        from core.keycloak_client import upsert_user_to_keycloak
        from core.models import UpsertControl
        usuario = _make_usuario()
        admin = _make_admin_mock(kc_user_id="kc-existing")

        UpsertControl.objects.create(
            entity_type="user",
            source_system=usuario.source,
            source_id=usuario.cpf,
            target_realm="sme-apps",
            target_id="kc-existing",
            content_hash="old-hash",
        )
        result = upsert_user_to_keycloak(admin, usuario, realm="sme-apps")
        assert result["action"] == "updated"
        assert result["kc_user_id"] == "kc-existing"

    def test_skips_when_hash_unchanged(self):
        from core.keycloak_client import upsert_user_to_keycloak, compute_content_hash, build_kc_payload
        from core.models import UpsertControl
        usuario = _make_usuario()
        payload = build_kc_payload(usuario)
        current_hash = compute_content_hash(payload)

        UpsertControl.objects.create(
            entity_type="user",
            source_system=usuario.source,
            source_id=usuario.cpf,
            target_realm="sme-apps",
            target_id="kc-same",
            content_hash=current_hash,
        )
        admin = _make_admin_mock(kc_user_id="kc-same")
        result = upsert_user_to_keycloak(admin, usuario, realm="sme-apps")
        assert result["action"] == "skipped"

    def test_uses_rf_when_no_cpf(self):
        from core.keycloak_client import upsert_user_to_keycloak
        from staging.models import StagingUsuarioServidor
        from core.models import ETLExecution
        execution = ETLExecution.objects.create(source="se1426")
        usuario = StagingUsuarioServidor.objects.create(
            execution_id=execution.id,
            rf="12345",
            cpf=None,
            nome="Sem CPF",
            status="ready",
            source="se1426",
        )
        admin = _make_admin_mock(kc_user_id="kc-rf-only")
        result = upsert_user_to_keycloak(admin, usuario, realm="sme-apps")
        assert result["action"] in ("created", "updated")




class TestFindExistingKcUser:
    def test_returns_none_when_no_users_found(self):
        from core.keycloak_client import _find_existing_kc_user
        usuario = SimpleNamespace(rf="12345", cpf="52998224725", email="j@sme.sp")
        admin = MagicMock()
        admin.get_users.return_value = []
        result = _find_existing_kc_user(admin, usuario, {"username": "52998224725"})
        assert result is None

    def test_returns_existing_id_when_username_matches(self):
        from core.keycloak_client import _find_existing_kc_user
        usuario = SimpleNamespace(rf="12345", cpf="52998224725", email="j@sme.sp")
        admin = MagicMock()
        admin.get_users.return_value = [{"id": "existing-id", "username": "52998224725"}]
        result = _find_existing_kc_user(admin, usuario, {"username": "52998224725", "email": "j@sme.sp"})
        assert result == "existing-id"

    def test_deletes_user_when_username_changed(self):
        from core.keycloak_client import _find_existing_kc_user
        usuario = SimpleNamespace(rf="12345", cpf="52998224725", email="j@sme.sp")
        admin = MagicMock()

        admin.get_users.return_value = [{"id": "old-id", "username": "12345"}]
        result = _find_existing_kc_user(admin, usuario, {"username": "52998224725", "email": "j@sme.sp"})

        admin.delete_user.assert_called_once_with("old-id")
        assert result is None




class TestAssignRolesAndGroups:
    def test_assigns_realm_roles(self):
        from core.keycloak_client import _assign_roles_and_groups
        admin = MagicMock()
        admin.get_realm_role.return_value = {"id": "role-id", "name": "role-servidores"}
        _assign_roles_and_groups(admin, "kc-user-1", ["role-servidores"], [])
        admin.assign_realm_roles.assert_called_once()

    def test_ignores_missing_roles_silently(self):
        from core.keycloak_client import _assign_roles_and_groups
        admin = MagicMock()
        admin.get_realm_role.side_effect = Exception("role not found")

        _assign_roles_and_groups(admin, "kc-user-1", ["nonexistent-role"], [])

    def test_assigns_groups(self):
        from core.keycloak_client import _assign_roles_and_groups
        admin = MagicMock()
        admin.get_group_by_path.return_value = {"id": "group-id"}
        _assign_roles_and_groups(admin, "kc-user-1", [], ["/smeprefsp/servidores"])
        admin.group_user_add.assert_called_once()

    def test_ignores_missing_groups_silently(self):
        from core.keycloak_client import _assign_roles_and_groups
        admin = MagicMock()
        admin.get_group_by_path.return_value = None

        _assign_roles_and_groups(admin, "kc-user-1", [], ["/nonexistent"])




class TestEmitRetroalim:
    def test_creates_retroalim_record(self):
        from core.keycloak_client import emit_retroalim
        from staging.models import RetroalimentacaoCoreSSO
        usuario = _make_usuario()
        emit_retroalim(
            tipo="user_created",
            usuario=usuario,
            payload={"kc_user_id": "kc-123", "realm": "sme-apps"},
        )
        assert RetroalimentacaoCoreSSO.objects.filter(
            tipo="user_created", rf=usuario.rf
        ).exists()

    def test_emit_without_payload(self):
        from core.keycloak_client import emit_retroalim
        from staging.models import RetroalimentacaoCoreSSO
        usuario = _make_usuario(rf="55555")
        emit_retroalim(tipo="user_updated", usuario=usuario)
        assert RetroalimentacaoCoreSSO.objects.filter(
            tipo="user_updated", rf="55555"
        ).exists()




class TestGetAdminClient:
    @patch("keycloak.KeycloakAdmin")
    def test_returns_admin_client(self, mock_kc_cls, settings):
        settings.KEYCLOAK_SERVER_URL = "http://keycloak:8080"
        settings.KEYCLOAK_REALM = "sme-apps"
        settings.KEYCLOAK_ADMIN_USER = "admin"
        settings.KEYCLOAK_ADMIN_PASSWORD = "admin"
        settings.KEYCLOAK_ADMIN_CLIENT_ID = "admin-cli"

        mock_admin = MagicMock()
        mock_kc_cls.return_value = mock_admin

        from core.keycloak_client import get_admin_client
        admin = get_admin_client(realm="sme-apps")
        assert admin is mock_admin
        mock_kc_cls.assert_called_once()




class TestUpsertKcClient:
    def _make_sistema(self, coresso_sis_id=10):
        from staging.models import StagingSistema
        return StagingSistema.objects.create(
            nome="Sistema SGP",
            sigla="sgp",
            coresso_sis_id=coresso_sis_id,
            url_callback="http://sgp.sme.sp.gov.br/callback",
            situacao=1,
        )

    def _make_admin(self, existing_uuid=None):
        admin = MagicMock()
        admin.get_client_id.return_value = existing_uuid
        admin.create_client.return_value = "new-kc-uuid"
        admin.connection = MagicMock()
        admin.connection.raw_post.return_value = MagicMock()
        return admin

    def test_creates_new_client(self, settings):
        settings.KEYCLOAK_REALM = "sme-apps"
        settings.KEYCLOAK_SERVER_URL = "http://kc"
        settings.KEYCLOAK_VERIFY_SSL = False
        settings.KEYCLOAK_CLIENT_SUFFIX = "prod"

        sistema = self._make_sistema()
        admin = self._make_admin(existing_uuid=None)
        admin.get_client_id.return_value = None
        admin.create_client.return_value = "new-uuid-001"


        import json
        from unittest.mock import patch as mock_patch

        with mock_patch("keycloak.keycloak_admin.raise_error_from_response",
                        return_value={"registrationAccessToken": "reg-tok-001"}), \
             mock_patch("core.keycloak_client._with_backoff", wraps=lambda fn, *a, **kw: fn(*a, **kw)):
            from core.keycloak_client import upsert_kc_client
            result = upsert_kc_client(admin, sistema, realm="sme-apps")

        assert result["action"] == "created"
        assert result["client_id"] == "sgp-prod"
        sistema.refresh_from_db()
        assert sistema.kc_client_id == "sgp-prod"
        assert sistema.status == "loaded"

    def test_updates_existing_client(self, settings):
        settings.KEYCLOAK_REALM = "sme-apps"
        settings.KEYCLOAK_SERVER_URL = "http://kc"
        settings.KEYCLOAK_VERIFY_SSL = False
        settings.KEYCLOAK_CLIENT_SUFFIX = "prod"

        sistema = self._make_sistema(coresso_sis_id=11)
        admin = self._make_admin(existing_uuid="existing-uuid-999")

        sistema.kc_registration_access_token = None

        from unittest.mock import patch as mock_patch
        with mock_patch("keycloak.keycloak_admin.raise_error_from_response",
                        return_value={"registrationAccessToken": "reg-tok-new"}), \
             mock_patch("core.keycloak_client._with_backoff", wraps=lambda fn, *a, **kw: fn(*a, **kw)):
            from core.keycloak_client import upsert_kc_client
            result = upsert_kc_client(admin, sistema, realm="sme-apps")

        assert result["action"] == "updated"
        assert result["kc_uuid"] == "existing-uuid-999"
        sistema.refresh_from_db()
        assert sistema.status == "loaded"

    def test_slugify_for_client_empty_sigla(self, settings):
        settings.KEYCLOAK_REALM = "sme-apps"
        settings.KEYCLOAK_SERVER_URL = "http://kc"
        settings.KEYCLOAK_VERIFY_SSL = False
        settings.KEYCLOAK_CLIENT_SUFFIX = ""

        from staging.models import StagingSistema
        sistema = StagingSistema.objects.create(
            nome="Gestão Pedagógica",
            sigla="",
            coresso_sis_id=12,
            situacao=1,
        )
        admin = self._make_admin(existing_uuid=None)
        admin.create_client.return_value = "new-uuid-002"

        from unittest.mock import patch as mock_patch
        with mock_patch("keycloak.keycloak_admin.raise_error_from_response",
                        return_value={}), \
             mock_patch("core.keycloak_client._with_backoff", wraps=lambda fn, *a, **kw: fn(*a, **kw)):
            from core.keycloak_client import upsert_kc_client
            result = upsert_kc_client(admin, sistema, realm="sme-apps")

        assert result["action"] == "created"

        assert "gestao" in result["client_id"] or "pedagogica" in result["client_id"]




class TestUpsertKcClientRole:
    def _make_perfil(self, coresso_sis_id=10):
        from staging.models import StagingSistema, StagingPerfilCoreSSO
        sistema = StagingSistema.objects.create(
            nome="Sistema SGP",
            sigla="sgp",
            coresso_sis_id=coresso_sis_id,
            kc_client_uuid="kc-client-uuid-001",
            kc_client_id="sgp-prod",
        )
        return StagingPerfilCoreSSO.objects.create(
            nome="Administrador SGP",
            coresso_gru_id="GUID-001",
            sistema=sistema,
            coresso_sis_id=coresso_sis_id,
        )

    def test_creates_new_role(self):
        perfil = self._make_perfil()

        admin = MagicMock()
        admin.create_client_role = MagicMock()
        admin.get_client_role.return_value = {"id": "role-uuid-001", "name": perfil.kc_role_name}

        from core.keycloak_client import upsert_kc_client_role
        result = upsert_kc_client_role(admin, perfil)

        assert result["action"] == "created"
        assert result["role_id"] == "role-uuid-001"
        perfil.refresh_from_db()
        assert perfil.status == "loaded"
        assert perfil.kc_role_id == "role-uuid-001"

    def test_handles_already_exists_error(self):
        perfil = self._make_perfil(coresso_sis_id=11)

        admin = MagicMock()
        admin.create_client_role.side_effect = Exception("already exists")
        admin.get_client_role.return_value = {"id": "role-uuid-002", "name": perfil.kc_role_name}

        from core.keycloak_client import upsert_kc_client_role
        result = upsert_kc_client_role(admin, perfil)

        assert result["action"] == "updated"
        perfil.refresh_from_db()
        assert perfil.status == "loaded"

    def test_skips_when_sistema_has_no_client_uuid(self):
        from staging.models import StagingSistema, StagingPerfilCoreSSO
        sistema = StagingSistema.objects.create(
            nome="Sistema Sem UUID",
            sigla="ssm",
            coresso_sis_id=99,
            kc_client_uuid=None,
        )
        perfil = StagingPerfilCoreSSO.objects.create(
            nome="Perfil X",
            coresso_gru_id="GUID-999",
            sistema=sistema,
            coresso_sis_id=99,
        )

        admin = MagicMock()
        from core.keycloak_client import upsert_kc_client_role
        result = upsert_kc_client_role(admin, perfil)

        assert result["action"] == "skipped"
        perfil.refresh_from_db()
        assert perfil.status == "error"




class TestInferTipoUsuario:
    def test_servidor(self):
        from staging.models import StagingUsuarioServidor
        srv = StagingUsuarioServidor(rf="12345", cpf="52998224725", source="se1426")
        from core.keycloak_client import _infer_tipo_usuario
        assert _infer_tipo_usuario(srv) == "servidor"

    def test_aluno(self):
        from staging.models import StagingUsuarioAluno
        aluno = StagingUsuarioAluno(matricula="9999", source="eol_alunos")
        from core.keycloak_client import _infer_tipo_usuario
        assert _infer_tipo_usuario(aluno) == "aluno"

    def test_terceiro(self):
        from staging.models import StagingUsuarioTerceiro
        t = StagingUsuarioTerceiro(cpf="52998224725", tipo_acesso="parceiro", source="coresso")
        from core.keycloak_client import _infer_tipo_usuario
        assert _infer_tipo_usuario(t) == "parceiro"

    def test_fallback_rf(self):
        from core.keycloak_client import _infer_tipo_usuario
        obj = MagicMock(spec=[])
        obj.rf = "12345"
        obj.matricula = None
        from core.keycloak_client import _infer_tipo_usuario
        assert _infer_tipo_usuario(obj) == "servidor"

    def test_fallback_outro(self):
        from core.keycloak_client import _infer_tipo_usuario
        obj = MagicMock(spec=[])
        obj.rf = None
        obj.matricula = None
        assert _infer_tipo_usuario(obj) == "outro"

