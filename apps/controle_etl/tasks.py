"""Celery tasks do pipeline ETL de identidades SME-SP.

Implementa as tasks definidas na especificação arquitetural:
  - task_identidade_extrair_se1426
  - task_identidade_extrair_coresso
  - task_identidade_resolver_identidade
  - task_provisionar_identidade_keycloak
  - task_carregar_atributos_token
  - task_sync_rec_etl
  - task_identidade_limpar_staging
"""

import logging
import time
from typing import Any

from celery import chain, chord, shared_task
from django.utils import timezone

logger = logging.getLogger("etl_identidade")

_ATRASO_BASE_REINTENTO = 60
_ATRASO_MAXIMO_REINTENTO = 600
_TAMANHO_LOTE_PROVISIONAMENTO = 200


def _calcular_atraso(tentativa: int) -> int:
    """Calcula countdown com backoff exponencial.

    Args:
        tentativa: Número da tentativa atual (base 1).

    Returns:
        Segundos de espera antes da próxima tentativa.
    """
    return int(
        min(
            _ATRASO_BASE_REINTENTO * (2 ** (tentativa - 1)),
            _ATRASO_MAXIMO_REINTENTO,
        )
    )


def _registrar_tentativa(
    id_execucao: str,
    nome_tarefa: str,
    numero: int,
    erro: str | None = None,
    duracao: float | None = None,
) -> None:
    """Persistir rastreio de uma tentativa no SYNC_REC_DB."""
    from apps.controle_etl.models import RastreioTentativa

    RastreioTentativa.objects.create(
        id_execucao=id_execucao,
        nome_tarefa=nome_tarefa,
        numero_tentativa=numero,
        erro=erro,
        duracao_segundos=duracao,
    )


def _atualizar_checkpoint(
    id_execucao: str,
    etapa: str,
    pagina: int = 0,
    ultimo_id: str | None = None,
    estado: dict | None = None,
) -> None:
    """Atualiza o checkpoint de retomada para a etapa indicada."""
    from apps.controle_etl.models import CheckpointEtl

    CheckpointEtl.objects.update_or_create(
        id_execucao=id_execucao,
        defaults={
            "etapa": etapa,
            "pagina_atual": pagina,
            "ultimo_id_processado": ultimo_id,
            "estado_json": estado or {},
        },
    )


# ---------------------------------------------------------------------------
# TASK_IDENTIDADE_EXTRAIR_SE1426
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="task_identidade_extrair_se1426",
    max_retries=5,
)
def task_identidade_extrair_se1426(
    self: Any,
    id_execucao: str,
    data_referencia: str | None = None,
) -> dict:
    """Extrai dados institucionais da fonte SE1426.

    Args:
        id_execucao: UUID da ExecucaoETL associada.
        data_referencia: Data ISO para replay
            (sobrepõe o watermark persistido).

    Returns:
        Dicionário com ``total_extraido``.
    """
    from apps.extracao.tasks import extrair_se1426  # noqa: PLC0415
    from apps.staging.tasks import persistir_extracao_staging  # noqa: PLC0415

    inicio = time.monotonic()
    logger.info("[%s] task_identidade_extrair_se1426 — início", id_execucao)
    try:
        registros = extrair_se1426(data_referencia=data_referencia)
        total = persistir_extracao_staging(registros, id_execucao=id_execucao)
        duracao = time.monotonic() - inicio
        _registrar_tentativa(
            id_execucao,
            "task_identidade_extrair_se1426",
            self.request.retries + 1,
            duracao=duracao,
        )
        logger.info(
            "[%s] SE1426 — %d registros (%.1fs)",
            id_execucao,
            total,
            duracao,
        )
        return {"total_extraido": total}
    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        _registrar_tentativa(
            id_execucao,
            "task_identidade_extrair_se1426",
            self.request.retries + 1,
            erro=str(exc),
            duracao=time.monotonic() - inicio,
        )
        logger.warning(
            "[%s] task_identidade_extrair_se1426 — erro"
            " (tentativa %d, próxima em %ds): %s",
            id_execucao,
            self.request.retries + 1,
            atraso,
            exc,
        )
        raise self.retry(exc=exc, countdown=atraso) from exc


# ---------------------------------------------------------------------------
# TASK_IDENTIDADE_EXTRAIR_CORESSO
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="task_identidade_extrair_coresso",
    max_retries=5,
)
def task_identidade_extrair_coresso(
    self: Any,
    id_execucao: str,
    data_referencia: str | None = None,
) -> dict:
    """Extrai usuários e perfis legados da fonte CoreSSO.

    Args:
        id_execucao: UUID da ExecucaoETL associada.
        data_referencia: Data ISO para replay.

    Returns:
        Dicionário com ``total_extraido``.
    """
    from apps.extracao.tasks import extrair_coresso  # noqa: PLC0415
    from apps.staging.tasks import persistir_extracao_staging  # noqa: PLC0415

    inicio = time.monotonic()
    logger.info("[%s] task_identidade_extrair_coresso — início", id_execucao)
    try:
        registros = extrair_coresso(data_referencia=data_referencia)
        total = persistir_extracao_staging(registros, id_execucao=id_execucao)
        duracao = time.monotonic() - inicio
        _registrar_tentativa(
            id_execucao,
            "task_identidade_extrair_coresso",
            self.request.retries + 1,
            duracao=duracao,
        )
        logger.info(
            "[%s] CoreSSO — %d registros (%.1fs)",
            id_execucao,
            total,
            duracao,
        )
        return {"total_extraido": total}
    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        _registrar_tentativa(
            id_execucao,
            "task_identidade_extrair_coresso",
            self.request.retries + 1,
            erro=str(exc),
        )
        raise self.retry(exc=exc, countdown=atraso) from exc


# ---------------------------------------------------------------------------
# TASK_IDENTIDADE_EXTRAIR_EOL_ALUNOS
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="task_identidade_extrair_eol_alunos",
    max_retries=5,
)
def task_identidade_extrair_eol_alunos(
    self: Any,
    id_execucao: str,
    data_referencia: str | None = None,
) -> dict:
    """Extrai alunos da fonte EOL_DB.

    Args:
        id_execucao: UUID da ExecucaoETL associada.
        data_referencia: Data ISO para replay.

    Returns:
        Dicionário com ``total_extraido``.
    """
    from apps.extracao.tasks import extrair_eol_alunos  # noqa: PLC0415
    from apps.staging.tasks import persistir_extracao_staging  # noqa: PLC0415

    inicio = time.monotonic()
    logger.info(
        "[%s] task_identidade_extrair_eol_alunos — início",
        id_execucao,
    )
    try:
        registros = extrair_eol_alunos(data_referencia=data_referencia)
        total = persistir_extracao_staging(registros, id_execucao=id_execucao)
        duracao = time.monotonic() - inicio
        _registrar_tentativa(
            id_execucao,
            "task_identidade_extrair_eol_alunos",
            self.request.retries + 1,
            duracao=duracao,
        )
        logger.info(
            "[%s] task_identidade_extrair_eol_alunos — %d registros"
            " (%.1fs)",
            id_execucao,
            total,
            duracao,
        )
        return {"total_extraido": total}
    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        _registrar_tentativa(
            id_execucao,
            "task_identidade_extrair_eol_alunos",
            self.request.retries + 1,
            erro=str(exc),
        )
        raise self.retry(exc=exc, countdown=atraso) from exc


# ---------------------------------------------------------------------------
# TASK_IDENTIDADE_RESOLVER_IDENTIDADE
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="task_identidade_resolver_identidade",
    max_retries=3,
)
def task_identidade_resolver_identidade(
    self: Any, resultados_extracao: list, id_execucao: str
) -> dict:
    """Resolve identidades: merge, reconciliação, dedup e projeções.

    Executada após o chord de extração. Recebe os resultados
    das tasks de extração como primeiro argumento (padrão chord Celery).

    Args:
        resultados_extracao: Lista de resultados das tasks de extração.
        id_execucao: UUID da ExecucaoETL associada.

    Returns:
        Dicionário com totais de transformação e deduplicação.
    """
    from apps.controle_etl.models import (  # noqa: PLC0415
        ExecucaoETL,
        LogEtapaETL,
    )
    from apps.staging.tasks import (  # noqa: PLC0415
        deduplicar_identidades,
        transformar_staging,
    )

    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    etapa = LogEtapaETL.objects.create(
        execucao=execucao,
        nome_etapa=LogEtapaETL.NomeEtapa.RESOLVER_IDENTIDADE,
        ordem_etapa=3,
    )
    inicio = time.monotonic()
    logger.info(
        "[%s] task_identidade_resolver_identidade — início",
        id_execucao,
    )

    try:
        resultado_transform = transformar_staging(id_execucao=id_execucao)
        _atualizar_checkpoint(
            id_execucao,
            "task_identidade_resolver_identidade",
        )

        resultado_dedup = deduplicar_identidades(
            resultado_transform, id_execucao=id_execucao
        )

        total_transformado = resultado_transform.get("total", 0)
        total_dedup = resultado_dedup.get("total_deduplicado", 0)

        execucao.total_transformado = total_transformado
        execucao.save(update_fields=["total_transformado", "atualizado_em"])

        etapa.registros_entrada = sum(
            (r or {}).get("total_extraido", 0)
            for r in (resultados_extracao or [])
        )
        etapa.registros_saida = total_transformado
        etapa.metadados = {"total_deduplicado": total_dedup}
        etapa.situacao = LogEtapaETL.Situacao.SUCESSO
        etapa.finalizado_em = timezone.now()
        etapa.save()

        logger.info(
            "[%s] task_identidade_resolver_identidade — %d transformados,"
            " %d deduplicados (%.1fs)",
            id_execucao,
            total_transformado,
            total_dedup,
            time.monotonic() - inicio,
        )
        return {
            "total_transformado": total_transformado,
            "total_deduplicado": total_dedup,
        }
    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        etapa.situacao = LogEtapaETL.Situacao.FALHA
        etapa.detalhe_erro = str(exc)
        etapa.finalizado_em = timezone.now()
        etapa.save()
        raise self.retry(exc=exc, countdown=atraso) from exc


# ---------------------------------------------------------------------------
# TASK_PROVISIONAR_IDENTIDADE_KEYCLOAK
# ---------------------------------------------------------------------------


def _provisionar_lote_kc(
    admin: Any,
    lote: list,
    execucao: Any,
    id_execucao: str,
    *,
    forcar_atualizacao: bool = False,
) -> tuple[int, int, int]:
    """Provisiona um lote de usuários no Keycloak em paralelo.

    Args:
        admin: Cliente KeycloakAdmin autenticado.
        lote: Lista de instâncias de staging do usuário.
        execucao: Instância de ExecucaoETL para rastreamento.
        id_execucao: UUID da execução, usado apenas em logs.
        forcar_atualizacao: Força update mesmo sem mudança.

    Returns:
        Tupla ``(provisionados, ignorados, erros)``.
    """
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        provisionar_usuarios_kc_em_paralelo,
    )

    resultados = provisionar_usuarios_kc_em_paralelo(
        admin,
        lote,
        realm=execucao.realm_destino,
        execucao=execucao,
        forcar_atualizacao=forcar_atualizacao,
    )

    provisionados = ignorados = erros = 0
    for usuario, resultado in zip(lote, resultados, strict=False):
        if isinstance(resultado, Exception):
            logger.warning(
                "[%s] KC: erro para %s: %s",
                id_execucao,
                getattr(usuario, "rf", None) or usuario.cpf,
                resultado,
            )
            usuario.situacao = "erro"
            usuario.detalhe_erro = str(resultado)[:1000]
            usuario.save(update_fields=["situacao", "detalhe_erro"])
            erros += 1
        elif resultado["acao"] == "ignorado":
            usuario.situacao = "ignorado"
            usuario.save(update_fields=["situacao"])
            ignorados += 1
        else:
            usuario.situacao = "carregado"
            usuario.save(update_fields=["situacao"])
            provisionados += 1

    return provisionados, ignorados, erros


@shared_task(
    bind=True,
    name="task_provisionar_identidade_keycloak",
    max_retries=3,
)
def task_provisionar_identidade_keycloak(
    self: Any, resultado_resolucao: dict, id_execucao: str
) -> dict:
    """Provisiona identidades no Keycloak via upsert idempotente.

    Args:
        resultado_resolucao: Resultado da task de resolução.
        id_execucao: UUID da ExecucaoETL associada.

    Returns:
        Dicionário com totais de provisionamento.
    """
    from django.conf import settings as conf  # noqa: PLC0415

    from apps.controle_etl.models import (  # noqa: PLC0415
        ExecucaoETL,
        LogEtapaETL,
    )
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        obter_admin_keycloak,
    )
    from apps.staging.models import (  # noqa: PLC0415
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    etapa = LogEtapaETL.objects.create(
        execucao=execucao,
        nome_etapa=LogEtapaETL.NomeEtapa.PROVISIONAR_KEYCLOAK,
        ordem_etapa=4,
    )

    if not conf.ETL_CARGA_KEYCLOAK_BULK_HABILITADO:
        logger.warning(
            "[%s] task_provisionar_identidade_keycloak"
            " — desabilitado por ETL_CARGA_KEYCLOAK_BULK_HABILITADO",
            id_execucao,
        )
        etapa.situacao = LogEtapaETL.Situacao.IGNORADO
        etapa.finalizado_em = timezone.now()
        etapa.metadados = {
            "motivo": "ETL_CARGA_KEYCLOAK_BULK_HABILITADO=false"
        }
        etapa.save()
        return {"total_provisionado": 0, "ignorado": True}

    inicio = time.monotonic()
    logger.info(
        "[%s] task_provisionar_identidade_keycloak — realm=%s",
        id_execucao,
        execucao.realm_destino,
    )

    try:
        admin = obter_admin_keycloak(realm=execucao.realm_destino)
        provisionados = erros = ignorados = total = 0

        for modelo in (
            UsuarioServidorStaging,
            UsuarioAlunoStaging,
            UsuarioTerceiroStaging,
        ):
            qs = modelo.objects.filter(
                id_execucao=id_execucao, situacao="pronto"
            )
            total += qs.count()

            lote: list = []
            for usuario in qs.iterator(
                chunk_size=_TAMANHO_LOTE_PROVISIONAMENTO
            ):
                lote.append(usuario)
                if len(lote) >= _TAMANHO_LOTE_PROVISIONAMENTO:
                    p, i, e = _provisionar_lote_kc(
                        admin, lote, execucao, id_execucao
                    )
                    provisionados += p
                    ignorados += i
                    erros += e
                    lote = []
            if lote:
                p, i, e = _provisionar_lote_kc(
                    admin, lote, execucao, id_execucao
                )
                provisionados += p
                ignorados += i
                erros += e

        etapa.registros_entrada = total
        etapa.registros_saida = provisionados
        etapa.registros_erro = erros
        etapa.metadados = {"ignorados": ignorados}
        etapa.situacao = (
            LogEtapaETL.Situacao.SUCESSO
            if erros == 0
            else LogEtapaETL.Situacao.FALHA
        )
        etapa.finalizado_em = timezone.now()
        etapa.save()

        execucao.total_carregado = provisionados
        execucao.total_ignorados = (execucao.total_ignorados or 0) + ignorados
        execucao.save(
            update_fields=[
                "total_carregado",
                "total_ignorados",
                "atualizado_em",
            ]
        )

        logger.info(
            "[%s] task_provisionar_identidade_keycloak"
            " — %d provisionados, %d ignorados, %d erros (%.1fs)",
            id_execucao,
            provisionados,
            ignorados,
            erros,
            time.monotonic() - inicio,
        )
        return {
            "total_provisionado": provisionados,
            "total_erros": erros,
        }

    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        etapa.situacao = LogEtapaETL.Situacao.FALHA
        etapa.detalhe_erro = str(exc)
        etapa.finalizado_em = timezone.now()
        etapa.save()
        raise self.retry(exc=exc, countdown=atraso) from exc


# ---------------------------------------------------------------------------
# TASK_CARREGAR_ATRIBUTOS_TOKEN
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="task_carregar_atributos_token",
    max_retries=3,
)
def task_carregar_atributos_token(
    self: Any, resultado_provisionamento: dict, id_execucao: str
) -> dict:
    """Publica atributos complementares para o token-ms.

    Args:
        resultado_provisionamento: Resultado da task de provisionamento.
        id_execucao: UUID da ExecucaoETL associada.

    Returns:
        Dicionário com ``enviados`` e ``lotes``.
    """
    from apps.controle_etl.cliente_token_ms import (  # noqa: PLC0415
        enviar_todos,
    )
    from apps.controle_etl.models import (  # noqa: PLC0415
        ExecucaoETL,
        LogEtapaETL,
    )
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        construir_payload_token_ms,
    )
    from apps.staging.models import (  # noqa: PLC0415
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    etapa = LogEtapaETL.objects.create(
        execucao=execucao,
        nome_etapa=LogEtapaETL.NomeEtapa.CARREGAR_TOKEN,
        ordem_etapa=5,
    )
    inicio = time.monotonic()
    logger.info("[%s] task_carregar_atributos_token — início", id_execucao)

    try:

        def _payloads() -> Any:
            for modelo in (
                UsuarioServidorStaging,
                UsuarioAlunoStaging,
                UsuarioTerceiroStaging,
            ):
                qs = modelo.objects.filter(
                    id_execucao=id_execucao,
                    situacao__in=["pronto", "carregado"],
                )
                for u in qs.iterator(chunk_size=1000):
                    yield construir_payload_token_ms(u)

        metricas = enviar_todos(_payloads(), id_execucao=id_execucao)

        etapa.registros_entrada = metricas["enviados"]
        etapa.registros_saida = metricas["enviados"]
        etapa.metadados = {"lotes": metricas["lotes"]}
        etapa.situacao = LogEtapaETL.Situacao.SUCESSO
        etapa.finalizado_em = timezone.now()
        etapa.save()

        logger.info(
            "[%s] task_carregar_atributos_token"
            " — %d usuários em %d lotes (%.1fs)",
            id_execucao,
            metricas["enviados"],
            metricas["lotes"],
            time.monotonic() - inicio,
        )
        return metricas

    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        etapa.situacao = LogEtapaETL.Situacao.FALHA
        etapa.detalhe_erro = str(exc)[:2000]
        etapa.finalizado_em = timezone.now()
        etapa.save()
        raise self.retry(exc=exc, countdown=atraso) from exc


# ---------------------------------------------------------------------------
# TASK_SYNC_REC_ETL
# ---------------------------------------------------------------------------


@shared_task(bind=True, name="task_sync_rec_etl")
def task_sync_rec_etl(
    self: Any, resultado_token: dict, id_execucao: str
) -> dict:
    """Registra metadados operacionais e finaliza a execução.

    Consolida watermarks, checkpoints e métricas finais no SYNC_REC_DB.

    Args:
        resultado_token: Resultado da task de carga no token-ms.
        id_execucao: UUID da ExecucaoETL associada.

    Returns:
        Dicionário com ``situacao`` e ``duracao_segundos``.
    """
    from apps.controle_etl.models import (  # noqa: PLC0415
        CheckpointEtl,
        ExecucaoETL,
        LogEtapaETL,
    )

    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    etapa = LogEtapaETL.objects.create(
        execucao=execucao,
        nome_etapa=LogEtapaETL.NomeEtapa.SYNC_REC,
        ordem_etapa=6,
    )
    logger.info("[%s] task_sync_rec_etl — início", id_execucao)

    try:
        etapas = execucao.etapas.all()  # type: ignore[attr-defined]
        total_erros = sum(e.registros_erro for e in etapas)
        tem_falhas = etapas.filter(
            situacao=LogEtapaETL.Situacao.FALHA
        ).exists()

        execucao.total_erros = total_erros

        situacao_final = "parcial" if tem_falhas else "sucesso"
        execucao.marcar_finalizada(situacao_final)

        # Remove checkpoint após conclusão bem-sucedida
        CheckpointEtl.objects.filter(id_execucao=id_execucao).delete()

        etapa.situacao = LogEtapaETL.Situacao.SUCESSO
        etapa.finalizado_em = timezone.now()
        etapa.metadados = {
            "total_etapas": etapas.count(),
            "etapas_com_falha": etapas.filter(situacao="falha").count(),
            "duracao_segundos": execucao.duracao_segundos,
        }
        etapa.save()

        logger.info(
            "[%s] task_sync_rec_etl — pipeline finalizado:"
            " situacao=%s, extraidos=%d, carregados=%d,"
            " erros=%d, duracao=%.1fs",
            id_execucao,
            execucao.situacao,
            execucao.total_extraido,
            execucao.total_carregado,
            execucao.total_erros,
            execucao.duracao_segundos or 0,
        )

        task_identidade_limpar_staging.apply_async(countdown=30)

        return {
            "situacao": execucao.situacao,
            "duracao_segundos": execucao.duracao_segundos,
        }

    except Exception as exc:
        logger.exception(
            "[%s] task_sync_rec_etl — FALHA: %s", id_execucao, exc
        )
        etapa.situacao = LogEtapaETL.Situacao.FALHA
        etapa.detalhe_erro = str(exc)
        etapa.finalizado_em = timezone.now()
        etapa.save()
        execucao.marcar_finalizada("falha")
        return {"situacao": "falha"}


# ---------------------------------------------------------------------------
# Orquestrador principal do pipeline
# ---------------------------------------------------------------------------


@shared_task(bind=True, name="task_identidade_executar_pipeline")
def task_identidade_executar_pipeline(
    self: Any,
    id_execucao: str,
    data_referencia: str | None = None,
) -> None:
    """Orquestra o pipeline completo de ingestão de identidades.

    Executa extração paralela via chord, seguida da cadeia
    de resolução → provisionamento → token → registro operacional.

    Args:
        id_execucao: UUID da ExecucaoETL associada.
        data_referencia: Data ISO opcional para replay.
    """
    from apps.controle_etl.models import ExecucaoETL  # noqa: PLC0415

    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    execucao.marcar_executando()

    logger.info(
        "[%s] Pipeline de identidade iniciado — fonte=%s realm=%s",
        id_execucao,
        execucao.fonte,
        execucao.realm_destino,
    )

    kwargs = {"id_execucao": id_execucao}
    if data_referencia:
        kwargs["data_referencia"] = data_referencia

    tarefas_extracao = []
    fonte = execucao.fonte

    if fonte in ("todos", "se1426"):
        tarefas_extracao.append(task_identidade_extrair_se1426.s(**kwargs))
    if fonte in ("todos", "coresso"):
        tarefas_extracao.append(task_identidade_extrair_coresso.s(**kwargs))
    if fonte in ("todos", "eol_alunos"):
        tarefas_extracao.append(task_identidade_extrair_eol_alunos.s(**kwargs))

    if not tarefas_extracao:
        execucao.marcar_finalizada("falha")
        logger.error(
            "[%s] Nenhuma tarefa de extração para fonte=%s",
            id_execucao,
            fonte,
        )
        return

    chord(tarefas_extracao)(
        chain(
            task_identidade_resolver_identidade.s(id_execucao=id_execucao),
            task_provisionar_identidade_keycloak.s(id_execucao=id_execucao),
            task_carregar_atributos_token.s(id_execucao=id_execucao),
            task_sync_rec_etl.s(id_execucao=id_execucao),
        )
    )


# ---------------------------------------------------------------------------
# TASK_IDENTIDADE_LIMPAR_STAGING
# ---------------------------------------------------------------------------


@shared_task(name="task_identidade_limpar_staging")
def task_identidade_limpar_staging(manter_ultimas: int = 2) -> None:
    """Remove registros de staging de execuções antigas.

    Args:
        manter_ultimas: Número de execuções recentes a preservar.
    """
    from apps.controle_etl.models import ExecucaoETL  # noqa: PLC0415
    from apps.staging.models import (  # noqa: PLC0415
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    ids_manter = list(
        ExecucaoETL.objects.filter(
            situacao__in=["sucesso", "parcial", "executando", "pendente"]
        )
        .order_by("-finalizado_em")
        .values_list("id_execucao", flat=True)[: manter_ultimas + 5]
    )

    if not ids_manter:
        return

    total_removido = 0
    for modelo in (
        UsuarioServidorStaging,
        UsuarioAlunoStaging,
        UsuarioTerceiroStaging,
    ):
        removidos, _ = modelo.objects.exclude(
            id_execucao__in=ids_manter
        ).delete()
        total_removido += removidos

    if total_removido:
        logger.info(
            "Limpeza de staging: %d registros removidos"
            " (mantidas %d execuções)",
            total_removido,
            len(ids_manter),
        )
