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
    task_carregar_atributo_token_individual,
    task_carregar_atributos_token,
    task_carregar_lote_atributos_token,
    task_identidade_executar_pipeline,
    task_identidade_extrair_coresso,
    task_identidade_extrair_eol_alunos,
    task_identidade_extrair_se1426,
    task_identidade_limpar_staging,
    task_identidade_resolver_identidade,
    task_identidade_tratar_erro_pipeline,
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

    def test_dispara_lote_token_ms_quando_pendente(
        self, settings: Any
    ) -> None:
        """Sucesso no Keycloak com token_ms_pendente acumula e dispara em lote.

        Um único disparo por lote de provisionamento, não um por
        usuário — ver task_carregar_lote_atributos_token.
        """
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
                return_value={
                    "acao": "criado",
                    "kc_user_id": "kc-1",
                    "token_ms_pendente": True,
                    "id_origem": "12345678901",
                },
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_carregar_lote_atributos_token.apply_async"
            ) as mock_disparo,
        ):
            task_provisionar_identidade_keycloak({}, id_execucao)

        mock_disparo.assert_called_once()
        kwargs = mock_disparo.call_args.kwargs["kwargs"]
        assert len(kwargs["itens"]) == 1
        assert kwargs["itens"][0]["id_origem"] == "12345678901"
        assert kwargs["itens"][0]["kc_user_id"] == "kc-1"
        assert mock_disparo.call_args.kwargs["queue"] == "etl_carga_token_ms"

    def test_nao_dispara_lote_token_ms_quando_nao_pendente(
        self, settings: Any
    ) -> None:
        """token_ms_pendente=False não entra no lote de disparo."""
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
                return_value={
                    "acao": "ignorado",
                    "kc_user_id": "kc-1",
                    "token_ms_pendente": False,
                    "id_origem": "12345678901",
                },
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_carregar_lote_atributos_token.apply_async"
            ) as mock_disparo,
        ):
            task_provisionar_identidade_keycloak({}, id_execucao)

        mock_disparo.assert_not_called()

    def test_erro_no_keycloak_nao_dispara_lote_token_ms(
        self, settings: Any
    ) -> None:
        """Erro no Keycloak não inclui o usuário no lote do token-ms.

        O token-ms depende de kc_user_id resolvido pelo Keycloak.
        """
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
            patch(
                "apps.controle_etl.tasks"
                ".task_carregar_lote_atributos_token.apply_async"
            ) as mock_disparo,
        ):
            task_provisionar_identidade_keycloak({}, id_execucao)

        mock_disparo.assert_not_called()

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

        with (
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_todos",
                side_effect=_consome_payloads,
            ) as mock_enviar,
            patch(
                "apps.controle_etl.tasks._sincronizar_perfis_execucao",
                return_value={"sincronizados": 0, "erros": 0},
            ) as mock_perfis,
        ):
            resultado = task_carregar_atributos_token({}, id_execucao)

        assert resultado == {
            "enviados": 3,
            "lotes": 1,
            "perfis_sincronizados": 0,
            "perfis_erros": 0,
            "sem_identificador": 0,
        }
        mock_enviar.assert_called_once()
        mock_perfis.assert_called_once_with(
            id_execucao, execucao.realm_destino
        )
        etapa = LogEtapaETL.objects.get()
        assert etapa.situacao == LogEtapaETL.Situacao.SUCESSO

    def test_descarta_usuario_sem_identificador(self) -> None:
        """Usuário sem rf/cpf/matricula é descartado, não trava o lote.

        O token-ms rejeita o lote inteiro (400) se qualquer item não
        tiver identificador — filtrar antes de enviar evita que um
        registro incompleto (dado real do CoreSSO, ex. terceiro sem
        nenhum identificador) bloqueie os demais usuários válidos.
        """
        from apps.staging.models import UsuarioTerceiroStaging

        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        UsuarioTerceiroStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="coresso",
            cpf="11122233344",
            nome="João Silva",
            situacao="carregado",
        )
        UsuarioTerceiroStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="coresso",
            nome="Sem Identificador",
            situacao="carregado",
        )

        def _consome_payloads(
            payloads: Any, id_execucao: Any
        ) -> dict[str, int]:
            quantidade = len(list(payloads))
            return {"enviados": quantidade, "lotes": 1}

        with (
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_todos",
                side_effect=_consome_payloads,
            ) as mock_enviar,
            patch(
                "apps.controle_etl.tasks._sincronizar_perfis_execucao",
                return_value={"sincronizados": 0, "erros": 0},
            ),
        ):
            resultado = task_carregar_atributos_token({}, id_execucao)

        assert resultado["enviados"] == 1
        assert resultado["sem_identificador"] == 1
        mock_enviar.assert_called_once()
        etapa = LogEtapaETL.objects.get()
        assert etapa.situacao == LogEtapaETL.Situacao.SUCESSO
        assert etapa.registros_entrada == 2
        assert etapa.registros_saida == 1
        assert etapa.registros_erro == 1

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

    def test_sincroniza_perfis_antes_do_push_batch(self) -> None:
        """Verifica que perfis são sincronizados antes do push-batch.

        A ProjecaoUsuario precisa existir no token-ms antes do
        push-batch de atributos complementares, para que o vínculo
        oportunista (por rf/cpf) feito no upsert de
        AtributoComplementarUsuario já a encontre — em vez de gravar
        usuario=None por a projeção ainda não existir naquele
        momento.
        """
        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        chamadas: list[str] = []

        def _push_batch(*args: Any, **kwargs: Any) -> dict[str, int]:
            chamadas.append("push_batch")
            return {"enviados": 0, "lotes": 0}

        def _perfis(*args: Any, **kwargs: Any) -> dict[str, int]:
            chamadas.append("perfis")
            return {"sincronizados": 0, "erros": 0}

        with (
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_todos",
                side_effect=_push_batch,
            ),
            patch(
                "apps.controle_etl.tasks._sincronizar_perfis_execucao",
                side_effect=_perfis,
            ),
        ):
            task_carregar_atributos_token({}, id_execucao)

        assert chamadas == ["perfis", "push_batch"]

    def test_falha_ao_sincronizar_perfil_de_um_usuario_nao_derruba_lote(
        self,
    ) -> None:
        """Verifica que erro isolado num perfil não afeta os demais."""
        from apps.staging.models import UsuarioServidorStaging

        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            rf="1234567",
            cpf="12345678901",
            nome="Ana Lima",
            situacao="carregado",
        )
        UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            rf="7654321",
            cpf="98765432100",
            nome="Beto Souza",
            situacao="carregado",
        )

        admin = object()
        payload = {"login": "x", "nome": "x", "situacao": "ativo"}

        with (
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_todos",
                return_value={"enviados": 1, "lotes": 1},
            ),
            patch(
                "apps.controle_etl.orquestrador_kc.obter_admin_keycloak",
                return_value=admin,
            ),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".resolver_kc_user_id_de_usuario",
                side_effect=["kc-1", "kc-2"],
            ),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".construir_payload_perfil_token_ms",
                return_value=payload,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_perfil",
                side_effect=[
                    Exception("falha no token-ms"),
                    {"situacao": "ok"},
                ],
            ) as mock_enviar_perfil,
        ):
            resultado = task_carregar_atributos_token({}, id_execucao)

        assert resultado["perfis_sincronizados"] == 1
        assert resultado["perfis_erros"] == 1
        assert mock_enviar_perfil.call_count == 2
        etapa = LogEtapaETL.objects.get()
        assert etapa.situacao == LogEtapaETL.Situacao.SUCESSO
        assert etapa.metadados["perfis_sincronizados"] == 1
        assert etapa.metadados["perfis_erros"] == 1

    def test_usuario_sem_kc_user_id_nao_chama_enviar_perfil(self) -> None:
        """Verifica que usuário não resolvido no Keycloak é ignorado."""
        from apps.staging.models import UsuarioServidorStaging

        execucao = self._execucao()
        id_execucao = str(execucao.id_execucao)
        UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            rf="1234567",
            cpf="12345678901",
            nome="Ana Lima",
            situacao="carregado",
        )

        with (
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_todos",
                return_value={"enviados": 1, "lotes": 1},
            ),
            patch(
                "apps.controle_etl.orquestrador_kc.obter_admin_keycloak",
                return_value=object(),
            ),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".resolver_kc_user_id_de_usuario",
                return_value=None,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_perfil"
            ) as mock_enviar_perfil,
        ):
            resultado = task_carregar_atributos_token({}, id_execucao)

        assert resultado["perfis_sincronizados"] == 0
        assert resultado["perfis_erros"] == 0
        mock_enviar_perfil.assert_not_called()


# ---------------------------------------------------------------------------
# task_carregar_atributo_token_individual
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTaskCarregarAtributoTokenIndividual:
    """Testa a carga individual de token-ms disparada pelo Keycloak."""

    def _usuario_staging(self, execucao: ExecucaoETL) -> Any:
        from apps.staging.models import UsuarioServidorStaging

        return UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            cpf="12345678901",
            rf="1234567",
            nome="Ana Lima",
            email="ana@sme.sp.gov.br",
            situacao="carregado",
        )

    def _kwargs_base(self, usuario: Any, id_execucao: str) -> dict[str, Any]:
        return {
            "modelo_staging": "UsuarioServidorStaging",
            "staging_pk": usuario.pk,
            "id_execucao": id_execucao,
            "tipo_entidade": "usuario",
            "sistema_origem": usuario.fonte,
            "id_origem": usuario.cpf,
            "realm_destino": "sme-apps",
        }

    def test_envia_e_grava_hash_token_ms(self) -> None:
        """Envia perfil+lote e grava hash_token_ms em sucesso."""
        from apps.controle_etl.models import ControleProvisionamento

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        usuario = self._usuario_staging(execucao)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".resolver_kc_user_id_de_usuario",
                return_value="kc-user-1",
            ),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".construir_payload_perfil_token_ms",
                return_value=None,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_lote"
            ) as mock_enviar_lote,
        ):
            resultado = task_carregar_atributo_token_individual(
                **self._kwargs_base(usuario, str(execucao.id_execucao))
            )

        assert resultado == {"situacao": "sucesso"}
        mock_enviar_lote.assert_called_once()
        controle = ControleProvisionamento.objects.get(id_origem=usuario.cpf)
        assert controle.hash_token_ms is not None

    def test_usa_kc_user_id_fornecido_sem_login_ou_busca(self) -> None:
        """Com kc_user_id no kwarg, pula login e busca por username.

        _provisionar_lote_kc já resolveu kc_user_id ao provisionar no
        Keycloak segundos antes — refazer login+busca aqui é
        redundante e foi o principal gargalo de throughput da carga
        de token-ms em volumes grandes.
        """
        from apps.controle_etl.models import ControleProvisionamento

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        usuario = self._usuario_staging(execucao)
        kwargs = self._kwargs_base(usuario, str(execucao.id_execucao))
        kwargs["kc_user_id"] = "kc-user-pronto"

        with (
            patch(
                "apps.controle_etl.orquestrador_kc.obter_admin_keycloak"
            ) as mock_admin,
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".resolver_kc_user_id_de_usuario"
            ) as mock_resolver,
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".construir_payload_perfil_token_ms",
                return_value=None,
            ),
            patch("apps.controle_etl.cliente_token_ms.enviar_lote"),
        ):
            resultado = task_carregar_atributo_token_individual(**kwargs)

        assert resultado == {"situacao": "sucesso"}
        mock_admin.assert_not_called()
        mock_resolver.assert_not_called()
        controle = ControleProvisionamento.objects.get(id_origem=usuario.cpf)
        assert controle.hash_token_ms is not None

    def test_no_op_quando_hash_ja_confirmado(self) -> None:
        """Não reenvia se hash_token_ms já bate com o payload atual."""
        from apps.controle_etl.models import ControleProvisionamento
        from apps.controle_etl.orquestrador_kc import (
            _payload_token_ms_hash,
            calcular_hash_conteudo,
        )

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        usuario = self._usuario_staging(execucao)
        hash_atual = calcular_hash_conteudo(_payload_token_ms_hash(usuario))
        ControleProvisionamento.objects.create(
            tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
            sistema_origem=usuario.fonte,
            id_origem=usuario.cpf,
            realm_destino="sme-apps",
            hash_keycloak="qualquer",
            hash_token_ms=hash_atual,
        )

        with patch(
            "apps.controle_etl.cliente_token_ms.enviar_lote"
        ) as mock_enviar_lote:
            resultado = task_carregar_atributo_token_individual(
                **self._kwargs_base(usuario, str(execucao.id_execucao))
            )

        assert resultado == {"situacao": "ignorado"}
        mock_enviar_lote.assert_not_called()

    def test_sem_identificador_nao_envia(self) -> None:
        """Registro sem rf/cpf/matricula é descartado, não reenvia."""
        from apps.staging.models import UsuarioTerceiroStaging

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        usuario = UsuarioTerceiroStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="coresso",
            nome="Sem Identificador",
            situacao="carregado",
        )

        with patch(
            "apps.controle_etl.cliente_token_ms.enviar_lote"
        ) as mock_enviar_lote:
            resultado = task_carregar_atributo_token_individual(
                modelo_staging="UsuarioTerceiroStaging",
                staging_pk=usuario.pk,
                id_execucao=str(execucao.id_execucao),
                tipo_entidade="usuario",
                sistema_origem=usuario.fonte,
                id_origem=str(usuario.pk),
                realm_destino="sme-apps",
            )

        assert resultado == {"situacao": "sem_identificador"}
        mock_enviar_lote.assert_not_called()

    def test_erro_transitorio_reagenda(self) -> None:
        """Erro no envio dispara retry com backoff."""
        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        usuario = self._usuario_staging(execucao)

        with (
            patch("apps.controle_etl.orquestrador_kc.obter_admin_keycloak"),
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".resolver_kc_user_id_de_usuario",
                return_value=None,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_lote",
                side_effect=Exception("token-ms indisponível"),
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_carregar_atributo_token_individual.retry",
                side_effect=Exception("retry-chamado"),
            ) as mock_retry,
            pytest.raises(Exception, match="retry-chamado"),
        ):
            task_carregar_atributo_token_individual(
                **self._kwargs_base(usuario, str(execucao.id_execucao))
            )

        mock_retry.assert_called_once()


@pytest.mark.django_db
class TestTaskCarregarLoteAtributosToken:
    """Testa a carga em lote de token-ms disparada pelo Keycloak."""

    def _usuario_staging(
        self, execucao: ExecucaoETL, cpf: str = "12345678901"
    ) -> Any:
        from apps.staging.models import UsuarioServidorStaging

        return UsuarioServidorStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="se1426",
            cpf=cpf,
            rf="1234567",
            nome="Ana Lima",
            email="ana@sme.sp.gov.br",
            situacao="carregado",
        )

    def _item(self, usuario: Any, kc_user_id: str = "kc-1") -> dict[str, Any]:
        return {
            "modelo_staging": "UsuarioServidorStaging",
            "staging_pk": usuario.pk,
            "tipo_entidade": "usuario",
            "sistema_origem": usuario.fonte,
            "id_origem": usuario.cpf,
            "kc_user_id": kc_user_id,
        }

    def test_envia_lote_e_grava_hash_de_todos(self) -> None:
        """Lote com N usuários pendentes envia 1x e grava hash de todos."""
        from apps.controle_etl.models import ControleProvisionamento

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        u1 = self._usuario_staging(execucao, cpf="11111111111")
        u2 = self._usuario_staging(execucao, cpf="22222222222")

        with (
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".construir_payload_perfil_token_ms",
                return_value=None,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_lote"
            ) as mock_enviar_lote,
        ):
            resultado = task_carregar_lote_atributos_token(
                itens=[self._item(u1), self._item(u2)],
                id_execucao=str(execucao.id_execucao),
                realm_destino="sme-apps",
            )

        assert resultado == {"enviados": 2, "descartados": 0}
        mock_enviar_lote.assert_called_once()
        (payloads_enviados,), _ = mock_enviar_lote.call_args
        assert len(payloads_enviados) == 2

        for cpf in ("11111111111", "22222222222"):
            controle = ControleProvisionamento.objects.get(id_origem=cpf)
            assert controle.hash_token_ms is not None

    def test_usuario_ja_confirmado_e_filtrado_do_lote(self) -> None:
        """Usuário com hash já confirmado não entra no POST do lote."""
        from apps.controle_etl.models import ControleProvisionamento
        from apps.controle_etl.orquestrador_kc import (
            _payload_token_ms_hash,
            calcular_hash_conteudo,
        )

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        u1 = self._usuario_staging(execucao, cpf="11111111111")
        u2 = self._usuario_staging(execucao, cpf="22222222222")

        hash_u1 = calcular_hash_conteudo(_payload_token_ms_hash(u1))
        ControleProvisionamento.objects.create(
            tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
            sistema_origem=u1.fonte,
            id_origem=u1.cpf,
            realm_destino="sme-apps",
            hash_keycloak="qualquer",
            hash_token_ms=hash_u1,
        )

        with (
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".construir_payload_perfil_token_ms",
                return_value=None,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_lote"
            ) as mock_enviar_lote,
        ):
            resultado = task_carregar_lote_atributos_token(
                itens=[self._item(u1), self._item(u2)],
                id_execucao=str(execucao.id_execucao),
                realm_destino="sme-apps",
            )

        assert resultado == {"enviados": 1, "descartados": 0}
        (payloads_enviados,), _ = mock_enviar_lote.call_args
        assert len(payloads_enviados) == 1
        assert payloads_enviados[0]["cpf"] == "22222222222"

    def test_todos_confirmados_nao_chama_enviar_lote(self) -> None:
        """Lote inteiro já confirmado não faz nenhuma chamada HTTP."""
        from apps.controle_etl.models import ControleProvisionamento
        from apps.controle_etl.orquestrador_kc import (
            _payload_token_ms_hash,
            calcular_hash_conteudo,
        )

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        usuario = self._usuario_staging(execucao)
        hash_atual = calcular_hash_conteudo(_payload_token_ms_hash(usuario))
        ControleProvisionamento.objects.create(
            tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
            sistema_origem=usuario.fonte,
            id_origem=usuario.cpf,
            realm_destino="sme-apps",
            hash_keycloak="qualquer",
            hash_token_ms=hash_atual,
        )

        with patch(
            "apps.controle_etl.cliente_token_ms.enviar_lote"
        ) as mock_enviar_lote:
            resultado = task_carregar_lote_atributos_token(
                itens=[self._item(usuario)],
                id_execucao=str(execucao.id_execucao),
                realm_destino="sme-apps",
            )

        assert resultado == {"enviados": 0, "descartados": 0}
        mock_enviar_lote.assert_not_called()

    def test_usuario_sem_identificador_e_descartado_sem_travar_lote(
        self,
    ) -> None:
        """Usuário sem rf/cpf/matricula é descartado, resto segue."""
        from apps.staging.models import UsuarioTerceiroStaging

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        sem_id = UsuarioTerceiroStaging.objects.create(
            id_execucao=execucao.id_execucao,
            fonte="coresso",
            nome="Sem Identificador",
            situacao="carregado",
        )
        com_id = self._usuario_staging(execucao)

        with (
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".construir_payload_perfil_token_ms",
                return_value=None,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_lote"
            ) as mock_enviar_lote,
        ):
            resultado = task_carregar_lote_atributos_token(
                itens=[
                    {
                        "modelo_staging": "UsuarioTerceiroStaging",
                        "staging_pk": sem_id.pk,
                        "tipo_entidade": "usuario",
                        "sistema_origem": sem_id.fonte,
                        "id_origem": str(sem_id.pk),
                        "kc_user_id": "kc-x",
                    },
                    self._item(com_id),
                ],
                id_execucao=str(execucao.id_execucao),
                realm_destino="sme-apps",
            )

        assert resultado == {"enviados": 1, "descartados": 1}
        (payloads_enviados,), _ = mock_enviar_lote.call_args
        assert len(payloads_enviados) == 1

    def test_erro_no_envio_reagenda_sem_gravar_hash(self) -> None:
        """Erro em enviar_lote dispara retry; nenhum hash é gravado."""
        from apps.controle_etl.models import ControleProvisionamento

        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        usuario = self._usuario_staging(execucao)

        with (
            patch(
                "apps.controle_etl.orquestrador_kc"
                ".construir_payload_perfil_token_ms",
                return_value=None,
            ),
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_lote",
                side_effect=Exception("token-ms indisponível"),
            ),
            patch(
                "apps.controle_etl.tasks"
                ".task_carregar_lote_atributos_token.retry",
                side_effect=Exception("retry-chamado"),
            ) as mock_retry,
            pytest.raises(Exception, match="retry-chamado"),
        ):
            task_carregar_lote_atributos_token(
                itens=[self._item(usuario)],
                id_execucao=str(execucao.id_execucao),
                realm_destino="sme-apps",
            )

        mock_retry.assert_called_once()
        controle = ControleProvisionamento.objects.get(id_origem=usuario.cpf)
        assert controle.hash_token_ms is None

    def test_sem_kc_user_id_nao_chama_enviar_perfil(self) -> None:
        """Item sem kc_user_id não chama enviar_perfil, mas entra no lote."""
        execucao = ExecucaoETL.objects.create(realm_destino="sme-apps")
        usuario = self._usuario_staging(execucao)
        item = self._item(usuario, kc_user_id=None)

        with (
            patch(
                "apps.controle_etl.cliente_token_ms.enviar_perfil"
            ) as mock_enviar_perfil,
            patch("apps.controle_etl.cliente_token_ms.enviar_lote"),
        ):
            resultado = task_carregar_lote_atributos_token(
                itens=[item],
                id_execucao=str(execucao.id_execucao),
                realm_destino="sme-apps",
            )

        assert resultado == {"enviados": 1, "descartados": 0}
        mock_enviar_perfil.assert_not_called()


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
        mock_limpar.assert_called_once_with(countdown=600)
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
            patch(
                "apps.controle_etl.views"
                "._cancelar_execucoes_nao_finalizadas_e_purgar_filas"
            ),
        ):
            task_identidade_executar_pipeline(id_execucao)

        mock_chord.assert_called_once()
        tarefas_extracao = mock_chord.call_args[0][0]
        assert len(tarefas_extracao) == 3
        mock_chain.assert_called_once()
        # 3 elos: resolver_identidade -> provisionar_keycloak ->
        # sync_rec_etl. token-ms não faz parte da chain síncrona — é
        # disparado fire-and-forget por registro a partir do Keycloak.
        assert len(mock_chain.call_args[0]) == 3
        mock_chord.return_value.assert_called_once_with(
            mock_chain.return_value.on_error.return_value
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
            patch(
                "apps.controle_etl.views"
                "._cancelar_execucoes_nao_finalizadas_e_purgar_filas"
            ),
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
            patch(
                "apps.controle_etl.views"
                "._cancelar_execucoes_nao_finalizadas_e_purgar_filas"
            ),
        ):
            task_identidade_executar_pipeline(id_execucao)

        tarefas_extracao = mock_chord.call_args[0][0]
        assert len(tarefas_extracao) == 1

    def test_fonte_sem_tarefa_correspondente_marca_falha(self) -> None:
        """Verifica que fonte sem tarefa marca a execução como falha."""
        execucao = self._execucao(fonte="fonte_invalida")
        id_execucao = str(execucao.id_execucao)

        with (
            patch("apps.controle_etl.tasks.chord") as mock_chord,
            patch(
                "apps.controle_etl.views"
                "._cancelar_execucoes_nao_finalizadas_e_purgar_filas"
            ),
        ):
            task_identidade_executar_pipeline(id_execucao)

        mock_chord.assert_not_called()
        execucao.refresh_from_db()
        assert execucao.situacao == "falha"

    def test_callback_tem_handler_de_erro_registrado(self) -> None:
        """Verifica que a chain de callback recebe link_error do chord.

        Sem isso, um ChordError (ex.: todas as extrações esgotam
        retries por SQL Server inacessível) propaga sem que nada
        marque a ExecucaoETL como falha, deixando-a presa em
        "executando" para sempre.
        """
        execucao = self._execucao(fonte="coresso")
        id_execucao = str(execucao.id_execucao)

        with (
            patch("apps.controle_etl.tasks.chord") as mock_chord,
            patch(
                "apps.controle_etl.views"
                "._cancelar_execucoes_nao_finalizadas_e_purgar_filas"
            ),
        ):
            task_identidade_executar_pipeline(id_execucao)

        callback = mock_chord.return_value.call_args[0][0]
        assert callback.options.get("link_error")

    def test_purga_filas_antes_de_montar_chord(self) -> None:
        """Purga defensiva das filas de trabalho roda no início.

        Redundância de segurança contra resíduos de outro
        processamento — ver
        _cancelar_execucoes_nao_finalizadas_e_purgar_filas.
        """
        execucao = self._execucao(fonte="coresso")
        id_execucao = str(execucao.id_execucao)

        with (
            patch("apps.controle_etl.tasks.chord"),
            patch("apps.controle_etl.tasks.chain"),
            patch(
                "apps.controle_etl.views"
                "._cancelar_execucoes_nao_finalizadas_e_purgar_filas"
            ) as mock_purgar,
        ):
            task_identidade_executar_pipeline(id_execucao)

        mock_purgar.assert_called_once_with(excluir_pk=execucao.pk)


@pytest.mark.django_db
class TestTaskIdentidadeTratarErroPipeline:
    """Testa o handler de erro do chord de extração do pipeline."""

    def test_marca_execucao_como_falha(self) -> None:
        """Verifica que uma execução em andamento é marcada como falha."""
        execucao = ExecucaoETL.objects.create(fonte="coresso")
        execucao.marcar_executando()
        id_execucao = str(execucao.id_execucao)

        task_identidade_tratar_erro_pipeline(
            request=None,
            exc=Exception("ChordError simulado"),
            traceback=None,
            id_execucao=id_execucao,
        )

        execucao.refresh_from_db()
        assert execucao.situacao == "falha"

    def test_nao_sobrescreve_execucao_ja_finalizada(self) -> None:
        """Não sobrescreve situação de execução já concluída.

        Evita corrida entre o link_error do chord e uma finalização
        legítima que já tenha acontecido por outro caminho.
        """
        execucao = ExecucaoETL.objects.create(fonte="coresso")
        execucao.marcar_executando()
        execucao.marcar_finalizada("sucesso")
        id_execucao = str(execucao.id_execucao)

        task_identidade_tratar_erro_pipeline(
            request=None,
            exc=Exception("ChordError simulado"),
            traceback=None,
            id_execucao=id_execucao,
        )

        execucao.refresh_from_db()
        assert execucao.situacao == "sucesso"
