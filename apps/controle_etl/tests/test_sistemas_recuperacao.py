"""Contrato da extração e publicação dos sistemas de recuperação."""

from typing import Any
from unittest.mock import patch

import httpx
import psycopg2
import pytest
from rest_framework.test import APIClient

from apps.controle_etl.models import ExecucaoETL
from apps.controle_etl.tasks import (
    sincronizar_sistemas_recuperacao,
)
from apps.extracao.tasks import (
    extrair_sistemas_recuperacao,
)
from config.conexoes_db import _parametros_conexao_api_eol


def test_parametros_npgsql_com_senha_contendo_ponto_virgula() -> None:
    """Preserva senhas delimitadas por aspas na conexão do legado."""
    valores = _parametros_conexao_api_eol(
        'Host=localhost;Database=api_eol;Username=leitor;Password="a;b";Port=5432'
    )
    assert valores["password"] == "a;b"
    assert valores["dbname"] == "api_eol"
    with pytest.raises(ValueError):
        _parametros_conexao_api_eol("")


def test_extracao_somente_leitura(settings: Any) -> None:
    """Lê apenas código, nome e URL na origem PostgreSQL correta."""
    settings.API_EOL_CONNECTION = "postgresql://leitor@localhost/api_eol"
    with patch("psycopg2.connect") as conectar:
        cursor = (
            conectar.return_value.cursor.return_value.__enter__.return_value
        )
        cursor.fetchall.return_value = [
            (1, "SGP", "https://sgp.example/redefinir-senha/")
        ]
        registros = extrair_sistemas_recuperacao()
    conectar.return_value.set_session.assert_called_once_with(readonly=True)
    conectar.return_value.close.assert_called_once()
    assert "public.sistemarecuperacaosenha" in cursor.execute.call_args.args[0]
    assert registros[0]["codigo_sistema"] == 1
    assert "id" not in registros[0]


@pytest.mark.django_db
def test_publicacao_por_api_e_retentativa(settings: Any) -> None:
    """Reenvia falhas transitórias sem reextrair os dados da origem."""
    settings.TOKEN_MS_URL = "http://token/identidade-token"
    settings.TOKEN_MS_API_KEY = "chave-teste"
    settings.TOKEN_MS_API_KEY_HEADER = "X-API-Key"
    registros = [
        {
            "codigo_sistema": 1,
            "nome_sistema": "SGP",
            "pagina_recuperacao_senha": "https://sgp.example/",
        }
    ]
    requisicao = httpx.Request(
        "POST",
        "http://token/identidade-token/api/v1/etl/push-batch",
    )
    with (
        patch(
            "apps.extracao.tasks.extrair_sistemas_recuperacao",
            return_value=registros,
        ) as extrair,
        patch("apps.controle_etl.cliente_token_ms.httpx.Client") as cliente,
        patch("apps.controle_etl.cliente_token_ms._aguardar_nova_tentativa"),
    ):
        post = cliente.return_value.__enter__.return_value.post
        post.side_effect = [
            httpx.Response(503, request=requisicao),
            httpx.Response(
                200,
                json={"sistemas_recuperacao_senha": {"processados": 1}},
                request=requisicao,
            ),
        ]
        assert sincronizar_sistemas_recuperacao() == {"processados": 1}
        extrair.assert_called_once()
        assert post.call_count == 2
        corpo = post.call_args.kwargs["json"]
        assert corpo["usuarios"] == []
        assert corpo["sistemas_recuperacao_senha"] == registros
        assert corpo["id_execucao"]
        assert post.call_args.kwargs["headers"]["X-API-Key"] == "chave-teste"
    execucao = ExecucaoETL.objects.get(fonte="eol_db_apoio")
    assert corpo["id_execucao"] == str(execucao.id_execucao)
    assert execucao.situacao == ExecucaoETL.Situacao.SUCESSO
    assert execucao.total_extraido == 1
    assert execucao.total_carregado == 1
    assert execucao.total_erros == 0
    assert execucao.finalizado_em is not None


@pytest.mark.django_db
def test_destino_precisa_confirmar_a_carga() -> None:
    """Não aceita sucesso silencioso de um destino sem suporte à seção."""
    with (
        patch(
            "apps.extracao.tasks.extrair_sistemas_recuperacao",
            return_value=[],
        ),
        patch(
            "apps.controle_etl.cliente_token_ms.enviar_lote",
            return_value={"processados": 0},
        ),
        pytest.raises(ValueError, match="não confirmou"),
    ):
        sincronizar_sistemas_recuperacao()
    execucao = ExecucaoETL.objects.get(fonte="eol_db_apoio")
    assert execucao.situacao == ExecucaoETL.Situacao.FALHA
    assert execucao.total_carregado == 0
    assert execucao.total_erros == 1


@pytest.mark.django_db
def test_falha_no_envio_preserva_total_lido() -> None:
    """Registra a leitura sem contar como enviados os dados rejeitados."""
    with (
        patch(
            "apps.extracao.tasks.extrair_sistemas_recuperacao",
            return_value=[{"codigo_sistema": 1}],
        ),
        patch(
            "apps.controle_etl.cliente_token_ms.enviar_lote",
            side_effect=httpx.ConnectError("destino indisponível"),
        ),
        pytest.raises(httpx.ConnectError),
    ):
        sincronizar_sistemas_recuperacao()
    execucao = ExecucaoETL.objects.get(fonte="eol_db_apoio")
    assert execucao.situacao == ExecucaoETL.Situacao.FALHA
    assert execucao.total_extraido == 1
    assert execucao.total_carregado == 0


def test_comando_sincroniza_sistemas_da_origem() -> None:
    """Executa a sincronização sem receber cadastros por arquivo."""
    from django.core.management import call_command

    with patch(
        "apps.controle_etl.management.commands.sincronizar_sistemas_recuperacao.sincronizar_sistemas_recuperacao",
        return_value={"processados": 1},
    ) as publicar:
        call_command("sincronizar_sistemas_recuperacao")
    publicar.assert_called_once_with()


def test_endpoint_publica_sistemas(settings: Any) -> None:
    """Preserva a rota e os totais retornados pelo MS-Token."""
    settings.API_KEY = "chave-teste"
    cliente = APIClient()
    totais = {"processados": 4, "criados": 0, "atualizados": 4}
    with patch(
        "apps.controle_etl.tasks.sincronizar_sistemas_recuperacao",
        return_value=totais,
    ) as sincronizar:
        resposta = cliente.post(
            "/identidade-etl/api/v1/etl/sistemas-recuperacao-senha/sincronizar/",
            HTTP_X_API_KEY="chave-teste",
        )
    assert resposta.status_code == 200
    assert resposta.json() == totais
    sincronizar.assert_called_once_with()


@pytest.mark.parametrize(
    "erro",
    [
        ValueError("confirmação ausente"),
        psycopg2.OperationalError("origem indisponível"),
        httpx.ConnectError("destino indisponível"),
    ],
)
def test_endpoint_retorna_falha_sem_expor_detalhes(
    settings: Any, erro: Exception
) -> None:
    """Preserva o 502 e a mensagem pública nas falhas de sincronização."""
    settings.API_KEY = "chave-teste"
    cliente = APIClient()
    with patch(
        "apps.controle_etl.tasks.sincronizar_sistemas_recuperacao",
        side_effect=erro,
    ):
        resposta = cliente.post(
            "/identidade-etl/api/v1/etl/sistemas-recuperacao-senha/sincronizar/",
            HTTP_X_API_KEY="chave-teste",
        )
    assert resposta.status_code == 502
    assert resposta.json() == {
        "detalhe": "Falha ao sincronizar sistemas de recuperação."
    }


def test_endpoint_exige_api_key() -> None:
    """Rejeita chamadas sem chave antes de acessar origem ou destino."""
    with patch(
        "apps.controle_etl.tasks.sincronizar_sistemas_recuperacao"
    ) as sincronizar:
        resposta = APIClient().post(
            "/identidade-etl/api/v1/etl/sistemas-recuperacao-senha/sincronizar/"
        )
    assert resposta.status_code == 401
    sincronizar.assert_not_called()
