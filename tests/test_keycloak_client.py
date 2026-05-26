import types
import pytest

from unittest.mock import MagicMock, patch
import importlib
import sys



def _make_mock_usuario(**kwargs):
    defaults = {
        "cpf": "52998224725",
        "email": "joao.silva@sme.prefeitura.sp.gov.br",
        "nome": "JOAO DA SILVA",
        "rf": "654321",
        "cargo": "PROFESSOR DE EDUCACAO INFANTIL E ENSINO FUNDAMENTAL I",
        "funcao": None,
        "situacao": "ativo",
        "lotacao": "91234",
        "lotacao_nome": "EMEF TESTE",
        "dre": "DRE-G",
        "ue": "91234",
        "matricula": None,
        "cod_escola": None,
        "turma": None,
        "tipo_acesso": None,
        "source": "se1426",
        "execution_id": "00000000-0000-0000-0000-000000000001",
        "id": "00000000-0000-0000-0000-000000000₀₀₂",
    }
    defaults.update(kwargs)
    obj = types.SimpleNamespace(**defaults)
    return obj




class TestDeriveRealmRoles:
    def _fn(self, usuario):
        from core.keycloak_client import _derive_realm_roles
        return _derive_realm_roles(usuario)

    def test_professor_cargo(self):
        u = _make_mock_usuario(cargo="PROFESSOR DE EDUCACAO INFANTIL E ENSINO FUNDAMENTAL I")
        roles = self._fn(u)
        assert "Professor" in roles

    def test_diretor_cargo(self):
        u = _make_mock_usuario(cargo="DIRETOR DE ESCOLA", funcao=None)
        roles = self._fn(u)
        assert "Diretor" in roles

    def test_funcao_overrides(self):
        u = _make_mock_usuario(cargo=None, funcao="COORDENADOR PEDAGOGICO")
        roles = self._fn(u)
        assert "CoordenadorPedagogico" in roles

    def test_unknown_cargo_returns_empty(self):
        u = _make_mock_usuario(cargo="CARGO DESCONHECIDO", funcao=None)
        roles = self._fn(u)
        assert roles == []

    def test_no_cargo_no_funcao(self):
        u = _make_mock_usuario(cargo=None, funcao=None)
        roles = self._fn(u)
        assert roles == []

    def test_case_insensitive_cargo(self):
        u = _make_mock_usuario(cargo="  diretor de escola  ", funcao=None)
        roles = self._fn(u)
        assert "Diretor" in roles




class TestDeriveGroupPaths:
    def _fn(self, usuario):
        from core.keycloak_client import _derive_group_paths
        return _derive_group_paths(usuario)

    def test_dre_and_ue(self):
        u = _make_mock_usuario(dre="DRE-G", ue="91234")
        paths = self._fn(u)
        assert paths == ["/SME/DRE-DRE-G/UE-91234"]

    def test_dre_only(self):
        u = _make_mock_usuario(dre="DRE-G", ue=None)
        paths = self._fn(u)
        assert paths == ["/SME/DRE-DRE-G"]

    def test_lotacao_fallback(self):
        u = _make_mock_usuario(dre=None, ue=None, lotacao="91234")
        paths = self._fn(u)
        assert paths == ["/SME/LOTACAO-91234"]

    def test_no_dre_no_lotacao(self):
        u = _make_mock_usuario(dre=None, ue=None, lotacao=None)
        paths = self._fn(u)
        assert paths == []




class TestResolveUsername:
    def _fn(self, usuario):
        from core.keycloak_client import _resolve_username
        return _resolve_username(usuario)

    def test_cpf_priority(self):
        u = _make_mock_usuario(cpf="52998224725", rf="654321")
        assert self._fn(u) == "52998224725"

    def test_falls_back_to_rf_when_no_cpf(self):
        u = _make_mock_usuario(cpf=None, rf="654321", matricula=None)
        assert self._fn(u) == "654321"

    def test_falls_back_to_matricula(self):
        u = _make_mock_usuario(cpf=None, rf=None, matricula="999001")
        assert self._fn(u) == "999001"

    def test_fallback_source_id(self):
        u = _make_mock_usuario(cpf=None, rf=None, matricula=None, source="coresso",
                               id="aabb1234-0000-0000-0000-000000000000")
        result = self._fn(u)
        assert "coresso" in result




class TestBuildKcPayload:
    def _fn(self, usuario):
        from core.keycloak_client import build_kc_payload
        return build_kc_payload(usuario)

    def test_payload_structure(self):
        u = _make_mock_usuario()
        p = self._fn(u)
        assert p["username"] == "52998224725"
        assert p["email"] == "joao.silva@sme.prefeitura.sp.gov.br"
        assert p["firstName"] == "JOAO"
        assert "lastName" in p
        assert p["enabled"] is True
        assert "attributes" in p
        assert "realmRoles" in p
        assert "groups" in p

    def test_inativo_disabled(self):
        u = _make_mock_usuario(situacao="inativo")
        p = self._fn(u)
        assert p["enabled"] is False

    def test_single_name_no_last_name(self):
        u = _make_mock_usuario(nome="MARIA")
        p = self._fn(u)
        assert p["firstName"] == "MARIA"
        assert p["lastName"] == "-"  # implementação retorna '-' quando há apenas um nome




class TestBuildTokenMsPayload:
    def _fn(self, usuario):
        from core.keycloak_client import build_token_ms_payload
        return build_token_ms_payload(usuario)

    def test_payload_keys_present(self):
        u = _make_mock_usuario()
        p = self._fn(u)
        assert p["cpf"] == "52998224725"
        assert p["rf"] == "654321"
        assert p["tipo_usuario"] == "servidor"
        assert p["dre"] == "DRE-G"

    def test_aluno_tipo_usuario(self):
        u = _make_mock_usuario(rf=None, matricula="999001", cargo=None)
        p = self._fn(u)

        assert "tipo_usuario" in p




class TestComputeContentHash:
    def test_deterministic(self):
        from core.keycloak_client import compute_content_hash
        payload = {"cpf": "52998224725", "nome": "Joao"}
        h1 = compute_content_hash(payload)
        h2 = compute_content_hash(payload)
        assert h1 == h2

    def test_different_payloads_different_hashes(self):
        from core.keycloak_client import compute_content_hash
        h1 = compute_content_hash({"cpf": "52998224725"})
        h2 = compute_content_hash({"cpf": "11144477735"})
        assert h1 != h2

    def test_order_invariant(self):
        from core.keycloak_client import compute_content_hash
        h1 = compute_content_hash({"a": 1, "b": 2})
        h2 = compute_content_hash({"b": 2, "a": 1})
        assert h1 == h2

    def test_returns_sha256_hex(self):
        from core.keycloak_client import compute_content_hash
        h = compute_content_hash({"x": 1})
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)




class TestWithBackoff:
    def test_success_on_first_try(self):
        from core.keycloak_client import _with_backoff
        result = _with_backoff(lambda: 42)
        assert result == 42

    def test_raises_after_max_retries(self):
        from core.keycloak_client import _with_backoff
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            raise ConnectionError("fail")

        with pytest.raises(ConnectionError):
            _with_backoff(flaky, max_retries=2, base_delay=0.001)

        assert calls["n"] == 3

    def test_succeeds_on_retry(self):
        from core.keycloak_client import _with_backoff
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        result = _with_backoff(flaky, max_retries=5, base_delay=0.001)
        assert result == "ok"
        assert calls["n"] == 3


class FakeSistema:
    class Status:
        LOADED = "loaded"
        ERROR = "error"

    def __init__(self):
        self.kc_client_id = None
        self.kc_client_uuid = None
        self.kc_realm = None
        self.kc_registration_access_token = None
        self.status = None
        self.updated_at = None

    def save(self, update_fields=None):
        self.saved_update_fields = update_fields


class FakePerfil:
    def __init__(self):
        self.kc_role_id = None
        self.status = None
        self.error_detail = None
        self.updated_at = None

    def save(self, update_fields=None):
        self.saved_update_fields = update_fields


class TestImportFallbacks:
    def test_import_error_fallbacks(self):
        original_keycloak = sys.modules.pop("keycloak", None)
        original_exceptions = sys.modules.pop("keycloak.exceptions", None)
        original_admin = sys.modules.pop("keycloak.keycloak_admin", None)

        try:
            with patch.dict(sys.modules, {"keycloak": None}):
                import core.keycloak_client as kc
                importlib.reload(kc)

                assert kc.KeycloakAdmin is None
                assert kc.raise_error_from_response is None
                assert kc.KeycloakConnectionError is ConnectionError
        finally:
            if original_keycloak:
                sys.modules["keycloak"] = original_keycloak
            if original_exceptions:
                sys.modules["keycloak.exceptions"] = original_exceptions
            if original_admin:
                sys.modules["keycloak.keycloak_admin"] = original_admin

            import core.keycloak_client as kc
            importlib.reload(kc)


class TestResolveRoleFallbackPatterns:
    def test_pattern_match_returns_role(self):
        from core.keycloak_client import (
            _CARGO_RULES,
            CARGO_ROLE_MAP,
            _resolve_role,
        )

        role = _resolve_role(
            "ASSIST. DIR",
            CARGO_ROLE_MAP,
            _CARGO_RULES,
        )

        assert role == "AssistenteDiretor"


class TestAssignRolesAndGroups:
    def test_group_exception_is_ignored(self):
        from core.keycloak_client import _assign_roles_and_groups

        admin = MagicMock()

        admin.get_realm_role.return_value = {"name": "Professor"}

        admin.get_group_by_path.side_effect = Exception("group error")

        _assign_roles_and_groups(
            admin,
            "kc-user",
            [],
            ["/SME/DRE-X"],
        )

        admin.get_group_by_path.assert_called_once_with("/SME/DRE-X")


class TestFindExistingKcUser:
    def test_email_lookup_exception_falls_back(self):
        from core.keycloak_client import _find_existing_kc_user

        admin = MagicMock()

        admin.get_users.side_effect = [
            Exception("email error"),
            [{"id": "123", "username": "654321"}],
        ]

        usuario = types.SimpleNamespace(
            rf="654321",
        )

        payload = {
            "username": "654321",
            "email": "x@sme.sp.gov.br",
        }

        result = _find_existing_kc_user(admin, usuario, payload)

        assert result == "123"

    def test_delete_user_exception_is_ignored(self):
        from core.keycloak_client import _find_existing_kc_user

        admin = MagicMock()

        admin.get_users.return_value = [
            {
                "id": "legacy-id",
                "username": "old-user",
            }
        ]

        admin.delete_user.side_effect = Exception("delete error")

        usuario = types.SimpleNamespace(
            rf="654321",
        )

        payload = {
            "username": "new-user",
            "email": "x@sme.sp.gov.br",
        }

        result = _find_existing_kc_user(admin, usuario, payload)

        assert result is None

        admin.delete_user.assert_called_once_with("legacy-id")
    
    def test_email_lookup_exception_and_rf_lookup_exception_returns_none(self):
        from core.keycloak_client import _find_existing_kc_user

        admin = MagicMock()

        admin.get_users.side_effect = [
            Exception("email lookup error"),
            Exception("rf lookup error"),
        ]

        usuario = types.SimpleNamespace(
            rf="654321",
        )

        payload = {
            "username": "654321",
            "email": "user@sme.sp.gov.br",
        }

        result = _find_existing_kc_user(
            admin,
            usuario,
            payload,
        )

        assert result is None

        assert admin.get_users.call_count == 2


    def test_rf_lookup_success_after_email_exception(self):
        from core.keycloak_client import _find_existing_kc_user

        admin = MagicMock()

        admin.get_users.side_effect = [
            Exception("email lookup error"),
            [{"id": "kc-123", "username": "654321"}],
        ]

        usuario = types.SimpleNamespace(
            rf="654321",
        )

        payload = {
            "username": "654321",
            "email": "user@sme.sp.gov.br",
        }

        result = _find_existing_kc_user(
            admin,
            usuario,
            payload,
        )

        assert result == "kc-123"

        assert admin.get_users.call_count == 2


class TestUpsertUserToKeycloak:
    @patch("core.keycloak_client._assign_roles_and_groups")
    @patch("core.keycloak_client._with_backoff")
    @patch("core.keycloak_client.UpsertControl")
    @patch("core.keycloak_client.compute_content_hash")
    @patch("core.keycloak_client.build_kc_payload")
    def test_execution_updates_last_execution(
        self,
        mock_payload,
        mock_hash,
        mock_upsert_control,
        mock_backoff,
        mock_assign,
    ):
        from core.keycloak_client import upsert_user_to_keycloak

        usuario = types.SimpleNamespace(
            cpf="52998224725",
            source="se1426",
            id="1",
        )

        execution = object()

        mock_payload.return_value = {
            "username": "52998224725",
            "realmRoles": [],
            "groups": [],
        }

        mock_hash.return_value = "hash"

        upsert = MagicMock()
        upsert.target_id = "kc-id"
        upsert.content_hash = "old"
        upsert.version = 1

        mock_upsert_control.objects.get_or_create.return_value = (
            upsert,
            False,
        )

        upsert_user_to_keycloak(
            MagicMock(),
            usuario,
            execution=execution,
        )

        assert upsert.last_execution == execution


class TestTryUpdateViaRegistrationApi:
    @patch("core.keycloak_client.requests.put")
    @patch("core.keycloak_client.settings")
    def test_returns_none_when_no_token(
        self,
        mock_settings,
        mock_put,
    ):
        from core.keycloak_client import _try_update_via_registration_api

        sistema = types.SimpleNamespace(
            kc_registration_access_token="",
        )

        result = _try_update_via_registration_api(
            sistema,
            "client-id",
            "realm",
            "uuid",
            {},
        )

        assert result == (None, None)

        mock_put.assert_not_called()

    @patch("core.keycloak_client.requests.put")
    @patch("core.keycloak_client.settings")
    def test_request_exception_returns_none(
        self,
        mock_settings,
        mock_put,
    ):
        from core.keycloak_client import _try_update_via_registration_api

        sistema = types.SimpleNamespace(
            kc_registration_access_token="token",
        )

        mock_settings.KEYCLOAK_SERVER_URL = "http://localhost"
        mock_settings.KEYCLOAK_VERIFY_SSL = False

        mock_put.side_effect = Exception("request error")

        result = _try_update_via_registration_api(
            sistema,
            "client-id",
            "realm",
            "uuid",
            {},
        )

        assert result == (None, None)

    @patch("core.keycloak_client.requests.put")
    @patch("core.keycloak_client.settings")
    def test_non_200_returns_none(
        self,
        mock_settings,
        mock_put,
    ):
        from core.keycloak_client import _try_update_via_registration_api

        sistema = types.SimpleNamespace(
            kc_registration_access_token="token",
        )

        mock_settings.KEYCLOAK_SERVER_URL = "http://localhost"
        mock_settings.KEYCLOAK_VERIFY_SSL = False

        response = MagicMock()
        response.status_code = 401

        mock_put.return_value = response

        result = _try_update_via_registration_api(
            sistema,
            "client-id",
            "realm",
            "uuid",
            {},
        )

        assert result == (None, None)

    @patch("core.keycloak_client.requests.put")
    @patch("core.keycloak_client.settings")
    def test_returns_registration_token(
        self,
        mock_settings,
        mock_put,
    ):
        from core.keycloak_client import _try_update_via_registration_api

        sistema = types.SimpleNamespace(
            kc_registration_access_token="token",
        )

        mock_settings.KEYCLOAK_SERVER_URL = "http://localhost"
        mock_settings.KEYCLOAK_VERIFY_SSL = False

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "id": "uuid-1",
            "registrationAccessToken": "new-token",
        }

        mock_put.return_value = response

        result = _try_update_via_registration_api(
            sistema,
            "client-id",
            "realm",
            "uuid",
            {},
        )

        assert result == ("uuid-1", "new-token")


class TestUpdateExistingClient:
    @patch("core.keycloak_client._try_update_via_registration_api")
    @patch("core.keycloak_client._with_backoff")
    def test_returns_registration_token(
        self,
        mock_backoff,
        mock_try_update,
    ):
        from core.keycloak_client import _update_existing_client

        mock_try_update.return_value = ("uuid-1", "token")

        result = _update_existing_client(
            MagicMock(),
            MagicMock(),
            "client-id",
            "realm",
            "uuid",
            {},
        )

        assert result == ("uuid-1", "token")

    @patch("core.keycloak_client._try_update_via_registration_api")
    @patch("core.keycloak_client._with_backoff")
    def test_fallback_update_client(
        self,
        mock_backoff,
        mock_try_update,
    ):
        from core.keycloak_client import _update_existing_client

        mock_try_update.return_value = (None, None)

        result = _update_existing_client(
            MagicMock(),
            MagicMock(),
            "client-id",
            "realm",
            "uuid",
            {},
        )

        assert result == ("uuid", None)


class TestFetchRegistrationToken:
    def test_returns_none_when_not_dict(self):
        from core.keycloak_client import _fetch_registration_token

        admin = MagicMock()

        admin.connection.raw_post.return_value = {}

        with patch(
            "core.keycloak_client.raise_error_from_response",
            return_value="invalid",
        ):
            result = _fetch_registration_token(
                admin,
                "realm",
                "uuid",
                "client-id",
            )

        assert result is None


class TestSaveSistema:
    def test_sets_registration_token(self):
        from core.keycloak_client import _save_sistema

        sistema = FakeSistema()

        _save_sistema(
            sistema,
            "client-id",
            "uuid",
            "realm",
            "token-123",
        )

        assert sistema.kc_registration_access_token == "token-123"

    def test_save_fields_contains_registration_token(self):
        from core.keycloak_client import _save_sistema

        sistema = FakeSistema()

        _save_sistema(
            sistema,
            "client-id",
            "uuid",
            "realm",
            "token-123",
        )

        assert "kc_registration_access_token" in sistema.saved_update_fields


class TestUpsertKcClient:
    @patch("core.keycloak_client._save_sistema")
    @patch("core.keycloak_client._fetch_registration_token")
    @patch("core.keycloak_client._with_backoff")
    @patch("core.keycloak_client._build_client_payload")
    @patch("core.keycloak_client.settings")
    def test_get_client_id_after_create(
        self,
        mock_settings,
        mock_payload,
        mock_backoff,
        mock_fetch,
        mock_save,
    ):
        from core.keycloak_client import upsert_kc_client

        sistema = types.SimpleNamespace(
            sigla="abc",
            nome="Sistema ABC",
        )

        mock_settings.KEYCLOAK_REALM = "realm"

        mock_payload.return_value = {}

        mock_backoff.side_effect = [
            None,
            None,
            "uuid-created",
        ]

        result = upsert_kc_client(
            MagicMock(),
            sistema,
        )

        assert result["kc_uuid"] == "uuid-created"


class TestUpsertKcClientRole:
    def test_existing_role_exception_is_ignored(self):
        from core.keycloak_client import upsert_kc_client_role

        sistema = types.SimpleNamespace(
            kc_client_uuid="uuid",
            kc_client_id="client-id",
        )

        perfil = FakePerfil()
        perfil.nome = "Perfil"
        perfil.coresso_gru_id = "1"
        perfil.coresso_sis_id = "2"
        perfil.kc_role_name = "role-name"
        perfil.sistema = sistema

        admin = MagicMock()

        admin.create_client_role.side_effect = Exception(
            "already exists"
        )

        admin.get_client_role.return_value = {
            "id": "role-id",
        }

        result = upsert_kc_client_role(
            admin,
            perfil,
        )

        assert result["action"] == "updated"

    def test_get_client_role_exception(self):
        from core.keycloak_client import upsert_kc_client_role

        sistema = types.SimpleNamespace(
            kc_client_uuid="uuid",
            kc_client_id="client-id",
        )

        perfil = FakePerfil()
        perfil.nome = "Perfil"
        perfil.coresso_gru_id = "1"
        perfil.coresso_sis_id = "2"
        perfil.kc_role_name = "role-name"
        perfil.sistema = sistema

        admin = MagicMock()

        admin.get_client_role.side_effect = Exception("error")

        result = upsert_kc_client_role(
            admin,
            perfil,
        )

        assert result["role_id"] is None

    def test_raises_when_exception_not_already_exists(self):
        from core.keycloak_client import upsert_kc_client_role

        sistema = types.SimpleNamespace(
            kc_client_uuid="uuid",
            kc_client_id="client-id",
        )

        perfil = FakePerfil()
        perfil.nome = "Perfil"
        perfil.coresso_gru_id = "1"
        perfil.coresso_sis_id = "2"
        perfil.kc_role_name = "role-name"
        perfil.sistema = sistema

        admin = MagicMock()

        admin.create_client_role.side_effect = Exception(
            "fatal error"
        )

        with pytest.raises(Exception):
            upsert_kc_client_role(
                admin,
                perfil,
            )


class TestAssignUserClientRoles:
    @patch("core.keycloak_client.fetch_coresso_groups_for_login")
    @patch("core.keycloak_client.StagingPerfilCoreSSO")
    def test_skips_profiles_without_client_uuid(
        self,
        mock_model,
        mock_fetch,
    ):
        from core.keycloak_client import assign_user_client_roles

        mock_fetch.return_value = [
            {"gru_id": "1"},
        ]

        perfil = types.SimpleNamespace(
            sistema=types.SimpleNamespace(
                kc_client_uuid=None,
            ),
            kc_role_id="role-id",
            kc_role_name="role-name",
        )

        mock_queryset = MagicMock()
        mock_queryset.filter.return_value.select_related.return_value = [
            perfil,
        ]

        mock_model.objects = mock_queryset

        result = assign_user_client_roles(
            MagicMock(),
            "kc-user",
            "login",
        )

        assert result["assigned"] == 0


class TestEmitRetroalim:
    @patch("core.keycloak_client.RetroalimentacaoCoreSSO")
    def test_exception_is_ignored(
        self,
        mock_model,
    ):
        from core.keycloak_client import emit_retroalim

        mock_model.objects.create.side_effect = Exception(
            "db error"
        )

        usuario = types.SimpleNamespace(
            rf="123",
            cpf="456",
            execution_id="exec",
        )

        emit_retroalim(
            "TIPO",
            usuario,
        )

        mock_model.objects.create.assert_called_once()