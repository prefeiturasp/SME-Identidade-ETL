"""Comando Django para validar o pipeline ponta a ponta em ambiente real."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.controle_etl.models import ExecucaoETL
from apps.controle_etl.orquestrador_kc import (
    _resolver_username,
    obter_admin_keycloak,
)
from apps.controle_etl.tasks import (
    task_identidade_extrair_coresso,
    task_identidade_extrair_eol_alunos,
    task_identidade_extrair_se1426,
    task_identidade_resolver_identidade,
    task_provisionar_identidade_keycloak,
)
from apps.staging.models import (
    UsuarioAlunoStaging,
    UsuarioServidorStaging,
    UsuarioTerceiroStaging,
)

_FONTES_MODELO = (
    ("se1426", UsuarioServidorStaging),
    ("coresso", UsuarioTerceiroStaging),
    ("eol_alunos", UsuarioAlunoStaging),
)


def _identificador_exibicao(usuario: Any) -> str:
    """Retorna o identificador mais legível do usuário para relatório.

    Não é necessariamente o username usado no Keycloak — para isso
    ver ``apps.controle_etl.orquestrador_kc._resolver_username``.

    Args:
        usuario: Instância de staging (servidor, aluno ou terceiro).

    Returns:
        RF, CPF ou matrícula, na primeira ordem em que existir.
    """
    return (
        getattr(usuario, "rf", None)
        or usuario.cpf
        or getattr(usuario, "matricula", None)
        or "—"
    )


class Command(BaseCommand):
    """Executa extração → resolução → Keycloak com volume reduzido.

    Roda o pipeline manualmente, etapa por etapa (sem Celery), contra
    as fontes reais configuradas no ``.env`` e contra o Keycloak real
    do realm informado. Não dispara ``task_carregar_atributos_token``
    nem ``task_sync_rec_etl`` — o foco é validar extração, auditoria
    (ExecucaoETL/LogEtapaETL) e provisionamento Keycloak.

    Ao final, valida uma amostra de usuários direto na API do
    Keycloak e gera um relatório Markdown (``validacao.md``) com os
    totais por etapa e os registros extraídos de cada fonte, para
    conferência manual posterior contra os sistemas de origem.
    """

    help = (
        "Valida o pipeline ponta a ponta (extração → resolução → "
        "Keycloak) com volume reduzido e gera um relatório em "
        "Markdown para conferência manual."
    )

    def add_arguments(self, parser: Any) -> None:
        """Define os argumentos do comando."""
        parser.add_argument(
            "--lote-maximo",
            type=int,
            default=15,
            help="Teto de registros extraídos por fonte. Padrão: 15.",
        )
        parser.add_argument(
            "--realm",
            type=str,
            default="sme-apps",
            help="Realm Keycloak de destino. Padrão: sme-apps.",
        )
        parser.add_argument(
            "--saida",
            type=str,
            default="validacao.md",
            help="Caminho do relatório gerado. Padrão: validacao.md.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=None,
            help=(
                "Sobrepõe ETL_CHUNK_SIZE apenas nesta execução — útil "
                "para reduzir o custo de fetchmany() em testes com "
                "volume pequeno contra tabelas grandes (ex.: CoreSSO)."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Executa o pipeline reduzido e grava o relatório."""
        lote_maximo = options["lote_maximo"]
        realm = options["realm"]
        caminho_saida = Path(options["saida"])

        settings.ETL_LOTE_MAXIMO = lote_maximo
        if options["chunk_size"]:
            settings.ETL_CHUNK_SIZE = options["chunk_size"]

        execucao = ExecucaoETL.objects.create(
            fonte="todos",
            realm_destino=realm,
            tipo_disparo=ExecucaoETL.TipoDisparo.MANUAL,
        )
        execucao.marcar_executando()
        id_exec = str(execucao.id_execucao)
        self.stdout.write(
            f"ExecucaoETL criada: {id_exec} | lote_maximo={lote_maximo}"
            f" | realm={realm}"
        )

        resultados_extracao = []
        for nome, task in (
            ("SE1426", task_identidade_extrair_se1426),
            ("CoreSSO", task_identidade_extrair_coresso),
            ("EOL_DB (alunos)", task_identidade_extrair_eol_alunos),
        ):
            self.stdout.write(f"\n--- Extração {nome} ---")
            t0 = time.monotonic()
            resultado = task(id_execucao=id_exec)
            self.stdout.write(f"{resultado} ({time.monotonic() - t0:.1f}s)")
            resultados_extracao.append(resultado)

        self.stdout.write("\n--- Resolução / Dedup ---")
        resultado_resolucao = task_identidade_resolver_identidade(
            resultados_extracao, id_execucao=id_exec
        )
        self.stdout.write(str(resultado_resolucao))

        self.stdout.write("\n--- Provisionamento Keycloak ---")
        resultado_kc = task_provisionar_identidade_keycloak(
            resultado_resolucao, id_execucao=id_exec
        )
        self.stdout.write(str(resultado_kc))

        execucao.refresh_from_db()
        situacao_final = (
            ExecucaoETL.Situacao.FALHA
            if execucao.total_erros
            else ExecucaoETL.Situacao.SUCESSO
        )
        execucao.marcar_finalizada(situacao_final)
        self.stdout.write(
            self.style.SUCCESS(
                f"\nPipeline concluído | situacao={execucao.situacao}"
                f" | transformado={execucao.total_transformado}"
                f" | carregado={execucao.total_carregado}"
                f" | erros={execucao.total_erros}"
            )
        )

        self.stdout.write("\n--- Validação no Keycloak ---")
        validacoes_kc = self._validar_amostra_keycloak(id_exec, realm)

        self._gravar_relatorio(caminho_saida, execucao, id_exec, validacoes_kc)
        self.stdout.write(
            self.style.SUCCESS(f"\nRelatório gerado em {caminho_saida}")
        )

    def _validar_amostra_keycloak(
        self, id_exec: str, realm: str
    ) -> dict[int, bool]:
        """Confirma na API do Keycloak quais usuários foram criados.

        Busca cada usuário da execução pelo mesmo username que o
        orquestrador usa no provisionamento (CPF > RF > matrícula —
        ver ``apps.controle_etl.orquestrador_kc._resolver_username``),
        garantindo que a validação reflita a regra real de upsert.

        Args:
            id_exec: UUID da ExecucaoETL validada.
            realm: Realm Keycloak consultado.

        Returns:
            Mapa ``usuario.id -> encontrado_no_keycloak``.
        """
        admin = obter_admin_keycloak(realm=realm)
        confirmados_por_id: dict[int, bool] = {}

        for fonte, modelo in _FONTES_MODELO:
            usuarios = list(modelo.objects.filter(id_execucao=id_exec))
            confirmados = 0
            for usuario in usuarios:
                username = _resolver_username(usuario)
                usuarios_kc = admin.get_users(
                    query={"username": username, "exact": True}
                )
                encontrado = bool(usuarios_kc)
                confirmados_por_id[
                    usuario.id  # type: ignore[attr-defined]
                ] = encontrado
                confirmados += int(encontrado)

            self.stdout.write(
                f"{fonte}: {confirmados}/{len(usuarios)} confirmados"
                " no Keycloak"
            )

        return confirmados_por_id

    def _gravar_relatorio(
        self,
        caminho: Path,
        execucao: ExecucaoETL,
        id_exec: str,
        confirmados_por_id: dict[int, bool],
    ) -> None:
        """Monta o relatório Markdown de validação manual.

        Args:
            caminho: Arquivo de saída.
            execucao: ExecucaoETL já finalizada.
            id_exec: UUID da execução.
            confirmados_por_id: Resultado de
                ``_validar_amostra_keycloak`` (chave: ``usuario.id``).
        """
        linhas = [
            "# Relatório de validação E2E — SME-Identidade-ETL",
            "",
            f"- **Execução:** `{id_exec}`",
            f"- **Gerado em:** {timezone.now():%Y-%m-%d %H:%M:%S}",
            f"- **Realm Keycloak:** {execucao.realm_destino}",
            f"- **Situação final:** {execucao.situacao}",
            "",
            "## Totais por etapa",
            "",
            "| Etapa | Entrada | Saída | Erros |",
            "|---|---|---|---|",
        ]
        etapas = execucao.etapas  # type: ignore[attr-defined]
        for etapa in etapas.order_by("ordem_etapa"):
            linhas.append(
                f"| {etapa.nome_etapa} | {etapa.registros_entrada} "
                f"| {etapa.registros_saida} | {etapa.registros_erro} |"
            )

        linhas += [
            "",
            f"- Extraído: {execucao.total_extraido}",
            f"- Transformado: {execucao.total_transformado}",
            f"- Carregado no Keycloak: {execucao.total_carregado}",
            f"- Erros: {execucao.total_erros}",
            f"- Ignorados: {execucao.total_ignorados}",
            "",
            "## Amostra por fonte (para conferência manual)",
            "",
            "Compare os valores abaixo com o sistema de origem "
            "correspondente (SE1426/CoreSSO/EOL_DB) e com o usuário "
            "no Keycloak. Situação **ignorado** é esperada quando o "
            "usuário já existia de uma execução anterior com o mesmo "
            "conteúdo (upsert idempotente) — não indica falha.",
        ]

        for fonte, modelo in _FONTES_MODELO:
            linhas += [
                "",
                f"### {fonte}",
                "",
                "| RF/CPF/Matrícula | Nome | Situação | "
                "Confirmado no Keycloak |",
                "|---|---|---|---|",
            ]
            for usuario in modelo.objects.filter(id_execucao=id_exec).order_by(
                "id"
            ):
                identificador = _identificador_exibicao(usuario)
                uid = usuario.id  # type: ignore[union-attr]
                confirmado = confirmados_por_id.get(uid, False)
                marca = "✅" if confirmado else "❌"
                linhas.append(
                    f"| {identificador} | {usuario.nome or '—'} "
                    f"| {usuario.situacao or '—'} | {marca} |"
                )

        caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
