"""Comando Django para disparar o pipeline de identidade manualmente."""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.controle_etl.models import ExecucaoETL


class Command(BaseCommand):
    """Cria uma ExecucaoETL e dispara o pipeline de identidade."""

    help = (
        "Dispara o pipeline de identidade (extração → resolução → "
        "provisionamento Keycloak → token-ms → registro operacional). "
        "Use --lote-maximo para limitar o volume em testes locais."
    )

    def add_arguments(self, parser):
        """Define os argumentos do comando."""
        parser.add_argument(
            "--fonte",
            type=str,
            default="todos",
            choices=["todos", "se1426", "coresso", "eol_alunos"],
            help="Fonte a extrair. Padrão: todos.",
        )
        parser.add_argument(
            "--realm",
            type=str,
            default="sme-apps",
            help="Realm Keycloak de destino. Padrão: sme-apps.",
        )
        parser.add_argument(
            "--data-referencia",
            type=str,
            default=None,
            help="Data ISO (YYYY-MM-DD) para replay, sobrepõe o watermark.",
        )
        parser.add_argument(
            "--lote-maximo",
            type=int,
            default=None,
            help=(
                "Teto de registros extraídos por fonte nesta execução "
                "(sobrepõe ETL_LOTE_MAXIMO só para este comando)."
            ),
        )

    def handle(self, *args, **options):
        """Cria a execução e dispara o pipeline."""
        if options["lote_maximo"] is not None:
            settings.ETL_LOTE_MAXIMO = options["lote_maximo"]

        from apps.controle_etl.tasks import (  # noqa: PLC0415
            task_identidade_executar_pipeline,
        )

        execucao = ExecucaoETL.objects.create(
            fonte=options["fonte"],
            realm_destino=options["realm"],
            tipo_disparo=ExecucaoETL.TipoDisparo.MANUAL,
        )

        modo = (
            "síncrono (CELERY_TASK_ALWAYS_EAGER=1)"
            if settings.CELERY_TASK_ALWAYS_EAGER
            else "assíncrono — requer worker Celery rodando"
        )
        self.stdout.write(
            f"Execução {execucao.id_execucao} criada"
            f" | fonte={options['fonte']} realm={options['realm']}"
            f" | lote_maximo={settings.ETL_LOTE_MAXIMO or 'sem limite'}"
            f" | modo={modo}"
        )

        t0 = time.time()
        task_identidade_executar_pipeline(
            str(execucao.id_execucao),
            data_referencia=options["data_referencia"],
        )

        if not settings.CELERY_TASK_ALWAYS_EAGER:
            self.stdout.write(
                self.style.WARNING(
                    "Pipeline disparado de forma assíncrona — acompanhe"
                    ' com: python manage.py shell -c "from'
                    " apps.controle_etl.models import ExecucaoETL;"
                    f" e = ExecucaoETL.objects.get(id_execucao="
                    f"'{execucao.id_execucao}'); print(e.situacao)\""
                )
            )
            return

        execucao.refresh_from_db()
        self.stdout.write(
            self.style.SUCCESS(
                f"\nConcluído em {time.time() - t0:.1f}s"
                f" | situacao={execucao.situacao}"
                f" | extraidos={execucao.total_extraido}"
                f" | carregados={execucao.total_carregado}"
                f" | erros={execucao.total_erros}"
                f" | ignorados={execucao.total_ignorados}"
            )
        )

        etapas = execucao.etapas.order_by("ordem_etapa")
        if not etapas:
            return

        self.stdout.write("\nEtapas:")
        for etapa in etapas:
            linha = (
                f"  {etapa.ordem_etapa}. {etapa.nome_etapa}:"
                f" {etapa.situacao}"
            )
            if etapa.detalhe_erro:
                linha += f" — {etapa.detalhe_erro[:140]}"
            self.stdout.write(linha)

        if execucao.situacao == ExecucaoETL.Situacao.FALHA:
            raise CommandError("Pipeline finalizado com falha.")
