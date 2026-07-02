"""Testes para o management command executar_etl."""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

from apps.controle_etl.models import ExecucaoETL, LogEtapaETL


@pytest.mark.django_db
class TestExecutarEtl:
    """Testa o management command executar_etl."""

    def _marcar_sucesso(self, execucao: Any, **totais: Any) -> None:
        execucao.situacao = ExecucaoETL.Situacao.SUCESSO
        for campo, valor in totais.items():
            setattr(execucao, campo, valor)
        execucao.save()
        LogEtapaETL.objects.create(
            execucao=execucao,
            nome_etapa=LogEtapaETL.NomeEtapa.SYNC_REC,
            ordem_etapa=6,
            situacao=LogEtapaETL.Situacao.SUCESSO,
        )

    def test_modo_eager_reporta_sucesso(self, settings: Any) -> None:
        """Verifica que, em modo eager, o comando reporta sucesso."""
        settings.CELERY_TASK_ALWAYS_EAGER = True

        def _fake_pipeline(
            id_execucao: str,
            data_referencia: str | None = None,
        ) -> None:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            self._marcar_sucesso(execucao, total_extraido=5, total_carregado=5)

        saida = StringIO()
        with patch(
            "apps.controle_etl.tasks.task_identidade_executar_pipeline",
            side_effect=_fake_pipeline,
        ):
            call_command(
                "executar_etl",
                "--fonte=se1426",
                "--lote-maximo=10",
                stdout=saida,
            )

        texto = saida.getvalue()
        assert "situacao=sucesso" in texto
        assert "extraidos=5" in texto
        assert "task_sync_rec_etl: sucesso" in texto
        execucao = ExecucaoETL.objects.get()
        assert execucao.fonte == "se1426"
        assert execucao.tipo_disparo == ExecucaoETL.TipoDisparo.MANUAL

    def test_lote_maximo_sobrepoe_setting(self, settings: Any) -> None:
        """Verifica que --lote-maximo sobrepõe o valor de ETL_LOTE_MAXIMO."""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.ETL_LOTE_MAXIMO = 0

        def _fake_pipeline(
            id_execucao: str,
            data_referencia: str | None = None,
        ) -> None:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            self._marcar_sucesso(execucao)

        saida = StringIO()
        with patch(
            "apps.controle_etl.tasks.task_identidade_executar_pipeline",
            side_effect=_fake_pipeline,
        ):
            call_command("executar_etl", "--lote-maximo=25", stdout=saida)

        assert settings.ETL_LOTE_MAXIMO == 25
        assert "lote_maximo=25" in saida.getvalue()

    def test_modo_assincrono_apenas_avisa(self, settings: Any) -> None:
        """Verifica que, em modo assíncrono, o comando apenas avisa."""
        settings.CELERY_TASK_ALWAYS_EAGER = False

        saida = StringIO()
        with patch(
            "apps.controle_etl.tasks.task_identidade_executar_pipeline"
        ) as mock_pipeline:
            call_command("executar_etl", stdout=saida)

        mock_pipeline.assert_called_once()
        texto = saida.getvalue()
        assert "assíncrono" in texto
        assert "situacao=" not in texto

    def test_falha_no_pipeline_propaga_command_error(
        self, settings: Any
    ) -> None:
        """Verifica que falha no pipeline propaga CommandError."""
        settings.CELERY_TASK_ALWAYS_EAGER = True

        def _fake_pipeline(
            id_execucao: str,
            data_referencia: str | None = None,
        ) -> None:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            execucao.situacao = ExecucaoETL.Situacao.FALHA
            execucao.save()
            LogEtapaETL.objects.create(
                execucao=execucao,
                nome_etapa=LogEtapaETL.NomeEtapa.EXTRAIR_SE1426,
                ordem_etapa=1,
                situacao=LogEtapaETL.Situacao.FALHA,
                detalhe_erro="falha simulada",
            )

        saida = StringIO()
        with (
            patch(
                "apps.controle_etl.tasks.task_identidade_executar_pipeline",
                side_effect=_fake_pipeline,
            ),
            pytest.raises(CommandError),
        ):
            call_command("executar_etl", stdout=saida)

        assert "falha simulada" in saida.getvalue()

    def test_sem_etapas_nao_lista_secao_de_etapas(self, settings: Any) -> None:
        """Verifica que "Etapas:" não é exibida sem etapas registradas."""
        settings.CELERY_TASK_ALWAYS_EAGER = True

        def _fake_pipeline(
            id_execucao: str,
            data_referencia: str | None = None,
        ) -> None:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            execucao.situacao = ExecucaoETL.Situacao.SUCESSO
            execucao.save()

        saida = StringIO()
        with patch(
            "apps.controle_etl.tasks.task_identidade_executar_pipeline",
            side_effect=_fake_pipeline,
        ):
            call_command("executar_etl", stdout=saida)

        assert "Etapas:" not in saida.getvalue()

    def test_data_referencia_repassada_ao_pipeline(
        self, settings: Any
    ) -> None:
        """Verifica que --data-referencia é repassada ao pipeline."""
        settings.CELERY_TASK_ALWAYS_EAGER = True

        def _fake_pipeline(
            id_execucao: str,
            data_referencia: str | None = None,
        ) -> None:
            execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
            self._marcar_sucesso(execucao)

        with patch(
            "apps.controle_etl.tasks.task_identidade_executar_pipeline",
            side_effect=_fake_pipeline,
        ) as mock_pipeline:
            call_command(
                "executar_etl",
                "--data-referencia=2026-01-01",
                stdout=StringIO(),
            )

        mock_pipeline.assert_called_once()
        assert (
            mock_pipeline.call_args.kwargs["data_referencia"] == "2026-01-01"
        )
