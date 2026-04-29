import types
import pytest




def _make_mock_usuario(**kwargs):
    defaults = dict(
        cpf="52998224725",
        email="joao.silva@sme.prefeitura.sp.gov.br",
        nome="JOAO DA SILVA",
        rf="654321",
        cargo="PROFESSOR DE EDUCACAO INFANTIL E ENSINO FUNDAMENTAL I",
        funcao=None,
        situacao="ativo",
        lotacao="91234",
        lotacao_nome="EMEF TESTE",
        dre="DRE-G",
        ue="91234",
        matricula=None,
        cod_escola=None,
        turma=None,
        tipo_acesso=None,
        source="se1426",
        execution_id="00000000-0000-0000-0000-000000000001",
        id="00000000-0000-0000-0000-000000000002",
    )
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
        assert p["lastName"] == ""




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
