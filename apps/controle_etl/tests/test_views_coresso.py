"""Testes para views de sistemas/perfis CoreSSO em apps.controle_etl.views."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.staging.models import PerfilCoressoStaging, SistemaStaging


@pytest.fixture()
def cliente(settings: Any) -> APIClient:
    """Retorna APIClient autenticado com chave de teste."""
    settings.API_KEY = "chave-teste"
    settings.API_KEY_HEADER = "X-API-Key"
    c = APIClient()
    c.credentials(HTTP_X_API_KEY="chave-teste")
    return c


def _sistema(**kwargs: Any) -> SistemaStaging:
    defaults = {"coresso_sis_id": 1, "nome": "Sistema X", "sigla": "sisx"}
    defaults.update(kwargs)
    return SistemaStaging.objects.create(**defaults)


def _perfil(sistema: Any = None, **kwargs: Any) -> PerfilCoressoStaging:
    defaults = {
        "coresso_gru_id": "g1",
        "coresso_sis_id": 1,
        "nome": "Perfil X",
        "sistema": sistema,
    }
    defaults.update(kwargs)
    return PerfilCoressoStaging.objects.create(**defaults)


# ---------------------------------------------------------------------------
# extrair_sistemas
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExtrairSistemas:
    URL = "/identidade-etl/api/v1/etl/sistemas/extrair/"

    def test_extrai_com_sucesso(self, cliente: APIClient) -> None:
        with patch(
            "apps.extracao.tasks.extrair_sistemas_coresso",
            return_value=3,
        ) as mock_extrair:
            resp = cliente.post(self.URL)

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == {"total_extraido": 3}
        mock_extrair.assert_called_once_with()

    def test_falha_retorna_502(self, cliente: APIClient) -> None:
        with patch(
            "apps.extracao.tasks.extrair_sistemas_coresso",
            side_effect=ConnectionError("coresso indisponível"),
        ):
            resp = cliente.post(self.URL)

        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
        assert resp.json()["detalhe"] == "coresso indisponível"


# ---------------------------------------------------------------------------
# provisionar_sistemas
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProvisionarSistemas:
    URL = "/identidade-etl/api/v1/etl/sistemas/provisionar/"

    def test_provisiona_sistemas_pendentes(self, cliente: APIClient) -> None:
        _sistema(situacao=1)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_client_kc",
                return_value={"acao": "criado"},
            ),
        ):
            resp = cliente.post(self.URL, {}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["criados"] == 1
        assert data["atualizados"] == 0
        assert data["erros"] == []

    def test_filtra_por_sigla(self, cliente: APIClient) -> None:
        _sistema(coresso_sis_id=1, sigla="aaa", situacao=1)
        _sistema(coresso_sis_id=2, sigla="bbb", situacao=1)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_client_kc",
                return_value={"acao": "atualizado"},
            ) as mock_provisionar,
        ):
            resp = cliente.post(self.URL, {"sigla": "aaa"}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        mock_provisionar.assert_called_once()

    def test_erro_ao_provisionar_marca_sistema_e_retorna_no_corpo(
        self, cliente: APIClient
    ) -> None:
        sistema = _sistema(situacao=1)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_client_kc",
                side_effect=Exception("falha kc"),
            ),
        ):
            resp = cliente.post(self.URL, {}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data["erros"]) == 1
        sistema.refresh_from_db()
        assert sistema.situacao_provisionamento == "erro"
        assert sistema.detalhe_erro == "falha kc"


# ---------------------------------------------------------------------------
# listar_sistemas
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListarSistemas:
    URL = "/identidade-etl/api/v1/etl/sistemas/"

    def test_lista_vazia(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_lista_sistemas_existentes(self, cliente: APIClient) -> None:
        _sistema(coresso_sis_id=1, nome="Sistema A")
        _sistema(coresso_sis_id=2, nome="Sistema B")
        resp = cliente.get(self.URL)
        data = resp.json()
        assert len(data) == 2
        assert data[0]["coresso_sis_id"] == 1


# ---------------------------------------------------------------------------
# extrair_perfis
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExtrairPerfis:
    URL = "/identidade-etl/api/v1/etl/perfis/extrair/"

    def test_extrai_com_sucesso(self, cliente: APIClient) -> None:
        with patch(
            "apps.extracao.tasks.extrair_perfis_coresso",
            return_value=5,
        ) as mock_extrair:
            resp = cliente.post(self.URL)

        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == {"total_extraido": 5}
        mock_extrair.assert_called_once_with()

    def test_falha_retorna_502(self, cliente: APIClient) -> None:
        with patch(
            "apps.extracao.tasks.extrair_perfis_coresso",
            side_effect=ConnectionError("coresso indisponível"),
        ):
            resp = cliente.post(self.URL)

        assert resp.status_code == status.HTTP_502_BAD_GATEWAY


# ---------------------------------------------------------------------------
# provisionar_perfis
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProvisionarPerfis:
    URL = "/identidade-etl/api/v1/etl/perfis/provisionar/"

    def test_provisiona_perfis_existentes(self, cliente: APIClient) -> None:
        sistema = _sistema()
        _perfil(sistema=sistema)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_role_client_kc",
                return_value={"acao": "criado"},
            ),
        ):
            resp = cliente.post(self.URL, {}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["criados"] == 1
        assert data["atualizados"] == 0
        assert data["ignorados"] == 0
        assert data["erros"] == 0

    def test_filtra_por_coresso_sis_id(self, cliente: APIClient) -> None:
        sistema = _sistema()
        _perfil(sistema=sistema, coresso_gru_id="g1", coresso_sis_id=1)
        _perfil(sistema=sistema, coresso_gru_id="g2", coresso_sis_id=2)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_role_client_kc",
                return_value={"acao": "atualizado"},
            ) as mock_provisionar,
        ):
            resp = cliente.post(self.URL, {"coresso_sis_id": 1}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        mock_provisionar.assert_called_once()

    def test_acao_ignorado_e_contabilizada(self, cliente: APIClient) -> None:
        sistema = _sistema()
        _perfil(sistema=sistema)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_role_client_kc",
                return_value={"acao": "ignorado"},
            ),
        ):
            resp = cliente.post(self.URL, {}, format="json")

        assert resp.json()["ignorados"] == 1

    def test_erro_ao_provisionar_marca_perfil_e_retorna_detalhe(
        self, cliente: APIClient
    ) -> None:
        sistema = _sistema()
        perfil = _perfil(sistema=sistema)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_role_client_kc",
                side_effect=Exception("falha role"),
            ),
        ):
            resp = cliente.post(self.URL, {}, format="json")

        data = resp.json()
        assert data["erros"] == 1
        assert len(data["detalhes_erro"]) == 1
        perfil.refresh_from_db()
        assert perfil.situacao_provisionamento == "erro"
        assert perfil.detalhe_erro == "falha role"

    def test_renova_admin_a_cada_50_perfis(self, cliente: APIClient) -> None:
        sistema = _sistema()
        for i in range(51):
            _perfil(
                sistema=sistema,
                coresso_gru_id=f"g{i}",
                coresso_sis_id=1,
            )

        with (
            patch(
                "apps.controle_etl.orquestrador_kc.obter_admin_keycloak"
            ) as mock_admin,
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_role_client_kc",
                return_value={"acao": "criado"},
            ),
        ):
            resp = cliente.post(self.URL, {}, format="json")

        assert resp.status_code == status.HTTP_200_OK
        assert mock_admin.call_count == 2

    def test_falha_ao_renovar_admin_nao_propaga(
        self, cliente: APIClient
    ) -> None:
        sistema = _sistema()
        for i in range(51):
            _perfil(
                sistema=sistema,
                coresso_gru_id=f"g{i}",
                coresso_sis_id=1,
            )

        with (
            patch(
                "apps.controle_etl.orquestrador_kc.obter_admin_keycloak",
                side_effect=[None, Exception("kc indisponível"), None],
            ),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_role_client_kc",
                return_value={"acao": "criado"},
            ),
        ):
            resp = cliente.post(self.URL, {}, format="json")

        assert resp.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# listar_perfis
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestListarPerfis:
    URL = "/identidade-etl/api/v1/etl/perfis/"

    def test_lista_vazia(self, cliente: APIClient) -> None:
        resp = cliente.get(self.URL)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_lista_perfis_existentes(self, cliente: APIClient) -> None:
        sistema = _sistema()
        _perfil(sistema=sistema, coresso_gru_id="g1", nome="Perfil A")
        resp = cliente.get(self.URL)
        data = resp.json()
        assert len(data) == 1
        assert data[0]["nome"] == "Perfil A"

    def test_filtra_por_coresso_sis_id(self, cliente: APIClient) -> None:
        sistema = _sistema()
        _perfil(sistema=sistema, coresso_gru_id="g1", coresso_sis_id=1)
        _perfil(sistema=sistema, coresso_gru_id="g2", coresso_sis_id=2)
        resp = cliente.get(self.URL + "?coresso_sis_id=2")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["coresso_gru_id"] == "g2"


# ---------------------------------------------------------------------------
# conceder_acesso (view)
# ---------------------------------------------------------------------------

_EXTRACAO = "apps.extracao.tasks"
_ORQUESTRADOR = "apps.controle_etl.orquestrador_kc"


@pytest.mark.django_db
class TestConcederAcessoView:
    URL = "/identidade-etl/api/v1/etl/usuario/conceder-acesso/"

    def test_campos_obrigatorios(self, cliente: APIClient) -> None:
        resp = cliente.post(self.URL, {}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_usuario_nao_encontrado_retorna_204(
        self, cliente: APIClient
    ) -> None:
        """Retorna 204 (não 404) para não ser mascarado por proxy/WAF.

        Um nginx/WAF em frente ao ETL em QA intercepta qualquer
        resposta 404 e a substitui por uma página HTML genérica,
        mascarando o JSON estruturado que a view tentou enviar. 204
        não tem corpo por definição do protocolo HTTP — sem detalhe
        no JSON. Só ocorre quando o usuário não existe nem no
        CoreSSO nem no Keycloak (ver ``test_sem_coresso_...`` abaixo
        para o fallback).
        """
        with (
            patch(
                f"{_EXTRACAO}.buscar_dados_usuario_coresso",
                return_value=None,
            ),
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}._buscar_todas_contas_kc",
                return_value=[],
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "identificador": "0000000",
                    "sistema": 1,
                    "roles": ["X"],
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not resp.content

    def test_sem_coresso_mas_com_keycloak_concede_sem_sincronizar(
        self, cliente: APIClient
    ) -> None:
        """Usuário só existe no Keycloak (ex.: criado via ``usuario/criar/``).

        Quando ``buscar_dados_usuario_coresso`` não encontra vínculo
        no CoreSSO, a view busca a conta direto no Keycloak antes de
        desistir com 204 — concede o acesso ao ``kc_user_id`` já
        existente, sem upsert/sincronização.
        """
        conta_kc = {
            "id": "kc-existente",
            "username": "9000009",
            "firstName": "Fulano",
        }
        resultado_roles = {
            "sistema": "Auto Serviço",
            "client_id": "auto-servico-qa",
            "roles_atribuidos": ["COTIC"],
            "roles_nao_encontrados": [],
            "erros": 0,
        }
        with (
            patch(
                f"{_EXTRACAO}.buscar_dados_usuario_coresso",
                return_value=None,
            ),
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}._buscar_todas_contas_kc",
                return_value=[conta_kc],
            ),
            patch(
                f"{_ORQUESTRADOR}._conceder_roles_sistema_kc",
                return_value=resultado_roles,
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "identificador": "9000009",
                    "sistema": 1008,
                    "roles": ["COTIC"],
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK
        corpo = resp.json()
        assert corpo["kc_user_id"] == "kc-existente"
        assert corpo["roles_atribuidos"] == ["COTIC"]

    def test_sem_coresso_e_sem_keycloak_sistema_inexistente_retorna_204(
        self, cliente: APIClient
    ) -> None:
        """Fallback do Keycloak também trata sistema sem client como 204."""
        conta_kc = {"id": "kc-existente", "username": "9000009"}
        with (
            patch(
                f"{_EXTRACAO}.buscar_dados_usuario_coresso",
                return_value=None,
            ),
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}._buscar_todas_contas_kc",
                return_value=[conta_kc],
            ),
            patch(
                f"{_ORQUESTRADOR}._conceder_roles_sistema_kc",
                return_value={
                    "erro": (
                        "Sistema sis_id=9999 não encontrado"
                        " ou sem client no Keycloak."
                    )
                },
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "identificador": "9000009",
                    "sistema": 9999,
                    "roles": ["X"],
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not resp.content

    def test_sistema_inexistente_retorna_204(self, cliente: APIClient) -> None:
        """Retorna 204 quando o sistema não existe/não tem client.

        ``conceder_acesso_kc`` retorna um dict só com ``erro`` (sem
        ``sistema``) nesse caso — a view precisa distinguir isso do
        retorno de sucesso, que sempre inclui ``sistema``. Tratado
        como "nada a conceder" (204, sem corpo), não como erro de
        validação do payload.
        """
        resultado_erro = {
            "acao": "criado",
            "kc_user_id": "kc-1",
            "erro": (
                "Sistema sis_id=9999 não encontrado"
                " ou sem client no Keycloak."
            ),
        }
        with (
            patch(
                f"{_EXTRACAO}.buscar_dados_usuario_coresso",
                return_value={
                    "login": "7777777",
                    "cpf": "",
                    "nome": "Teste",
                    "email": "",
                    "situacao": "ativo",
                    "sistemas": {},
                },
            ),
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.conceder_acesso_kc",
                return_value=resultado_erro,
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "identificador": "7777777",
                    "sistema": 9999,
                    "roles": ["X"],
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not resp.content

    def test_sucesso(self, cliente: APIClient) -> None:
        resultado = {
            "acao": "criado",
            "kc_user_id": "kc-1",
            "username": "7777777",
            "sistema": "Teste",
            "roles_atribuidos": ["Admin"],
            "roles_nao_encontrados": [],
            "erros": 0,
        }
        with (
            patch(
                f"{_EXTRACAO}.buscar_dados_usuario_coresso",
                return_value={
                    "login": "7777777",
                    "cpf": "",
                    "nome": "Teste",
                    "email": "",
                    "situacao": "ativo",
                    "sistemas": {},
                },
            ),
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                return_value=MagicMock(),
            ),
            patch(
                f"{_ORQUESTRADOR}.conceder_acesso_kc",
                return_value=resultado,
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "identificador": "7777777",
                    "sistema": 1,
                    "roles": ["Admin"],
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["acao"] == "criado"

    def test_erro_coresso_retorna_502(self, cliente: APIClient) -> None:
        with patch(
            f"{_EXTRACAO}.buscar_dados_usuario_coresso",
            side_effect=Exception("timeout"),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "identificador": "7777777",
                    "sistema": 1,
                    "roles": ["X"],
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY

    def test_erro_keycloak_retorna_502(self, cliente: APIClient) -> None:
        with (
            patch(
                f"{_EXTRACAO}.buscar_dados_usuario_coresso",
                return_value={
                    "login": "7777777",
                    "cpf": "",
                    "nome": "Teste",
                    "email": "",
                    "situacao": "ativo",
                    "sistemas": {},
                },
            ),
            patch(
                f"{_ORQUESTRADOR}.obter_admin_keycloak",
                side_effect=Exception("kc down"),
            ),
        ):
            resp = cliente.post(
                self.URL,
                {
                    "identificador": "7777777",
                    "sistema": 1,
                    "roles": ["X"],
                },
                format="json",
            )
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
