"""Testes para apps.controle_etl.models."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.controle_etl.models import (
    CheckpointEtl,
    ControleProvisionamento,
    ExecucaoETL,
    LogEtapaETL,
    MarcaDaguaExtracao,
    RastreioTentativa,
)


@pytest.mark.django_db
class TestExecucaoETL:
    """Testa o modelo ExecucaoETL."""

    def test_criacao_com_defaults(self) -> None:
        """Verifica os valores padrão na criação de uma execução."""
        execucao = ExecucaoETL.objects.create()
        assert execucao.situacao == ExecucaoETL.Situacao.PENDENTE
        assert execucao.fonte == "todos"
        assert execucao.realm_destino == "sme-apps"
        assert execucao.total_extraido == 0
        assert execucao.id_execucao is not None

    def test_marcar_executando(self) -> None:
        """Verifica que marcar_executando atualiza situação e iniciado_em."""
        execucao = ExecucaoETL.objects.create()
        execucao.marcar_executando()
        execucao.refresh_from_db()
        assert execucao.situacao == ExecucaoETL.Situacao.EXECUTANDO
        assert execucao.iniciado_em is not None

    def test_marcar_finalizada_sucesso(self) -> None:
        """Verifica que marcar_finalizada(sucesso) atualiza os campos."""
        execucao = ExecucaoETL.objects.create()
        execucao.marcar_executando()
        execucao.marcar_finalizada("sucesso")
        execucao.refresh_from_db()
        assert execucao.situacao == ExecucaoETL.Situacao.SUCESSO
        assert execucao.finalizado_em is not None

    def test_marcar_finalizada_falha(self) -> None:
        """Verifica que marcar_finalizada aceita a situação falha."""
        execucao = ExecucaoETL.objects.create()
        execucao.marcar_finalizada("falha")
        assert execucao.situacao == ExecucaoETL.Situacao.FALHA

    def test_duracao_segundos_nula_sem_inicio(self) -> None:
        """Verifica que duracao_segundos é None sem início/fim."""
        execucao = ExecucaoETL.objects.create()
        assert execucao.duracao_segundos is None

    def test_duracao_segundos_calculada(self) -> None:
        """Verifica o cálculo de duracao_segundos a partir do início/fim."""
        execucao = ExecucaoETL.objects.create()
        execucao.iniciado_em = timezone.now()
        execucao.finalizado_em = execucao.iniciado_em + timedelta(seconds=42)
        assert execucao.duracao_segundos == pytest.approx(42.0)

    def test_str_contem_fonte_e_situacao(self) -> None:
        """Verifica que __str__ contém fonte e situação da execução."""
        execucao = ExecucaoETL.objects.create(fonte="se1426")
        s = str(execucao)
        assert "se1426" in s
        assert "pendente" in s

    def test_id_execucao_unico_por_instancia(self) -> None:
        """Verifica que cada execução recebe um id_execucao distinto."""
        e1 = ExecucaoETL.objects.create()
        e2 = ExecucaoETL.objects.create()
        assert e1.id_execucao != e2.id_execucao


@pytest.mark.django_db
class TestLogEtapaETL:
    """Testa o modelo LogEtapaETL."""

    def _execucao(self) -> ExecucaoETL:
        return ExecucaoETL.objects.create()

    def test_criacao_com_defaults(self) -> None:
        """Verifica os valores padrão na criação de uma etapa."""
        execucao = self._execucao()
        etapa = LogEtapaETL.objects.create(
            execucao=execucao,
            nome_etapa=LogEtapaETL.NomeEtapa.EXTRAIR_SE1426,
            ordem_etapa=1,
        )
        assert etapa.situacao == LogEtapaETL.Situacao.EXECUTANDO
        assert etapa.registros_entrada == 0

    def test_str_contem_etapa_e_situacao(self) -> None:
        """Verifica que __str__ contém o nome da etapa."""
        execucao = self._execucao()
        etapa = LogEtapaETL.objects.create(
            execucao=execucao,
            nome_etapa=LogEtapaETL.NomeEtapa.SYNC_REC,
            ordem_etapa=6,
        )
        s = str(etapa)
        assert "task_sync_rec_etl" in s

    def test_unique_together_execucao_etapa(self) -> None:
        """Verifica que execução+etapa duplicada viola unique_together."""
        from django.db import IntegrityError

        execucao = self._execucao()
        LogEtapaETL.objects.create(
            execucao=execucao,
            nome_etapa=LogEtapaETL.NomeEtapa.EXTRAIR_CORESSO,
            ordem_etapa=2,
        )
        with pytest.raises(IntegrityError):
            LogEtapaETL.objects.create(
                execucao=execucao,
                nome_etapa=LogEtapaETL.NomeEtapa.EXTRAIR_CORESSO,
                ordem_etapa=2,
            )

    def test_relacionamento_com_execucao(self) -> None:
        """Verifica que a etapa é acessível via related_name etapas."""
        execucao = self._execucao()
        LogEtapaETL.objects.create(
            execucao=execucao,
            nome_etapa=LogEtapaETL.NomeEtapa.EXTRAIR_EOL_ALUNOS,
            ordem_etapa=3,
        )
        assert execucao.etapas.count() == 1


@pytest.mark.django_db
class TestMarcaDaguaExtracao:
    """Testa o modelo MarcaDaguaExtracao."""

    def test_get_or_create_por_fonte(self) -> None:
        """Verifica que get_or_create cria a marca com total zerado."""
        marca, criada = MarcaDaguaExtracao.objects.get_or_create(
            fonte="se1426"
        )
        assert criada is True
        assert marca.total_processado == 0

    def test_str_contem_fonte(self) -> None:
        """Verifica que __str__ contém a fonte da marca."""
        marca = MarcaDaguaExtracao.objects.create(fonte="coresso")
        assert "coresso" in str(marca)


@pytest.mark.django_db
class TestCheckpointEtl:
    """Testa o modelo CheckpointEtl."""

    def test_criacao_e_str(self) -> None:
        """Verifica criação do checkpoint e __str__ com etapa e página."""
        uid = uuid.uuid4()
        cp = CheckpointEtl.objects.create(
            id_execucao=uid,
            etapa="task_identidade_resolver_identidade",
            pagina_atual=3,
        )
        s = str(cp)
        assert "task_identidade_resolver_identidade" in s
        assert "pág 3" in s

    def test_estado_json_default_vazio(self) -> None:
        """Verifica que estado_json tem dict vazio como valor padrão."""
        cp = CheckpointEtl.objects.create(
            id_execucao=uuid.uuid4(),
            etapa="qualquer",
        )
        assert cp.estado_json == {}


@pytest.mark.django_db
class TestRastreioTentativa:
    """Testa o modelo RastreioTentativa."""

    def test_criacao(self) -> None:
        """Verifica a criação de uma tentativa sem erro."""
        uid = uuid.uuid4()
        rt = RastreioTentativa.objects.create(
            id_execucao=uid,
            nome_tarefa="task_identidade_extrair_se1426",
            numero_tentativa=1,
            duracao_segundos=1.5,
        )
        assert rt.erro is None
        assert rt.duracao_segundos == pytest.approx(1.5)

    def test_criacao_com_erro(self) -> None:
        """Verifica a criação de uma tentativa com erro registrado."""
        rt = RastreioTentativa.objects.create(
            id_execucao=uuid.uuid4(),
            nome_tarefa="task_identidade_extrair_coresso",
            numero_tentativa=2,
            erro="Connection timeout",
        )
        assert rt.erro is not None and "timeout" in rt.erro.lower()

    def test_str_contem_nome_tarefa(self) -> None:
        """Verifica que __str__ contém o nome da tarefa."""
        rt = RastreioTentativa.objects.create(
            id_execucao=uuid.uuid4(),
            nome_tarefa="task_sync_rec_etl",
            numero_tentativa=1,
        )
        assert "task_sync_rec_etl" in str(rt)


@pytest.mark.django_db
class TestControleProvisionamento:
    """Testa o modelo ControleProvisionamento."""

    def test_criacao_e_unique_together(self) -> None:
        """Verifica que a combinação tipo+sistema+origem+realm é única."""
        from django.db import IntegrityError

        ControleProvisionamento.objects.create(
            tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
            sistema_origem="se1426",
            id_origem="1234567",
            realm_destino="sme-apps",
            hash_conteudo="abc123",
        )
        with pytest.raises(IntegrityError):
            ControleProvisionamento.objects.create(
                tipo_entidade=ControleProvisionamento.TipoEntidade.USUARIO,
                sistema_origem="se1426",
                id_origem="1234567",
                realm_destino="sme-apps",
                hash_conteudo="xyz789",
            )

    def test_str_contem_tipo_e_origem(self) -> None:
        """Verifica que __str__ contém tipo de entidade e sistema origem."""
        cp = ControleProvisionamento.objects.create(
            tipo_entidade=ControleProvisionamento.TipoEntidade.GRUPO,
            sistema_origem="coresso",
            id_origem="grupo-99",
            realm_destino="sme-apps",
            hash_conteudo="zzz",
        )
        s = str(cp)
        assert "grupo" in s
        assert "coresso" in s
