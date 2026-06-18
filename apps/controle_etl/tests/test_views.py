"""Testes para apps.controle_etl.views."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.controle_etl.models import (
    CheckpointEtl,
    ControleProvisionamento,
    ExecucaoETL,
    MarcaDaguaExtracao,
    RastreioTentativa,
)


@pytest.fixture()
def cliente(settings: Any) -> APIClient:
    """Retorna APIClient autenticado com chave de teste."""
    settings.API_KEY = "chave-teste"
    settings.API_KEY_HEADER = "X-API-Key"
    c = APIClient()
    c.credentials(HTTP_X_API_KEY="chave-teste")
    return c


@pytest.fixture()
def cliente_anonimo() -> APIClient:
    """Retorna APIClient sem autenticação."""
    return APIClient()


# ---------------------------------------------------------------------------
# ExecucoesView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExecucoesView:
    URL = "/identidade-etl/api/v1/etl/execucoes/"

    def test_lista_execucoes_vazia(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_lista_execucoes_com_registros(self, cliente: APIClient) -> None:
        ExecucaoETL.objects.create(fonte="se1426")
        ExecucaoETL.objects.create(fonte="coresso")
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.json()) == 2

    def test_filtra_por_situacao(self, cliente: APIClient) -> None:
        ExecucaoETL.objects.create(situacao="pendente")
        ExecucaoETL.objects.create(situacao="sucesso")
        resp = cliente.get(self.URL + "?situacao=pendente")
        data = resp.json()
        assert all(e["situacao"] == "pendente" for e in data)

    def test_filtra_por_fonte(self, cliente: APIClient) -> None:
        ExecucaoETL.objects.create(fonte="se1426")
        ExecucaoETL.objects.create(fonte="coresso")
        resp = cliente.get(self.URL + "?fonte=se1426")
        data = resp.json()
        assert all(e["fonte"] == "se1426" for e in data)

    def test_sem_autenticacao_retorna_403_ou_401(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL)
        assert resp.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_criar_execucao_dispara_pipeline_completo(
        self,
        cliente: APIClient,
    ) -> None:
        with patch(
            "apps.controle_etl.tasks"
            ".task_identidade_executar_pipeline.apply_async"
        ) as mock_apply_async:
            mock_apply_async.return_value.id = "celery-task-id-abc"
            resp = cliente.post(
                self.URL,
                {"fonte": "se1426", "realm_destino": "sme-apps"},
                format="json",
            )
        assert resp.status_code == status.HTTP_201_CREATED
        data = resp.json()
        assert data["fonte"] == "se1426"
        assert data["situacao"] == "pendente"
        mock_apply_async.assert_called_once()
        _, kwargs = mock_apply_async.call_args
        assert kwargs["kwargs"]["id_execucao"] == data["id_execucao"]

    def test_criar_execucao_fonte_default_todos(
        self,
        cliente: APIClient,
    ) -> None:
        with patch(
            "apps.controle_etl.tasks"
            ".task_identidade_executar_pipeline.apply_async"
        ) as mock_apply_async:
            mock_apply_async.return_value.id = "abc"
            resp = cliente.post(self.URL, {}, format="json")
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.json()["fonte"] == "todos"


# ---------------------------------------------------------------------------
# DetalheExecucaoView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDetalheExecucaoView:
    def test_retorna_execucao_existente(self, cliente: APIClient) -> None:
        execucao = ExecucaoETL.objects.create(fonte="coresso")
        url = f"/identidade-etl/api/v1/etl/execucoes/{execucao.pk}/"
        resp = cliente.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["fonte"] == "coresso"

    def test_retorna_404_para_inexistente(self, cliente: APIClient) -> None:
        resp = cliente.get("/identidade-etl/api/v1/etl/execucoes/99999/")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# CancelarExecucaoView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCancelarExecucaoView:
    def test_cancela_execucao_pendente(self, cliente: APIClient) -> None:
        execucao = ExecucaoETL.objects.create(situacao="pendente")
        with patch("config.celery.app.control.revoke"):
            resp = cliente.post(
                f"/identidade-etl/api/v1/etl/execucoes/{execucao.pk}/cancelar/"
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["situacao"] == "cancelado"

    def test_cancela_execucao_executando(self, cliente: APIClient) -> None:
        execucao = ExecucaoETL.objects.create(situacao="executando")
        execucao.id_tarefa_celery = "some-task-id"
        execucao.save(update_fields=["id_tarefa_celery"])
        with patch("config.celery.app.control.revoke") as mock_revoke:
            resp = cliente.post(
                f"/identidade-etl/api/v1/etl/execucoes/{execucao.pk}/cancelar/"
            )
        assert resp.status_code == status.HTTP_200_OK
        mock_revoke.assert_called_once_with("some-task-id", terminate=True)

    def test_nao_cancela_execucao_finalizada(
        self,
        cliente: APIClient,
    ) -> None:
        execucao = ExecucaoETL.objects.create(situacao="sucesso")
        base = "/identidade-etl/api/v1/etl/execucoes"
        resp = cliente.post(f"{base}/{execucao.pk}/cancelar/")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_404_para_inexistente(self, cliente: APIClient) -> None:
        base = "/identidade-etl/api/v1/etl/execucoes"
        resp = cliente.post(f"{base}/99999/cancelar/")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# MarcaDaguaView / ResetarMarcaDaguaView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMarcaDaguaView:
    URL = "/identidade-etl/api/v1/etl/watermark/"

    def test_lista_vazia(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_lista_marcas_existentes(self, cliente: APIClient) -> None:
        MarcaDaguaExtracao.objects.create(fonte="se1426")
        MarcaDaguaExtracao.objects.create(fonte="coresso")
        resp = cliente.get(self.URL)
        assert len(resp.json()) == 2

    def test_resetar_marca(self, cliente: APIClient) -> None:
        MarcaDaguaExtracao.objects.create(
            fonte="se1426",
            ultima_pagina=5,
        )
        base = "/identidade-etl/api/v1/etl/watermark"
        url = f"{base}/se1426/resetar/"
        resp = cliente.post(url)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["ultima_pagina"] == 0
        assert data["ultimo_processado_em"] is None

    def test_resetar_cria_marca_se_nao_existe(
        self,
        cliente: APIClient,
    ) -> None:
        base = "/identidade-etl/api/v1/etl/watermark"
        url = f"{base}/fonte_nova/resetar/"
        resp = cliente.post(url)
        assert resp.status_code == status.HTTP_200_OK
        assert MarcaDaguaExtracao.objects.filter(fonte="fonte_nova").exists()


# ---------------------------------------------------------------------------
# Estatísticas
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEstatisticas:
    URL = "/identidade-etl/api/v1/etl/estatisticas/"

    def test_retorna_estrutura_esperada(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "execucoes" in data
        assert "provisionamento" in data
        assert "periodo" in data

    def test_conta_execucoes_recentes(self, cliente: APIClient) -> None:
        ExecucaoETL.objects.create(situacao="sucesso")
        ExecucaoETL.objects.create(situacao="falha")
        resp = cliente.get(self.URL)
        assert resp.json()["execucoes"]["total"] == 2


# ---------------------------------------------------------------------------
# TentativasView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTentativasView:
    URL = "/identidade-etl/api/v1/etl/tentativas/"

    def test_lista_vazia(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_filtra_por_id_execucao(self, cliente: APIClient) -> None:
        import uuid

        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()
        RastreioTentativa.objects.create(
            id_execucao=uid1,
            nome_tarefa="task_a",
            numero_tentativa=1,
        )
        RastreioTentativa.objects.create(
            id_execucao=uid2,
            nome_tarefa="task_b",
            numero_tentativa=1,
        )
        resp = cliente.get(self.URL + f"?id_execucao={uid1}")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["nome_tarefa"] == "task_a"

    def test_filtra_por_nome_tarefa(self, cliente: APIClient) -> None:
        import uuid

        RastreioTentativa.objects.create(
            id_execucao=uuid.uuid4(),
            nome_tarefa="task_a",
            numero_tentativa=1,
        )
        RastreioTentativa.objects.create(
            id_execucao=uuid.uuid4(),
            nome_tarefa="task_b",
            numero_tentativa=1,
        )
        resp = cliente.get(self.URL + "?nome_tarefa=task_b")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["nome_tarefa"] == "task_b"


# ---------------------------------------------------------------------------
# ControleProvisionamentoView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestControleProvisionamentoView:
    URL = "/identidade-etl/api/v1/etl/provisionamento/"

    def _registro(self, **kwargs: Any) -> ControleProvisionamento:
        defaults = {
            "tipo_entidade": "usuario",
            "sistema_origem": "se1426",
            "id_origem": "12345678901",
            "ativo": True,
        }
        defaults.update(kwargs)
        return ControleProvisionamento.objects.create(**defaults)

    def test_lista_vazia(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_filtra_por_tipo_entidade(self, cliente: APIClient) -> None:
        self._registro(tipo_entidade="usuario")
        self._registro(tipo_entidade="role", id_origem="outro")
        resp = cliente.get(self.URL + "?tipo_entidade=role")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["tipo_entidade"] == "role"

    def test_filtra_por_sistema_origem(self, cliente: APIClient) -> None:
        self._registro(sistema_origem="se1426")
        self._registro(sistema_origem="coresso", id_origem="outro")
        resp = cliente.get(self.URL + "?sistema_origem=coresso")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["sistema_origem"] == "coresso"

    def test_filtra_por_ativo(self, cliente: APIClient) -> None:
        self._registro(ativo=True)
        self._registro(ativo=False, id_origem="outro")
        resp = cliente.get(self.URL + "?ativo=false")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["ativo"] is False


# ---------------------------------------------------------------------------
# CheckpointsView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckpointsView:
    URL = "/identidade-etl/api/v1/etl/checkpoints/"

    def test_lista_vazia(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_filtra_por_id_execucao(self, cliente: APIClient) -> None:
        import uuid

        uid1 = uuid.uuid4()
        uid2 = uuid.uuid4()
        CheckpointEtl.objects.create(id_execucao=uid1, etapa="etapa_a")
        CheckpointEtl.objects.create(id_execucao=uid2, etapa="etapa_b")
        resp = cliente.get(self.URL + f"?id_execucao={uid1}")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["etapa"] == "etapa_a"


# ---------------------------------------------------------------------------
# ConsultaIdentidadeView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConsultaIdentidadeView:
    URL = "/identidade-etl/api/v1/etl/identidades/consultar/"

    def test_endpoint_publico_sem_autenticacao(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL + "?cpf=12345678901")
        assert resp.status_code == status.HTTP_200_OK

    def test_busca_por_cpf_encontrado(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        ControleProvisionamento.objects.create(
            tipo_entidade="usuario",
            sistema_origem="se1426",
            id_origem="12345678901",
        )
        resp = cliente_anonimo.get(self.URL + "?cpf=123.456.789-01")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id_origem"] == "12345678901"

    def test_busca_por_rf_encontrado(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        ControleProvisionamento.objects.create(
            tipo_entidade="usuario",
            sistema_origem="coresso",
            id_origem="987654",
        )
        resp = cliente_anonimo.get(self.URL + "?rf=987654")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 1
        assert data[0]["sistema_origem"] == "coresso"

    def test_filtra_por_sistema_origem(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        ControleProvisionamento.objects.create(
            tipo_entidade="usuario",
            sistema_origem="se1426",
            id_origem="11111111111",
        )
        ControleProvisionamento.objects.create(
            tipo_entidade="usuario",
            sistema_origem="coresso",
            id_origem="11111111111",
        )
        resp = cliente_anonimo.get(
            self.URL + "?cpf=11111111111&sistema_origem=coresso"
        )
        data = resp.json()
        assert len(data) == 1
        assert data[0]["sistema_origem"] == "coresso"

    def test_nao_encontrado_retorna_lista_vazia(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL + "?cpf=00000000000")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_busca_por_email_retorna_400(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL + "?email=alguem@sme.sp")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_sem_identificador_retorna_400(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# HealthCheckView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHealthCheckView:
    URL = "/identidade-etl/api/v1/etl/health/"

    def test_endpoint_publico_sem_autenticacao(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["status"] == "healthy"

    def test_banco_indisponivel_retorna_503(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        with patch(
            "apps.controle_etl.views.HealthCheckView._check_database",
            return_value={"status": "unhealthy"},
        ):
            resp = cliente_anonimo.get(self.URL)
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert resp.json()["status"] == "unhealthy"


# ---------------------------------------------------------------------------
# ResumoExecucoesView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResumoExecucoesView:
    URL = "/identidade-etl/api/v1/etl/monitoramento/resumo/"

    def test_endpoint_publico_sem_autenticacao(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_retorna_apenas_ultima_execucao_por_fonte(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        import datetime

        from django.utils import timezone as dj_timezone

        antiga = ExecucaoETL.objects.create(fonte="coresso")
        antiga.criado_em = dj_timezone.now() - datetime.timedelta(days=1)
        antiga.save(update_fields=["criado_em"])
        recente = ExecucaoETL.objects.create(fonte="coresso")

        resp = cliente_anonimo.get(self.URL)
        data = resp.json()
        fontes_coresso = [e for e in data if e["fonte"] == "coresso"]
        assert len(fontes_coresso) == 1
        assert fontes_coresso[0]["id_execucao"] == str(recente.id_execucao)


# ---------------------------------------------------------------------------
# DashboardView / KanbanView (smoke tests)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDashboardView:
    URL = "/dashboard/"

    def test_renderiza_sem_execucoes(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_renderiza_com_execucoes(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        ExecucaoETL.objects.create(fonte="coresso", situacao="sucesso")
        resp = cliente_anonimo.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert b"coresso" in resp.content

    def test_filtro_por_fonte(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        ExecucaoETL.objects.create(fonte="se1426")
        resp = cliente_anonimo.get(self.URL + "?fonte=se1426")
        assert resp.status_code == status.HTTP_200_OK

    def test_filtro_por_datas_e_situacao(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        ExecucaoETL.objects.create(fonte="se1426", situacao="sucesso")
        resp = cliente_anonimo.get(
            self.URL
            + "?data_inicio=2020-01-01&data_fim=2999-01-01&situacao=sucesso"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert b"se1426" in resp.content


@pytest.mark.django_db
class TestKanbanView:
    URL = "/dashboard/kanban/"

    def test_renderiza_sem_execucoes(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK

    def test_renderiza_com_etapas(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        from apps.controle_etl.models import LogEtapaETL

        execucao = ExecucaoETL.objects.create(fonte="coresso")
        LogEtapaETL.objects.create(
            execucao=execucao,
            nome_etapa=LogEtapaETL.NomeEtapa.EXTRAIR_CORESSO,
            ordem_etapa=2,
            registros_entrada=10,
            registros_saida=10,
        )
        resp = cliente_anonimo.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert b"coresso" in resp.content

    def test_filtro_por_id_execucao_inexistente(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(
            self.URL + "?id_execucao=00000000-0000-0000-0000-000000000000"
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_filtro_por_fonte_restringe_resultado(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        ExecucaoETL.objects.create(fonte="se1426")
        ExecucaoETL.objects.create(fonte="coresso")
        resp = cliente_anonimo.get(self.URL + "?fonte=coresso")
        assert resp.status_code == status.HTTP_200_OK
        assert b"coresso" in resp.content

    def test_filtro_por_id_execucao_existente(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        execucao = ExecucaoETL.objects.create(fonte="coresso")
        resp = cliente_anonimo.get(
            self.URL + f"?id_execucao={execucao.id_execucao}"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert str(execucao.id_execucao).encode() in resp.content

    def test_filtro_por_id_execucao_e_fonte_diferente_inclui_na_lista(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        execucao = ExecucaoETL.objects.create(fonte="coresso")
        resp = cliente_anonimo.get(
            self.URL + f"?fonte=se1426&id_execucao={execucao.id_execucao}"
        )
        assert resp.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestDispararExecucaoDashboardView:
    URL = "/dashboard/executar/"

    def test_dispara_e_redireciona(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        with patch(
            "apps.controle_etl.tasks"
            ".task_identidade_executar_pipeline.apply_async"
        ) as mock_apply_async:
            mock_apply_async.return_value.id = "abc"
            resp = cliente_anonimo.post(self.URL, {"fonte": "coresso"})
        assert resp.status_code == status.HTTP_302_FOUND
        assert ExecucaoETL.objects.filter(fonte="coresso").exists()

    def test_fonte_invalida_usa_todos(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        with patch(
            "apps.controle_etl.tasks"
            ".task_identidade_executar_pipeline.apply_async"
        ) as mock_apply_async:
            mock_apply_async.return_value.id = "abc"
            cliente_anonimo.post(self.URL, {"fonte": "invalida"})
        assert ExecucaoETL.objects.filter(fonte="todos").exists()
