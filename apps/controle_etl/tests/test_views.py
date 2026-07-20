"""Testes para apps.controle_etl.views."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.urls import reverse
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

    _ORQUESTRADOR = "apps.controle_etl.orquestrador_kc"

    def test_sem_api_key_retorna_401(
        self,
        cliente_anonimo: APIClient,
    ) -> None:
        resp = cliente_anonimo.get(self.URL + "?cpf=12345678901")
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

    def test_busca_por_rf_encontrado(self, cliente: APIClient) -> None:
        conta_kc = {
            "id": "5c29cc47-41a1-4ef4-994f-c65aae52d456",
            "username": "7376065",
            "email": "monica.tang@sme.prefeitura.sp.gov.br",
            "firstName": "MONICA",
            "lastName": "CARVALHO TANG",
            "enabled": True,
            "attributes": {"rf": ["7376065"], "cpf": ["26930618810"]},
        }
        with (
            patch(
                f"{self._ORQUESTRADOR}.obter_admin_keycloak",
                return_value=object(),
            ),
            patch(
                f"{self._ORQUESTRADOR}._buscar_todas_contas_kc",
                return_value=[conta_kc],
            ) as mock_buscar,
        ):
            resp = cliente.get(self.URL + "?rf=7376065")

        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data) == 1
        assert data[0]["kc_user_id"] == conta_kc["id"]
        assert data[0]["username"] == "7376065"
        assert data[0]["nome"] == "MONICA CARVALHO TANG"
        assert data[0]["rf"] == "7376065"
        assert data[0]["cpf"] == "26930618810"
        mock_buscar.assert_called_once()
        args, _ = mock_buscar.call_args
        assert args[1] == "7376065"

    def test_busca_por_cpf_encontrado(self, cliente: APIClient) -> None:
        with (
            patch(
                f"{self._ORQUESTRADOR}.obter_admin_keycloak",
                return_value=object(),
            ),
            patch(
                f"{self._ORQUESTRADOR}._buscar_todas_contas_kc",
                return_value=[
                    {
                        "id": "kc-1",
                        "username": "12345678901",
                        "attributes": {},
                        "enabled": True,
                    }
                ],
            ) as mock_buscar,
        ):
            resp = cliente.get(self.URL + "?cpf=123.456.789-01")

        assert resp.status_code == status.HTTP_200_OK
        args, _ = mock_buscar.call_args
        assert args[2] == "12345678901"

    def test_nao_encontrado_retorna_lista_vazia(
        self, cliente: APIClient
    ) -> None:
        with (
            patch(
                f"{self._ORQUESTRADOR}.obter_admin_keycloak",
                return_value=object(),
            ),
            patch(
                f"{self._ORQUESTRADOR}._buscar_todas_contas_kc",
                return_value=[],
            ),
        ):
            resp = cliente.get(self.URL + "?cpf=00000000000")

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_sem_identificador_retorna_400(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_erro_no_keycloak_retorna_502(self, cliente: APIClient) -> None:
        with patch(
            f"{self._ORQUESTRADOR}.obter_admin_keycloak",
            side_effect=Exception("keycloak indisponível"),
        ):
            resp = cliente.get(self.URL + "?rf=7376065")

        assert resp.status_code == status.HTTP_502_BAD_GATEWAY


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
    URL = reverse("dashboard")

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
    URL = reverse("kanban")

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


# ------------------------------------------------------------------
# Vínculos
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestExtrairVinculos:
    URL = "/identidade-etl/api/v1/etl/vinculos/extrair/"

    def test_retorna_total_extraido(self, cliente: APIClient) -> None:
        vinculos = [
            {
                "login": "u1",
                "cpf": "111",
                "gru_id": "g1",
                "gru_nome": "X",
                "sis_id": 1,
            },
        ]
        with patch(
            "apps.extracao.tasks" ".extrair_vinculos_usuario_grupo_coresso",
            return_value=iter(vinculos),
        ):
            resp = cliente.post(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["total_extraido"] == 1

    def test_retorna_502_quando_falha(self, cliente: APIClient) -> None:
        with patch(
            "apps.extracao.tasks" ".extrair_vinculos_usuario_grupo_coresso",
            side_effect=ConnectionError("falha"),
        ):
            resp = cliente.post(self.URL)
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY


@pytest.mark.django_db
class TestProvisionarVinculos:
    URL = "/identidade-etl/api/v1/etl/vinculos/provisionar/"

    def test_retorna_resultado(self, cliente: APIClient) -> None:
        resultado = {
            "atribuidos": 2,
            "ignorados": 1,
            "erros": 0,
        }
        with (
            patch(
                "apps.controle_etl.orquestrador_kc" ".obter_admin_keycloak",
            ),
            patch(
                "apps.extracao.tasks"
                ".extrair_vinculos_usuario_grupo_coresso",
                return_value=iter([]),
            ),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".atribuir_client_roles_usuario_kc",
                return_value=resultado,
            ),
        ):
            resp = cliente.post(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["atribuidos"] == 2

    def test_retorna_502_quando_falha(self, cliente: APIClient) -> None:
        with patch(
            "apps.controle_etl.orquestrador_kc" ".obter_admin_keycloak",
            side_effect=Exception("kc down"),
        ):
            resp = cliente.post(self.URL)
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY


@pytest.mark.django_db
class TestListarVinculos:
    URL = "/identidade-etl/api/v1/etl/vinculos/"

    def test_retorna_lista_vazia_sem_dados(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data == []

    def test_retorna_sistemas_com_contagem(self, cliente: APIClient) -> None:
        from apps.staging.models import PerfilCoressoStaging, SistemaStaging

        sistema = SistemaStaging.objects.create(
            coresso_sis_id=42,
            nome="Teste",
            situacao=1,
        )
        PerfilCoressoStaging.objects.create(
            coresso_gru_id="g1",
            coresso_sis_id=42,
            sistema=sistema,
            nome="Perfil1",
            situacao_provisionamento="provisionado",
        )

        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert resp.data[0]["total_perfis"] == 1
        assert resp.data[0]["provisionados"] == 1


# ------------------------------------------------------------------
# Sincronizar Usuário
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestSincronizarUsuario:
    URL = "/identidade-etl/api/v1/etl/usuario/sincronizar/"

    def test_sem_identificador_retorna_400(self, cliente: APIClient) -> None:
        resp = cliente.post(self.URL, {})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_usuario_nao_encontrado_retorna_204(
        self, cliente: APIClient
    ) -> None:
        """Retorna 204 (não 404) para não ser mascarado por proxy/WAF.

        Um nginx/WAF em frente ao ETL em QA intercepta qualquer
        resposta 404 e a substitui por uma página HTML genérica,
        mascarando o JSON estruturado que a view tentou enviar. 204
        não tem corpo por definição do protocolo HTTP — sem detalhe
        no JSON.
        """
        with patch(
            "apps.extracao.tasks" ".buscar_dados_usuario_coresso",
            return_value=None,
        ):
            resp = cliente.post(
                self.URL,
                {"identificador": "inexistente"},
            )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not resp.content

    def test_sincroniza_com_sucesso(self, cliente: APIClient) -> None:
        dados = {
            "login": "123",
            "cpf": "",
            "nome": "Teste",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        resultado = {
            "acao": "criado",
            "kc_user_id": "kc-1",
            "username": "123",
            "nome": "Teste",
            "roles_atribuidos": 0,
            "roles_erros": 0,
            "sistemas": [],
        }
        with (
            patch(
                "apps.extracao.tasks" ".buscar_dados_usuario_coresso",
                return_value=dados,
            ),
            patch(
                "apps.controle_etl.orquestrador_kc" ".obter_admin_keycloak",
            ),
            patch(
                "apps.controle_etl.orquestrador_kc" ".sincronizar_usuario_kc",
                return_value=resultado,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_perfil",
            ) as mock_enviar_perfil,
        ):
            resp = cliente.post(
                self.URL,
                {"identificador": "123"},
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["acao"] == "criado"
        mock_enviar_perfil.assert_called_once()
        assert mock_enviar_perfil.call_args.args[0] == "kc-1"
        assert mock_enviar_perfil.call_args.args[1]["rf"] == "123"

    def test_erro_coresso_retorna_502(self, cliente: APIClient) -> None:
        with patch(
            "apps.extracao.tasks" ".buscar_dados_usuario_coresso",
            side_effect=ConnectionError("falha"),
        ):
            resp = cliente.post(
                self.URL,
                {"identificador": "123"},
            )
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY

    def test_falha_no_token_ms_nao_afeta_status_http(
        self, cliente: APIClient
    ) -> None:
        """Falha ao sincronizar no token-ms não muda a resposta HTTP.

        O Keycloak (etapa crítica) já foi atualizado com sucesso —
        a sincronização com o token-ms é best-effort.
        """
        dados = {
            "login": "123",
            "cpf": "",
            "nome": "Teste",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        resultado = {
            "acao": "criado",
            "kc_user_id": "kc-1",
            "username": "123",
            "nome": "Teste",
            "roles_atribuidos": 0,
            "roles_erros": 0,
            "sistemas": [],
        }
        with (
            patch(
                "apps.extracao.tasks" ".buscar_dados_usuario_coresso",
                return_value=dados,
            ),
            patch(
                "apps.controle_etl.orquestrador_kc" ".obter_admin_keycloak",
            ),
            patch(
                "apps.controle_etl.orquestrador_kc" ".sincronizar_usuario_kc",
                return_value=resultado,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_perfil",
                side_effect=ConnectionError("token-ms indisponível"),
            ),
        ):
            resp = cliente.post(
                self.URL,
                {"identificador": "123"},
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["acao"] == "criado"

    def test_sem_kc_user_id_nao_chama_token_ms(
        self, cliente: APIClient
    ) -> None:
        """Não tenta sincronizar no token-ms sem kc_user_id resolvido."""
        dados = {
            "login": "123",
            "cpf": "",
            "nome": "Teste",
            "email": "",
            "situacao": "ativo",
            "sistemas": {},
        }
        resultado = {"acao": "erro", "motivo": "sem kc_user_id"}
        with (
            patch(
                "apps.extracao.tasks" ".buscar_dados_usuario_coresso",
                return_value=dados,
            ),
            patch(
                "apps.controle_etl.orquestrador_kc" ".obter_admin_keycloak",
            ),
            patch(
                "apps.controle_etl.orquestrador_kc" ".sincronizar_usuario_kc",
                return_value=resultado,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_perfil",
            ) as mock_enviar_perfil,
        ):
            resp = cliente.post(
                self.URL,
                {"identificador": "123"},
            )
        assert resp.status_code == status.HTTP_200_OK
        mock_enviar_perfil.assert_not_called()


# ------------------------------------------------------------------
# Pipeline Sistema
# ------------------------------------------------------------------


@pytest.mark.django_db
class TestPipelineSistema:
    URL = "/identidade-etl/api/v1/etl/pipeline-sistema/"

    def test_sem_sis_id_retorna_400(self, cliente: APIClient) -> None:
        resp = cliente.post(self.URL, {})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_executa_pipeline(self, cliente: APIClient) -> None:
        with (
            patch(
                "apps.extracao.tasks" ".extrair_sistemas_coresso",
                return_value=1,
            ),
            patch(
                "apps.extracao.tasks" ".extrair_perfis_coresso",
                return_value=1,
            ),
            patch(
                "apps.controle_etl.orquestrador_kc" ".obter_admin_keycloak",
            ),
            patch(
                "apps.controle_etl.orquestrador_kc" ".provisionar_client_kc",
                return_value={"acao": "criado"},
            ),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".provisionar_role_client_kc",
                return_value={"acao": "criado"},
            ),
            patch(
                "apps.extracao.tasks"
                ".extrair_vinculos_usuario_grupo_coresso",
                return_value=iter([]),
            ),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".atribuir_client_roles_usuario_kc",
                return_value={
                    "atribuidos": 0,
                    "ignorados": 0,
                    "erros": 0,
                },
            ),
        ):
            from apps.staging.models import SistemaStaging

            SistemaStaging.objects.create(
                coresso_sis_id=99,
                nome="Test",
                situacao=1,
                kc_client_uuid="uuid-99",
            )
            resp = cliente.post(
                self.URL,
                {"coresso_sis_id": 99},
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["coresso_sis_id"] == 99
