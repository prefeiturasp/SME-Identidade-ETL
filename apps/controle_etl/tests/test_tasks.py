"""Testes para apps.controle_etl.tasks."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest

from apps.controle_etl.models import (
    CheckpointEtl,
    ExecucaoETL,
    LogEtapaETL,
    RastreioTentativa,
)
from apps.controle_etl.tasks import (
    _calcular_atraso,
    task_carregar_atributos_token,
    task_identidade_executar_pipeline,
    task_identidade_extrair_coresso,
    task_identidade_extrair_eol_alunos,
    task_identidade_extrair_se1426,
    task_identidade_limpar_staging,
    task_identidade_resolver_identidade,
    task_provisionar_identidade_keycloak,
    task_sync_rec_etl,
)

# ---------------------------------------------------------------------------
# _calcular_atraso
# ---------------------------------------------------------------------------


class TestCalcularAtraso:
    """Testa o cálculo de atraso (backoff) entre tentativas."""

    def test_primeira_tentativa(self) -> None:
        """Verifica o atraso de 60s na primeira tentativa."""
        assert _calcular_atraso(1) == 60

    def test_backoff_exponencial(self) -> None:
        """Verifica que o atraso dobra exponencialmente entre tentativas."""
        assert _calcular_atraso(2) == 120
        assert _calcular_atraso(3) == 240

    def test_respeita_atraso_maximo(self) -> None:
        """Verifica que o atraso é limitado ao valor máximo de 600s."""
        assert _calcular_atraso(10) == 600


# ---------------------------------------------------------------------------
# tasks de extração
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskIdentidadeExtrairSe1426:
    """Testa a task de extração e persistência do SE1426."""

    def test_extrai_e_persiste_com_sucesso(self) -> None:
        """Verifica extração, persistência e registro da tentativa."""
        id_execucao = str(uuid.uuid4())
        registros = [object(), object()]

        with (
            patch(
                "apps.extracao.tasks.extrair_se1426", return_value=registros
            ) as mock_extrair,
            patch(
                "apps.staging.tasks.persistir_extracao_staging",
                return_value=2,
            ) as mock_persistir,
        ):
            resultado = task_identidade_extrair_se1426(id_execucao)

        assert resultado == {"total_extraido": 2}
        mock_extrair.assert_called_once_with(data_referencia=None)
        mock_persistir.assert_called_once_with(
            registros, id_execucao=id_execucao
        )
        tentativa = RastreioTentativa.objects.get()
        assert tentativa.nome_tarefa == "task_identidade_extrair_se1426"
        assert tentativa.erro is None

    def test_repassa_data_referencia(self) -> None:
        """Verifica que data_referencia é repassada à extração."""
        id_execucao = str(uuid.uuid4())
        with (
            patch(
                "apps.extracao.tasks.extrair_se1426", return_value=[]
            ) as mock_extrair,
            patch(
                "apps.staging.tasks.persistir_extracao_staging",
                return_value=0,
            ),
        ):
            task_identidade_extrair_se1426(
                id_execucao, data_referencia="2026-01-01"
            )

        mock_extrair.assert_called_once_with(data_referencia="2026-01-01")

    def test_erro_registra_tentativa_e_reagenda(self) -> None:
        """Verifica que erro registra a tentativa com falha e chama retry."""
        id_execucao = str(uuid.uuid4())
        with (
            patch(
                "apps.extracao.tasks.extrair_se1426",
                side_effect=ConnectionError("falha de rede"),
            ),
            patch(
                "apps.controle_etl.tasks.task_identidade_extrair_se1426.retry",
                side_effect=Exception("retry-chamado"),
            ) as mock_retry,
            pytest.raises(Exception, match="retry-chamado"),
        ):
            task_identidade_extrair_se1426(id_execucao)

        mock_retry.assert_called_once()
        tentativa = RastreioTentativa.objects.get()
        assert tentativa.erro == "falha de rede"


@pytest.mark.django_db
class TestTaskIdentidadeExtrairCoresso:
    """Testa a task de extração e persistência do CoreSSO."""

    def test_extrai_e_persiste_com_sucesso(self) -> None:
        """Verifica extração e persistência com sucesso."""
        id_execucao = str(uuid.uuid4())
        with (
            patch(
                "apps.extracao.tasks.extrair_coresso", return_value=[1, 2, 3]
            ),
            patch(
                "apps.staging.tasks.persistir_extracao_staging",
                return_value=3,
            ),
        ):
            resultado = task_identidade_extrair_coresso(id_execucao)

        assert resultado == {"total_extraido": 3}

    def test_erro_reagenda(self) -> None:
        """Verifica que erro na extração dispara retentativa."""
        id_execucao = str(uuid.uuid4())
        with (
            patch(
                "apps.extracao.tasks.extrair_coresso",
                side_effect=TimeoutError("timeout"),
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_identidade_extrair_coresso.retry",
                side_effect=Exception("retry-chamado"),
            ) as mock_retry,
            pytest.raises(Exception, match="retry-chamado"),
        ):
            task_identidade_extrair_coresso(id_execucao)

        mock_retry.assert_called_once()


@pytest.mark.django_db
class TestTaskIdentidadeExtrairEolAlunos:
    """Testa a task de extração e persistência do EOL Alunos."""

    def test_extrai_e_persiste_com_sucesso(self) -> None:
        """Verifica extração e persistência com sucesso."""
        id_execucao = str(uuid.uuid4())
        with (
            patch("apps.extracao.tasks.extrair_eol_alunos", return_value=[1]),
            patch(
                "apps.staging.tasks.persistir_extracao_staging",
                return_value=1,
            ),
        ):
            resultado = task_identidade_extrair_eol_alunos(id_execucao)

        assert resultado == {"total_extraido": 1}

    def test_erro_reagenda(self) -> None:
        """Verifica que erro na extração dispara retentativa."""
        id_execucao = str(uuid.uuid4())
        with (
            patch(
                "apps.extracao.tasks.extrair_eol_alunos",
                side_effect=ConnectionError("falha"),
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_identidade_extrair_eol_alunos.retry",
                side_effect=Exception("retry-chamado"),
            ) as mock_retry,
            pytest.raises(Exception, match="retry-chamado"),
        ):
            task_identidade_extrair_eol_alunos(id_execucao)

        mock_retry.assert_called_once()


# ---------------------------------------------------------------------------
# task_identidade_resolver_identidade
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskIdentidadeResolverIdentidade:
    """Testa a task de resolução de identidade."""

    def _execucao(self) -> ExecucaoETL:
        return ExecucaoETL.objects.create(fonte="todos")

    def test_resolve_com_sucesso(self) -> None:
        """Verifica transformação e deduplicação com sucesso."""
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)

        with (
            patch(
                "apps.staging.tasks.transformar_staging",
                return_value={"total": 5, "erros": 0},
            ) as mock_transform,
            patch(
                "apps.staging.tasks.deduplicar_identidades",
                return_value={"total_deduplicado": 4, "ignorados": 1},
            ) as mock_dedup,
        ):
            resultado = task_identidade_resolver_identidade(
                [{"total_extraido": 5}], id_execucao
            )

        assert resultado == {
            "total_transformado": 5,
            "total_deduplicado": 4,
        }
        mock_transform.assert_called_once_with(id_execucao=id_execucao)
        mock_dedup.assert_called_once_with(
            {"total": 5, "erros": 0}, id_execucao=id_execucao
        )

        execucao.refresh_from_db()
        assert execucao.total_transformado == 5

        etapa = LogEtapaETL.objects.get()
        assert etapa.situacao == LogEtapaETL.Situacao.SUCESSO
        assert etapa.registros_entrada == 5
        assert etapa.registros_saida == 5

    def test_atualiza_checkpoint(self) -> None:
        """Verifica que o checkpoint é atualizado ao resolver identidade."""
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)

        with (
            patch(
                "apps.staging.tasks.transformar_staging",
                return_value={"total": 1},
            ),
            patch(
                "apps.staging.tasks.deduplicar_identidades",
                return_value={"total_deduplicado": 1},
            ),
        ):
            task_identidade_resolver_identidade([], id_execucao)

        checkpoint = CheckpointEtl.objects.get(
            id_execucao=execucao.id_execucao
        )
        assert checkpoint.etapa == "task_identidade_resolver_identidade"

    def test_erro_marca_etapa_como_falha_e_reagenda(self) -> None:
        """Verifica que erro marca a etapa como falha e dispara retentativa."""
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)

        with (
            patch(
                "apps.staging.tasks.transformar_staging",
                side_effect=ValueError("falha transform"),
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_identidade_resolver_identidade.retry",
                side_effect=Exception("retry-chamado"),
            ) as mock_retry,
            pytest.raises(Exception, match="retry-chamado"),
        ):
            task_identidade_resolver_identidade([], id_execucao)

        mock_retry.assert_called_once()
        etapa = LogEtapaETL.objects.get()
        assert etapa.situacao == LogEtapaETL.Situacao.FALHA
        assert etapa.detalhe_erro == "falha transform"


# ---------------------------------------------------------------------------
# task_provisionar_identidade_keycloak
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTaskProvisionarIdentidadeKeycloak:
    """Testa a task de provisionamento de identidades no Keycloak."""

    def _execucao(self) -> ExecucaoETL:
        return ExecucaoETL.objects.create(
            fonte="todos", realm_destino="sme-apps"
        )

    def test_ignorado_quando_carga_bulk_desabilitada(
        self, settings: Any
    ) -> None:
        """Verifica que a etapa é ignorada com a carga bulk desligada."""
        settings.ETL_CARGA_KEYCLOAK_BULK_HABILITADO = False
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)

        resultado = task_provisionar_identidade_keycloak({}, id_execucao)

        assert resultado == {"total_provisionado": 0, "ignorado": True}
        etapa = LogEtapaETL.objects.get()
        assert etapa.situacao == LogEtapaETL.Situacao.IGNORADO

    def test_provisiona_usuarios_pendentes(self, settings: Any) -> None:
        """Verifica que usuários prontos são provisionados e marcados."""
        from apps.staging.models import UsuarioServidorStaging

        settings.ETL_CARGA_KEYCLOAK_BULK_HABILITADO = True
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            cpf="12345678901",
            nome="Ana Lima",
            situacao="pronto",
        )

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_usuario_kc",
                return_value={"acao": "criado", "kc_user_id": "kc-1"},
            ),
        ):
            resultado = task_provisionar_identidade_keycloak({}, id_execucao)

        assert resultado["total_provisionado"] == 1
        assert resultado["total_erros"] == 0
        usuario = UsuarioServidorStaging.objects.get()
        assert usuario.situacao == "carregado"

    def test_usuario_ignorado_pelo_provisionamento(
        self, settings: Any
    ) -> None:
        """Verifica que usuário ignorado no provisionamento fica ignorado."""
        from apps.staging.models import UsuarioServidorStaging

        settings.ETL_CARGA_KEYCLOAK_BULK_HABILITADO = True
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            cpf="12345678901",
            nome="Ana Lima",
            situacao="pronto",
        )

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_usuario_kc",
                return_value={"acao": "ignorado", "kc_user_id": "kc-1"},
            ),
        ):
            task_provisionar_identidade_keycloak({}, id_execucao)

        usuario = UsuarioServidorStaging.objects.get()
        assert usuario.situacao == "ignorado"

    def test_erro_no_provisionamento_marca_usuario_com_erro(
        self, settings: Any
    ) -> None:
        """Verifica que erro no provisionamento marca o usuário com erro."""
        from apps.staging.models import UsuarioServidorStaging

        settings.ETL_CARGA_KEYCLOAK_BULK_HABILITADO = True
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            cpf="12345678901",
            nome="Ana Lima",
            situacao="pronto",
        )

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_usuario_kc",
                side_effect=Exception("falha kc"),
            ),
        ):
            resultado = task_provisionar_identidade_keycloak({}, id_execucao)

        assert resultado["total_erros"] == 1
        usuario = UsuarioServidorStaging.objects.get()
        assert usuario.situacao == "erro"
        assert usuario.detalhe_erro == "falha kc"

    def test_erro_geral_reagenda(self, settings: Any) -> None:
        """Verifica que erro geral (KC indisponível) dispara retentativa."""
        settings.ETL_CARGA_KEYCLOAK_BULK_HABILITADO = True
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)

        with (
            patch(
                "apps.controle_etl.orquestrador_kc.obter_admin_keycloak",
                side_effect=ConnectionError("kc indisponível"),
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_provisionar_identidade_keycloak.retry",
                side_effect=Exception("retry-chamado"),
            ) as mock_retry,
            pytest.raises(Exception, match="retry-chamado"),
        ):
            task_provisionar_identidade_keycloak({}, id_execucao)

        mock_retry.assert_called_once()

    def test_processa_mais_de_um_lote_quando_volume_excede_tamanho(
        self, settings: Any
    ) -> None:
        """Verifica que o provisionamento processa múltiplos lotes."""
        from apps.controle_etl.tasks import _TAMANHO_LOTE_PROVISIONAMENTO
        from apps.staging.models import UsuarioServidorStaging

        settings.ETL_CARGA_KEYCLOAK_BULK_HABILITADO = True
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)

        total_usuarios = _TAMANHO_LOTE_PROVISIONAMENTO + 1
        UsuarioServidorStaging.objects.bulk_create(
            [
                UsuarioServidorStaging(
                    id_execucao=execucao.id_execucao,
                    fonte="se1426",
                    cpf=str(n).zfill(11),
                    nome=f"Usuario {n}",
                    situacao="pronto",
                )
                for n in range(total_usuarios)
            ]
        )

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc.provisionar_usuario_kc",
                return_value={"acao": "criado", "kc_user_id": "kc-1"},
            ),
        ):
            resultado = task_provisionar_identidade_keycloak({}, id_execucao)

        assert resultado["total_provisionado"] == total_usuarios


# ---------------------------------------------------------------------------
# task_carregar_atributos_token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskCarregarAtributosToken:
    """Testa a task de carga de atributos no token-ms."""

    def _execucao(self) -> ExecucaoETL:
        return ExecucaoETL.objects.create(fonte="todos")

    def test_envia_usuarios_pendentes(self) -> None:
        """Verifica que usuários pendentes dos três tipos são enviados."""
        from apps.staging.models import (
            UsuarioAlunoStaging,
            UsuarioServidorStaging,
            UsuarioTerceiroStaging,
        )

        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            cpf="12345678901",
            nome="Ana Lima",
            situacao="carregado",
        )
        UsuarioAlunoStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="eol_alunos",
            cpf="98765432100",
            nome="Pedro Souza",
            situacao="pronto",
        )
        UsuarioTerceiroStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="coresso",
            cpf="11122233344",
            nome="João Silva",
            situacao="carregado",
        )

        def _consome_payloads(
            payloads: Any, id_execucao: Any
        ) -> dict[str, int]:
            quantidade = len(list(payloads))
            return {"enviados": quantidade, "lotes": 1}

        with patch(
            "apps.controle_etl.cliente_token_ms.enviar_todos",
            side_effect=_consome_payloads,
        ) as mock_enviar:
            resultado = task_carregar_atributos_token({}, id_execucao)

        assert resultado == {"enviados": 3, "lotes": 1}
        mock_enviar.assert_called_once()
        etapa = LogEtapaETL.objects.get()
        assert etapa.situacao == LogEtapaETL.Situacao.SUCESSO

    def test_erro_reagenda(self) -> None:
        """Verifica que erro no envio marca a etapa como falha e reagenda."""
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)

        with (
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_todos",
                side_effect=ConnectionError("token-ms indisponível"),
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_carregar_atributos_token.retry",
                side_effect=Exception("retry-chamado"),
            ) as mock_retry,
            pytest.raises(Exception, match="retry-chamado"),
        ):
            task_carregar_atributos_token({}, id_execucao)

        mock_retry.assert_called_once()
        etapa = LogEtapaETL.objects.get()
        assert etapa.situacao == LogEtapaETL.Situacao.FALHA


# ---------------------------------------------------------------------------
# task_sync_rec_etl
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskSyncRecEtl:
    """Testa a task final de registro operacional (sync_rec)."""

    def _execucao(self) -> ExecucaoETL:
        return ExecucaoETL.objects.create(fonte="todos")

    def test_finaliza_com_sucesso_sem_falhas(self) -> None:
        """Verifica que a execução finaliza como sucesso sem falhas."""
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        LogEtapaETL.objects.create(
            execucao=execucao,
            nome_etapa=LogEtapaETL.NomeEtapa.EXTRAIR_SE1426,
            ordem_etapa=1,
            situacao=LogEtapaETL.Situacao.SUCESSO,
            registros_erro=0,
        )

        with patch(
            "apps.controle_etl.tasks"
            ".task_identidade_limpar_staging.apply_async"
        ) as mock_limpar:
            resultado = task_sync_rec_etl({}, id_execucao)

        assert resultado["situacao"] == "sucesso"
        mock_limpar.assert_called_once_with(countdown=30)
        execucao.refresh_from_db()
        assert execucao.situacao == "sucesso"

    def test_finaliza_parcial_quando_ha_falha(self) -> None:
        """Verifica que a execução finaliza parcial com etapa em falha."""
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        LogEtapaETL.objects.create(
            execucao=execucao,
            nome_etapa=LogEtapaETL.NomeEtapa.EXTRAIR_SE1426,
            ordem_etapa=1,
            situacao=LogEtapaETL.Situacao.FALHA,
            registros_erro=2,
        )

        with patch(
            "apps.controle_etl.tasks"
            ".task_identidade_limpar_staging.apply_async"
        ):
            resultado = task_sync_rec_etl({}, id_execucao)

        assert resultado["situacao"] == "parcial"

    def test_remove_checkpoint_apos_finalizar(self) -> None:
        """Verifica que o checkpoint é removido após finalizar."""
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        CheckpointEtl.objects.create(
            id_execucao=execucao.id_execucao, etapa="alguma_etapa"
        )

        with patch(
            "apps.controle_etl.tasks"
            ".task_identidade_limpar_staging.apply_async"
        ):
            task_sync_rec_etl({}, id_execucao)

        assert not CheckpointEtl.objects.filter(
            id_execucao=execucao.id_execucao
        ).exists()

    def test_erro_marca_execucao_como_falha_sem_levantar(self) -> None:
        """Verifica que erro ao finalizar marca a execução como falha."""
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)

        with patch(
            "apps.controle_etl.models.ExecucaoETL.marcar_finalizada",
            side_effect=[Exception("erro ao finalizar"), None],
        ):
            resultado = task_sync_rec_etl({}, id_execucao)

        assert resultado == {"situacao": "falha"}


# ---------------------------------------------------------------------------
# task_identidade_limpar_staging
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskIdentidadeLimparStaging:
    """Testa a task de limpeza de registros antigos do staging."""

    def test_sem_execucoes_nao_remove_nada(self) -> None:
        """Verifica que registros recentes não são removidos."""
        from apps.staging.models import UsuarioServidorStaging

        outro_id = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=outro_id, fonte="se1426", situacao="extraido"
        )

        task_identidade_limpar_staging()

        assert UsuarioServidorStaging.objects.count() == 1

    def test_remove_registros_de_execucoes_antigas(self) -> None:
        """Verifica que registros de execuções antigas são removidos."""
        from apps.staging.models import UsuarioServidorStaging

        execucao_recente = ExecucaoETL.objects.create(
            fonte="todos", situacao="sucesso"
        )
        id_antigo = uuid.uuid4()
        UsuarioServidorStaging.objects.create(
            id_execucao=execucao_recente.id_execucao,
            fonte="se1426",
            situacao="carregado",
        )
        UsuarioServidorStaging.objects.create(
            id_execucao=id_antigo,
            fonte="se1426",
            situacao="carregado",
        )

        task_identidade_limpar_staging(manter_ultimas=2)

        restantes = set(
            UsuarioServidorStaging.objects.values_list(
                "id_execucao", flat=True
            )
        )
        assert restantes == {execucao_recente.id_execucao}


# ---------------------------------------------------------------------------
# task_identidade_executar_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskIdentidadeExecutarPipeline:
    """Testa a task orquestradora do pipeline completo de identidade."""

    def _execucao(self, fonte: str = "todos") -> ExecucaoETL:
        return ExecucaoETL.objects.create(fonte=fonte)

    def test_dispara_chord_para_fonte_todos(self) -> None:
        """Verifica que fonte "todos" dispara chord com 3 extrações."""
        execucao = self._execucao(fonte="todos")
        id_execucao = str(execucao.id_execucao)

        with (
            patch("apps.controle_etl.tasks.chord") as mock_chord,
            patch("apps.controle_etl.tasks.chain") as mock_chain,
        ):
            task_identidade_executar_pipeline(id_execucao)

        mock_chord.assert_called_once()
        tarefas_extracao = mock_chord.call_args[0][0]
        assert len(tarefas_extracao) == 3
        mock_chain.assert_called_once()
        mock_chord.return_value.assert_called_once_with(
            mock_chain.return_value
        )

        execucao.refresh_from_db()
        assert execucao.situacao == "executando"

    def test_repassa_data_referencia(self) -> None:
        """Verifica que data_referencia é repassada à task de extração."""
        execucao = self._execucao(fonte="se1426")
        id_execucao = str(execucao.id_execucao)

        with (
            patch(
                "apps.controle_etl.tasks.task_identidade_extrair_se1426.s"
            ) as mock_s,
            patch("apps.controle_etl.tasks.chord"),
            patch("apps.controle_etl.tasks.chain"),
        ):
            task_identidade_executar_pipeline(
                id_execucao, data_referencia="2026-01-01"
            )

        mock_s.assert_called_once_with(
            id_execucao=id_execucao, data_referencia="2026-01-01"
        )

    def test_fonte_unica_dispara_apenas_a_tarefa_correspondente(self) -> None:
        """Verifica que fonte única dispara só a tarefa correspondente."""
        execucao = self._execucao(fonte="coresso")
        id_execucao = str(execucao.id_execucao)

        with (
            patch("apps.controle_etl.tasks.chord") as mock_chord,
            patch("apps.controle_etl.tasks.chain"),
        ):
            task_identidade_executar_pipeline(id_execucao)

        tarefas_extracao = mock_chord.call_args[0][0]
        assert len(tarefas_extracao) == 1

    def test_fonte_sem_tarefa_correspondente_marca_falha(self) -> None:
        """Verifica que fonte sem tarefa marca a execução como falha."""
        execucao = self._execucao(fonte="fonte_invalida")
        id_execucao = str(execucao.id_execucao)

        with patch("apps.controle_etl.tasks.chord") as mock_chord:
            task_identidade_executar_pipeline(id_execucao)

        mock_chord.assert_not_called()
        execucao.refresh_from_db()
        assert execucao.situacao == "falha"
