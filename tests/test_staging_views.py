import uuid

import pytest
from rest_framework.test import APIClient
from unittest.mock import Mock, patch

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


def _exec_id():
    return uuid.uuid4()


class TestStagingUsuarioServidorViewSet:
    def test_list_empty(self, api_client):
        resp = api_client.get("/api/staging/servidores/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0

    def test_list_returns_servidor(self, api_client):
        from staging.models import StagingUsuarioServidor
        StagingUsuarioServidor.objects.create(
            rf="12345",
            cpf="52998224725",
            nome="JOAO SILVA",
            source="se1426",
            execution_id=_exec_id(),
        )
        resp = api_client.get("/api/staging/servidores/")
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_retrieve_servidor(self, api_client):
        from staging.models import StagingUsuarioServidor
        u = StagingUsuarioServidor.objects.create(
            rf="12345",
            source="se1426",
            execution_id=_exec_id(),
        )
        resp = api_client.get(f"/api/staging/servidores/{u.id}/")
        assert resp.status_code == 200
        assert resp.data["rf"] == "12345"

    def test_filter_by_status(self, api_client):
        from staging.models import StagingUsuarioServidor
        exec_id = _exec_id()
        StagingUsuarioServidor.objects.create(source="se1426", execution_id=exec_id, status="raw")
        StagingUsuarioServidor.objects.create(source="se1426", execution_id=exec_id, status="loaded")
        resp = api_client.get("/api/staging/servidores/?status=raw")
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_filter_by_execution_id(self, api_client):
        from staging.models import StagingUsuarioServidor
        exec_id1 = _exec_id()
        exec_id2 = _exec_id()
        StagingUsuarioServidor.objects.create(source="se1426", execution_id=exec_id1)
        StagingUsuarioServidor.objects.create(source="se1426", execution_id=exec_id2)
        resp = api_client.get(f"/api/staging/servidores/?execution_id={exec_id1}")
        assert resp.status_code == 200
        assert resp.data["count"] == 1


class TestStagingUsuarioAlunoViewSet:
    def test_list_empty(self, api_client):
        resp = api_client.get("/api/staging/alunos/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0

    def test_list_returns_aluno(self, api_client):
        from staging.models import StagingUsuarioAluno
        StagingUsuarioAluno.objects.create(
            matricula="999001",
            nome="ANA LIMA",
            source="eol_db",
            execution_id=_exec_id(),
        )
        resp = api_client.get("/api/staging/alunos/")
        assert resp.status_code == 200
        assert resp.data["count"] == 1

    def test_retrieve_aluno(self, api_client):
        from staging.models import StagingUsuarioAluno
        u = StagingUsuarioAluno.objects.create(
            matricula="999001",
            source="eol_db",
            execution_id=_exec_id(),
        )
        resp = api_client.get(f"/api/staging/alunos/{u.id}/")
        assert resp.status_code == 200
        assert resp.data["matricula"] == "999001"


class TestStagingUsuarioTerceiroViewSet:
    def test_list_empty(self, api_client):
        resp = api_client.get("/api/staging/terceiros/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0

    def test_list_returns_terceiro(self, api_client):
        from staging.models import StagingUsuarioTerceiro
        StagingUsuarioTerceiro.objects.create(
            cpf="11144477735",
            tipo_acesso="parceiro",
            source="coresso",
            execution_id=_exec_id(),
        )
        resp = api_client.get("/api/staging/terceiros/")
        assert resp.status_code == 200
        assert resp.data["count"] == 1




class TestDedupResultViewSet:
    def _make_dedup(self, exec_id=None, match_type="cpf_exact", decision="conflict", reviewed=False):
        from staging.models import DedupResult, StagingUsuarioServidor
        if exec_id is None:
            exec_id = _exec_id()
        winner = StagingUsuarioServidor.objects.create(
            rf="11111", cpf="52998224725", source="se1426", execution_id=exec_id
        )
        loser = StagingUsuarioServidor.objects.create(
            rf="22222", cpf="52998224725", source="coresso", execution_id=exec_id
        )
        return DedupResult.objects.create(
            execution_id=exec_id,
            winner=winner.id,
            winner_type="servidor",
            loser=loser.id,
            loser_type="servidor",
            dedup_key="52998224725",
            cpf="52998224725",
            match_type=match_type,
            decision=decision,
            reviewed=reviewed,
        )

    def test_list_empty(self, api_client):
        resp = api_client.get("/api/staging/dedup/")
        assert resp.status_code == 200
        assert resp.data["count"] == 0

    def test_stats_empty(self, api_client):
        resp = api_client.get("/api/staging/dedup/stats/")
        assert resp.status_code == 200
        assert resp.data["total_dedup_results"] == 0

    def test_stats_with_data(self, api_client):
        exec_id = _exec_id()
        self._make_dedup(exec_id, decision="conflict", reviewed=False)
        resp = api_client.get(f"/api/staging/dedup/stats/?execution_id={exec_id}")
        assert resp.status_code == 200
        assert resp.data["total_dedup_results"] == 1
        assert resp.data["pending_review"] == 1
        assert resp.data["by_decision"].get("conflict") == 1
        assert resp.data["execution_id"] == str(exec_id)

    def test_stats_global(self, api_client):
        self._make_dedup(decision="merged", reviewed=True)
        self._make_dedup(decision="conflict", reviewed=False)
        resp = api_client.get("/api/staging/dedup/stats/")
        assert resp.status_code == 200
        assert resp.data["total_dedup_results"] >= 2

    def test_conflicts_empty(self, api_client):
        resp = api_client.get("/api/staging/dedup/conflicts/")
        assert resp.status_code == 200

    def test_conflicts_filter_by_execution(self, api_client):
        exec_id = _exec_id()
        resp = api_client.get(f"/api/staging/dedup/conflicts/?execution_id={exec_id}")
        assert resp.status_code == 200

    def test_conflicts_without_pagination(self, api_client):
        self._make_dedup(decision="conflict", reviewed=False)

        mocked_serializer = Mock()
        mocked_serializer.data = [{"id": "1"}]

        with patch(
            "staging.views.DedupResultViewSet.paginate_queryset",
            return_value=None,
        ), patch(
            "staging.views.DedupResultViewSet.get_serializer",
            return_value=mocked_serializer,
        ):
            resp = api_client.get("/api/staging/dedup/conflicts/")

        assert resp.status_code == 200
        assert resp.data == [{"id": "1"}]