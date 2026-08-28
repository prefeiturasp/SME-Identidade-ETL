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
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from celery import chain, chord, shared_task
from django.utils import timezone

logger = logging.getLogger("etl_identidade")

_ATRASO_BASE_REINTENTO = 60
_ATRASO_MAXIMO_REINTENTO = 600
_TAMANHO_LOTE_PROVISIONAMENTO = 200
_MAX_WORKERS_ENVIO_PERFIL = 8


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
# TASK_REPROCESSAR_PENDENCIAS
# ---------------------------------------------------------------------------

_TAMANHO_LOTE_REPROCESSAMENTO = 200


@shared_task(
    bind=True,
    name="task_reprocessar_pendencias",
    max_retries=5,
    soft_time_limit=1800,
    time_limit=1860,
)
def task_reprocessar_pendencias(
    self: Any,
    id_execucao: str,
    data_referencia: str | None = None,
) -> dict:
    """Garante que clientes com `token_ms_pendente=True` sejam reenviados.

    Roda como mais uma tarefa de extração do pipeline padrão (não é
    uma ferramenta externa): toda execução verifica
    ``ControleProvisionamento`` — a fonte de verdade de idempotência
    por cliente, independente de qual execução processou cada um por
    último — e garante que ninguém fique "preso" com hash_keycloak
    atualizado mas hash_token_ms desatualizado (caso observado quando
    a fila de token-ms de uma execução anterior não terminou de
    drenar). Se o staging do cliente pendente já foi removido pela
    limpeza periódica (``task_identidade_limpar_staging``), reextrai
    pontualmente do CoreSSO em lote (não um SELECT por pessoa) via
    ``_extrair_coresso_por_identificadores``.

    Reaproveita o staging desta própria execução: os itens
    reextraídos entram com ``situacao="extraido"`` e seguem o mesmo
    caminho normal (``transformar_staging``/``deduplicar_identidades``
    → ``task_provisionar_identidade_keycloak``) — nenhuma lógica de
    decisão de hash é duplicada aqui.

    Args:
        id_execucao: UUID da ExecucaoETL associada.
        data_referencia: Aceito por compatibilidade de assinatura com
            as demais tasks de extração do chord; não utilizado (a
            pendência não depende de janela de data).

    Returns:
        Dicionário com ``total_pendentes`` e ``total_reextraido``.
    """
    from apps.controle_etl.models import ControleProvisionamento

    inicio = time.monotonic()
    logger.info("[%s] task_reprocessar_pendencias — início", id_execucao)

    try:
        total_pendentes = 0
        total_reextraido = 0

        qs_pendentes = ControleProvisionamento.objects.filter(
            token_ms_pendente=True,
            sistema_origem="coresso",
        ).values_list("id_origem", flat=True)

        lote: list[str] = []
        for id_origem in qs_pendentes.iterator(
            chunk_size=_TAMANHO_LOTE_REPROCESSAMENTO
        ):
            total_pendentes += 1
            lote.append(id_origem)
            if len(lote) >= _TAMANHO_LOTE_REPROCESSAMENTO:
                total_reextraido += _reextrair_pendentes_coresso(
                    lote, id_execucao
                )
                lote = []
        if lote:
            total_reextraido += _reextrair_pendentes_coresso(lote, id_execucao)

        duracao = time.monotonic() - inicio
        _registrar_tentativa(
            id_execucao,
            "task_reprocessar_pendencias",
            self.request.retries + 1,
            duracao=duracao,
        )
        logger.info(
            "[%s] task_reprocessar_pendencias — %d pendentes,"
            " %d reextraídos (%.1fs)",
            id_execucao,
            total_pendentes,
            total_reextraido,
            duracao,
        )
        return {
            "total_pendentes": total_pendentes,
            "total_reextraido": total_reextraido,
        }
    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        _registrar_tentativa(
            id_execucao,
            "task_reprocessar_pendencias",
            self.request.retries + 1,
            erro=str(exc),
        )
        raise self.retry(exc=exc, countdown=atraso) from exc


def _reextrair_pendentes_coresso(
    identificadores: list[str], id_execucao: str
) -> int:
    """Reextrai e persiste um lote de CPFs/RF pendentes como staging novo.

    Só cobre CoreSSO nesta versão — é a fonte onde a perda de rastro
    foi observada. Identificadores sem correspondência no CoreSSO
    (ex.: pendência de servidor puro SE1426, ou cliente removido na
    origem) simplesmente não retornam nada aqui e continuam com
    ``token_ms_pendente=True`` até a próxima extração completa da
    fonte correta trazê-los de volta.
    """
    from apps.extracao.tasks import (  # noqa: PLC0415
        _extrair_coresso_por_identificadores,
    )
    from apps.staging.tasks import persistir_extracao_staging  # noqa: PLC0415

    registros = _extrair_coresso_por_identificadores(identificadores)
    return persistir_extracao_staging(registros, id_execucao=id_execucao)


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
    etapa, _ = LogEtapaETL.objects.update_or_create(
        execucao=execucao,
        nome_etapa=LogEtapaETL.NomeEtapa.RESOLVER_IDENTIDADE,
        defaults={"ordem_etapa": 3},
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
        total_extraido = sum(
            (r or {}).get("total_extraido", 0)
            for r in (resultados_extracao or [])
        )

        execucao.total_extraido = total_extraido
        execucao.total_transformado = total_transformado
        execucao.save(
            update_fields=[
                "total_extraido",
                "total_transformado",
                "atualizado_em",
            ]
        )

        etapa.registros_entrada = total_extraido
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
    itens_token_ms: list[dict] = []
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
            continue

        if resultado["acao"] == "ignorado":
            usuario.situacao = "ignorado"
            usuario.save(update_fields=["situacao"])
            ignorados += 1
        else:
            usuario.situacao = "carregado"
            usuario.save(update_fields=["situacao"])
            provisionados += 1

        # Sucesso (ou ignorado por hash já confirmado) no Keycloak
        # acumula o usuário para a carga em lote do token-ms — só
        # quando o payload do token-ms mudou desde a última
        # confirmação (3ª checagem independente).
        if resultado.get("token_ms_pendente"):
            itens_token_ms.append(
                {
                    "modelo_staging": type(usuario).__name__,
                    "staging_pk": usuario.pk,
                    "tipo_entidade": "usuario",
                    "sistema_origem": usuario.fonte,
                    "id_origem": resultado["id_origem"],
                    "kc_user_id": resultado.get("kc_user_id"),
                }
            )

    if itens_token_ms:
        # Sem `queue=` explícito: deixa o roteamento padrão do Celery
        # resolver a fila, incluindo um eventual prefixo de isolamento
        # entre workers que compartilham o mesmo broker — passar
        # `queue=` aqui sempre teria prioridade sobre a rota
        # configurada e ignoraria esse prefixo.
        task_carregar_lote_atributos_token.apply_async(
            kwargs={
                "itens": itens_token_ms,
                "id_execucao": id_execucao,
                "realm_destino": execucao.realm_destino,
            },
        )

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
        limpar_cache_roles_grupos_kc,
        obter_admin_keycloak,
    )
    from apps.staging.models import (  # noqa: PLC0415
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    etapa, _ = LogEtapaETL.objects.update_or_create(
        execucao=execucao,
        nome_etapa=LogEtapaETL.NomeEtapa.PROVISIONAR_KEYCLOAK,
        defaults={"ordem_etapa": 4},
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
    limpar_cache_roles_grupos_kc()

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


def _sincronizar_perfis_execucao(
    id_execucao: str, realm_destino: str | None
) -> dict:
    """Sincroniza a projeção de perfis de cada usuário da execução.

    Falha ao sincronizar o perfil de um usuário não impede os demais
    — cada chamada ``PUT /perfis/{kc_user_id}/`` é isolada, então um
    erro pontual (ex.: usuário sem vínculos CoreSSO) só é contado e
    logado, sem interromper o restante do lote nem propagar para o
    push-batch de atributos complementares (responsabilidade distinta
    já concluída antes desta chamada).

    Args:
        id_execucao: UUID da ExecucaoETL associada.
        realm_destino: Realm Keycloak da execução.

    Returns:
        Dicionário com ``sincronizados`` e ``erros``.
    """
    from apps.controle_etl.cliente_token_ms import enviar_perfil
    from apps.controle_etl.orquestrador_kc import (
        construir_payload_perfil_token_ms,
        obter_admin_keycloak,
        resolver_kc_user_id_de_usuario,
    )
    from apps.staging.models import (
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    admin = obter_admin_keycloak(realm=realm_destino)
    cache_kc_user_id: dict[str, str | None] = {}
    sincronizados = erros = 0

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
            kc_user_id = resolver_kc_user_id_de_usuario(
                admin, u, cache_kc_user_id
            )
            payload_perfil = (
                construir_payload_perfil_token_ms(u) if kc_user_id else None
            )
            if not kc_user_id or payload_perfil is None:
                continue

            try:
                enviar_perfil(kc_user_id, payload_perfil)
                sincronizados += 1
            except Exception:
                erros += 1
                logger.exception(
                    "[%s] falha ao sincronizar perfil de %s",
                    id_execucao,
                    kc_user_id,
                )

    return {"sincronizados": sincronizados, "erros": erros}


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
        payload_tem_identificador,
    )
    from apps.staging.models import (  # noqa: PLC0415
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    etapa, _ = LogEtapaETL.objects.update_or_create(
        execucao=execucao,
        nome_etapa=LogEtapaETL.NomeEtapa.CARREGAR_TOKEN,
        defaults={"ordem_etapa": 5},
    )
    inicio = time.monotonic()
    logger.info("[%s] task_carregar_atributos_token — início", id_execucao)

    try:
        sem_identificador = 0

        def _payloads() -> Any:
            nonlocal sem_identificador
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
                    payload = construir_payload_token_ms(u)
                    if not payload_tem_identificador(payload):
                        sem_identificador += 1
                        logger.warning(
                            "[%s] task_carregar_atributos_token —"
                            " descartado sem rf/cpf/matricula: %s",
                            id_execucao,
                            payload.get("nome"),
                        )
                        continue
                    yield payload

        # Sincroniza perfis antes do push-batch: cria/atualiza a
        # ProjecaoUsuario no token-ms para que o vínculo oportunista
        # de AtributoComplementarUsuario (feito no push-batch, por
        # rf/cpf) já a encontre, em vez de ficar usuario=None.
        metricas_perfis = _sincronizar_perfis_execucao(
            id_execucao, execucao.realm_destino
        )
        metricas = enviar_todos(_payloads(), id_execucao=id_execucao)
        metricas["perfis_sincronizados"] = metricas_perfis["sincronizados"]
        metricas["perfis_erros"] = metricas_perfis["erros"]
        metricas["sem_identificador"] = sem_identificador

        etapa.registros_entrada = metricas["enviados"] + sem_identificador
        etapa.registros_saida = metricas["enviados"]
        etapa.registros_erro = sem_identificador
        etapa.metadados = {
            "lotes": metricas["lotes"],
            "perfis_sincronizados": metricas["perfis_sincronizados"],
            "perfis_erros": metricas["perfis_erros"],
            "sem_identificador": sem_identificador,
        }
        etapa.situacao = LogEtapaETL.Situacao.SUCESSO
        etapa.finalizado_em = timezone.now()
        etapa.save()

        logger.info(
            "[%s] task_carregar_atributos_token"
            " — %d usuários em %d lotes,"
            " %d perfis sincronizados (%d erros),"
            " %d descartados sem identificador (%.1fs)",
            id_execucao,
            metricas["enviados"],
            metricas["lotes"],
            metricas["perfis_sincronizados"],
            metricas["perfis_erros"],
            sem_identificador,
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
# TASK_CARREGAR_ATRIBUTO_TOKEN_INDIVIDUAL
# ---------------------------------------------------------------------------


def _resolver_modelo_staging(nome: str) -> Any:
    """Resolve a classe de model de staging a partir do nome.

    As tasks Celery recebem o nome do model como string (serialização
    JSON não suporta classes) — este lookup evita importar os 3
    models no topo do módulo só para esse fim.
    """
    from apps.staging import models as modelos_staging

    return getattr(modelos_staging, nome)


@shared_task(
    bind=True,
    name="task_carregar_atributo_token_individual",
    max_retries=5,
)
def task_carregar_atributo_token_individual(
    self: Any,
    *,
    modelo_staging: str,
    staging_pk: int,
    id_execucao: str,
    tipo_entidade: str,
    sistema_origem: str,
    id_origem: str,
    realm_destino: str,
    kc_user_id: str | None = None,
) -> dict:
    """Carrega atributos complementares e perfil de UM registro.

    Disparada fire-and-forget pelo sucesso do Keycloak para este MESMO
    registro (dentro de ``_provisionar_lote_kc``), sem esperar o
    restante do lote — nunca dispara sem o Keycloak já ter resolvido
    ``kc_user_id``, pois o payload do token-ms depende dele.

    Verifica ``hash_token_ms`` antes de enviar: se já bate com o
    payload atual, é no-op (idempotência). Se o registro de staging já
    tiver sido limpo (``task_identidade_limpar_staging``, poucas
    execuções depois) no momento de um retry tardio, a task falha e o
    retry esgota dentro da janela de backoff (~10min) — sempre menor
    que a retenção do staging; uma falha definitiva depois disso só se
    resolve numa próxima execução completa do pipeline.

    Args:
        modelo_staging: Nome da classe de staging (ex.:
            ``"UsuarioServidorStaging"``).
        staging_pk: PK do registro de staging.
        id_execucao: UUID da ExecucaoETL associada.
        tipo_entidade: Um de ``ControleProvisionamento.TipoEntidade``.
        sistema_origem: Fonte do registro (se1426, coresso, eol_alunos).
        id_origem: CPF/RF/matrícula que identifica o registro na fonte.
        realm_destino: Realm Keycloak de destino.
        kc_user_id: UUID do usuário no Keycloak, já resolvido pelo
            chamador (``_provisionar_lote_kc`` o obtém ao provisionar
            no Keycloak, segundos antes de disparar esta task) — evita
            uma busca HTTP redundante por username. Se ``None`` (ex.:
            retry de uma task antiga, disparada antes deste parâmetro
            existir), cai no fallback de resolver via busca.

    Returns:
        Dicionário com ``situacao`` (``sucesso``, ``ignorado`` ou
        ``sem_identificador``).
    """
    from apps.controle_etl.cliente_token_ms import (  # noqa: PLC0415
        enviar_lote,
        enviar_perfil,
    )
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        _payload_token_ms_hash,
        calcular_hash_conteudo,
        construir_payload_perfil_token_ms,
        construir_payload_token_ms,
        obter_admin_keycloak,
        obter_ou_criar_controle,
        payload_tem_identificador,
        resolver_kc_user_id_de_usuario,
    )

    modelo = _resolver_modelo_staging(modelo_staging)
    usuario = modelo.objects.get(pk=staging_pk)

    payload_token = construir_payload_token_ms(usuario)
    if not payload_tem_identificador(payload_token):
        logger.warning(
            "[%s] task_carregar_atributo_token_individual —"
            " descartado sem rf/cpf/matricula: %s",
            id_execucao,
            payload_token.get("nome"),
        )
        return {"situacao": "sem_identificador"}

    hash_token_atual = calcular_hash_conteudo(_payload_token_ms_hash(usuario))
    controle = obter_ou_criar_controle(
        tipo_entidade, sistema_origem, id_origem, realm_destino
    )

    if controle.hash_token_ms == hash_token_atual:
        if controle.token_ms_pendente:
            controle.token_ms_pendente = False
            controle.save(update_fields=["token_ms_pendente"])
        return {"situacao": "ignorado"}

    inicio = time.monotonic()
    try:
        if kc_user_id:
            kc_user_id_resolvido = kc_user_id
        else:
            admin = obter_admin_keycloak(realm=realm_destino)
            kc_user_id_resolvido = resolver_kc_user_id_de_usuario(
                admin, usuario, {}
            )

        if kc_user_id_resolvido:
            payload_perfil = construir_payload_perfil_token_ms(usuario)
            if payload_perfil is not None:
                enviar_perfil(kc_user_id_resolvido, payload_perfil)

        enviar_lote([payload_token], id_execucao=id_execucao)

        controle.hash_token_ms = hash_token_atual
        controle.save(update_fields=["hash_token_ms", "atualizado_em"])

        _registrar_tentativa(
            id_execucao,
            "task_carregar_atributo_token_individual",
            self.request.retries + 1,
            duracao=time.monotonic() - inicio,
        )
        return {"situacao": "sucesso"}

    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        _registrar_tentativa(
            id_execucao,
            "task_carregar_atributo_token_individual",
            self.request.retries + 1,
            erro=str(exc)[:2000],
            duracao=time.monotonic() - inicio,
        )
        raise self.retry(exc=exc, countdown=atraso) from exc


def _resolver_kc_user_id_lote(
    admin_cache: list[Any],
    usuario: Any,
    kc_user_id: str | None,
    *,
    realm_destino: str,
    id_execucao: str,
    nome_usuario: str | None,
) -> str | None:
    """Resolve o ``kc_user_id`` de um item do lote, buscando sob demanda.

    O Keycloak é a fonte da chave usada pelo token-ms — se o item
    chegou sem ``kc_user_id`` (não deveria acontecer:
    ``provisionar_usuario_kc`` sempre o retorna em sucesso), não
    desiste em silêncio: busca ativamente por username antes de
    decidir que não há perfil a enviar. ``admin_cache`` é uma lista de
    1 elemento usada como célula mutável — evita reautenticar no
    Keycloak a cada item do lote que já veio sem ``kc_user_id``.
    """
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        obter_admin_keycloak,
        resolver_kc_user_id_de_usuario,
    )

    if kc_user_id:
        return kc_user_id

    if admin_cache[0] is None:
        admin_cache[0] = obter_admin_keycloak(realm=realm_destino)
    kc_user_id = resolver_kc_user_id_de_usuario(admin_cache[0], usuario)
    if not kc_user_id:
        logger.warning(
            "[%s] kc_user_id não encontrado nem por busca ativa: %s",
            id_execucao,
            nome_usuario,
        )
    return kc_user_id


class _ItemLoteDescartado:
    """Sentinel: item sem rf/cpf/matricula, fora do lote e do hash."""


class _ItemLoteJaConfirmado:
    """Sentinel: hash_token_ms já bate — nada a enviar para este item."""


def _processar_item_lote_token_ms(
    item: dict,
    *,
    id_execucao: str,
    realm_destino: str,
    admin_cache: list[Any],
    perfis_pendentes: list[tuple[str, dict]],
) -> (
    tuple[dict, Any, str]
    | type[_ItemLoteDescartado]
    | type[_ItemLoteJaConfirmado]
):
    """Processa um item do lote: descarta, confirma ou prepara o envio.

    Retorna ``(payload_token, controle, hash_atual)`` quando o item
    deve entrar no lote de envio; ``_ItemLoteDescartado`` quando falta
    identificador (rf/cpf/matricula); ou ``_ItemLoteJaConfirmado``
    quando o hash já bate com o persistido. Distinguir os dois casos
    de "nada a enviar" é o que permite ao chamador contar
    ``descartados`` corretamente. Quando o ``kc_user_id`` está
    disponível, acumula o perfil em ``perfis_pendentes`` (mutação da
    lista recebida — nem todo item processado gera um perfil a enviar).
    """
    from apps.controle_etl.orquestrador_kc import (  # noqa: PLC0415
        _payload_token_ms_hash,
        calcular_hash_conteudo,
        construir_payload_perfil_token_ms,
        construir_payload_token_ms,
        obter_ou_criar_controle,
        payload_tem_identificador,
    )

    modelo = _resolver_modelo_staging(item["modelo_staging"])
    usuario = modelo.objects.get(pk=item["staging_pk"])

    payload_token = construir_payload_token_ms(usuario)
    if not payload_tem_identificador(payload_token):
        logger.warning(
            "[%s] descartado sem rf/cpf/matricula: %s",
            id_execucao,
            payload_token.get("nome"),
        )
        return _ItemLoteDescartado

    hash_atual = calcular_hash_conteudo(_payload_token_ms_hash(usuario))
    controle = obter_ou_criar_controle(
        item["tipo_entidade"],
        item["sistema_origem"],
        item["id_origem"],
        realm_destino,
    )
    if controle.hash_token_ms == hash_atual:
        # Já confirmado — mas a flag de pendência pode ter ficado
        # True de uma checagem anterior a este hash ter sido gravado.
        # Sem corrigir aqui, o registro nunca sai da varredura de
        # reprocessamento mesmo já estando correto.
        if controle.token_ms_pendente:
            controle.token_ms_pendente = False
            controle.save(update_fields=["token_ms_pendente"])
        return _ItemLoteJaConfirmado

    kc_user_id = _resolver_kc_user_id_lote(
        admin_cache,
        usuario,
        item.get("kc_user_id"),
        realm_destino=realm_destino,
        id_execucao=id_execucao,
        nome_usuario=payload_token.get("nome"),
    )
    if kc_user_id:
        payload_perfil = construir_payload_perfil_token_ms(usuario)
        if payload_perfil is not None:
            perfis_pendentes.append((kc_user_id, payload_perfil))

    return payload_token, controle, hash_atual


def _enviar_perfis_pendentes_lote(
    perfis_pendentes: list[tuple[str, dict]], id_execucao: str
) -> None:
    """Envia em paralelo os perfis coletados de um lote de token-ms.

    O token-ms não tem endpoint de lote para perfis (PUT individual
    por usuário) — paraleliza as chamadas via threads em vez de série,
    mesmo padrão usado no provisionamento Keycloak. Falha de UM perfil
    não interrompe o lote (best-effort: o que importa para a
    idempotência de ``hash_token_ms`` é o ``enviar_lote`` de atributos).
    """
    from apps.controle_etl.cliente_token_ms import enviar_perfil

    if not perfis_pendentes:
        return

    with ThreadPoolExecutor(
        max_workers=min(len(perfis_pendentes), _MAX_WORKERS_ENVIO_PERFIL)
    ) as executor:
        futures = [
            executor.submit(enviar_perfil, kc_user_id, payload_perfil)
            for kc_user_id, payload_perfil in perfis_pendentes
        ]
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                logger.warning(
                    "[%s] falha ao enviar perfil: %s",
                    id_execucao,
                    exc,
                )


def _confirmar_hashes_lote(
    controles_pendentes: list[tuple[Any, str]],
) -> None:
    """Grava ``hash_token_ms`` de cada controle após o POST do lote.

    Só é chamada DEPOIS do ``enviar_lote`` confirmar sucesso — se
    ``enviar_lote`` falhar, nenhum hash é gravado e o retry reprocessa
    o lote inteiro. ``token_ms_pendente=False`` marca esses clientes
    como resolvidos para a varredura de reprocessamento (ver
    ``task_reprocessar_pendencias``).
    """
    for controle, hash_novo in controles_pendentes:
        controle.hash_token_ms = hash_novo
        controle.token_ms_pendente = False
        controle.save(
            update_fields=[
                "hash_token_ms",
                "token_ms_pendente",
                "atualizado_em",
            ]
        )


@shared_task(
    bind=True,
    name="task_carregar_lote_atributos_token",
    max_retries=5,
)
def task_carregar_lote_atributos_token(
    self: Any,
    *,
    itens: list[dict],
    id_execucao: str,
    realm_destino: str,
) -> dict:
    """Carrega atributos complementares e perfis de um LOTE de usuários.

    Substitui o disparo fire-and-forget por usuário
    (``task_carregar_atributo_token_individual``) por um envio
    agrupado — reduz de até 1 task/chamada HTTP por usuário para 1 a
    cada ``_TAMANHO_LOTE_PROVISIONAMENTO`` usuários. Preserva a
    idempotência de ``hash_token_ms`` por usuário: cada item do lote é
    checado contra seu próprio hash antes de entrar no envio — o hash
    de cada um só é gravado depois que o POST do lote inteiro confirma
    sucesso.

    Args:
        itens: lista de dicts, um por usuário pendente do lote — cada
            um com ``modelo_staging``, ``staging_pk``, ``tipo_entidade``,
            ``sistema_origem``, ``id_origem``, ``kc_user_id``.
        id_execucao: UUID da ExecucaoETL associada.
        realm_destino: Realm Keycloak/token-ms de destino.

    Returns:
        Dicionário com ``enviados`` e ``descartados``.
    """
    from apps.controle_etl.cliente_token_ms import enviar_lote  # noqa: PLC0415

    inicio = time.monotonic()
    payloads_envio: list[dict] = []
    controles_pendentes: list[tuple[Any, str]] = []
    perfis_pendentes: list[tuple[str, dict]] = []
    descartados = 0
    admin_cache: list[Any] = [None]  # célula mutável: obtida sob demanda

    for item in itens:
        resultado = _processar_item_lote_token_ms(
            item,
            id_execucao=id_execucao,
            realm_destino=realm_destino,
            admin_cache=admin_cache,
            perfis_pendentes=perfis_pendentes,
        )
        if resultado is _ItemLoteDescartado:
            descartados += 1
            continue
        if resultado is _ItemLoteJaConfirmado:
            continue
        if not isinstance(resultado, tuple):
            continue

        payload_token, controle, hash_atual = resultado
        payloads_envio.append(payload_token)
        controles_pendentes.append((controle, hash_atual))

    if not payloads_envio:
        return {"enviados": 0, "descartados": descartados}

    _enviar_perfis_pendentes_lote(perfis_pendentes, id_execucao)

    try:
        enviar_lote(payloads_envio, id_execucao=id_execucao)
        _confirmar_hashes_lote(controles_pendentes)

        _registrar_tentativa(
            id_execucao,
            "task_carregar_lote_atributos_token",
            self.request.retries + 1,
            duracao=time.monotonic() - inicio,
        )
        return {
            "enviados": len(payloads_envio),
            "descartados": descartados,
        }

    except Exception as exc:
        atraso = _calcular_atraso(self.request.retries + 1)
        _registrar_tentativa(
            id_execucao,
            "task_carregar_lote_atributos_token",
            self.request.retries + 1,
            erro=str(exc)[:2000],
            duracao=time.monotonic() - inicio,
        )
        raise self.retry(exc=exc, countdown=atraso) from exc


# ---------------------------------------------------------------------------
# TASK_SYNC_REC_ETL
# ---------------------------------------------------------------------------


@shared_task(bind=True, name="task_sync_rec_etl")
def task_sync_rec_etl(
    self: Any, resultado_keycloak: dict, id_execucao: str
) -> dict:
    """Registra metadados operacionais e finaliza a execução.

    Consolida watermarks, checkpoints e métricas finais no SYNC_REC_DB.
    Fecha a ExecucaoETL assim que o Keycloak termina o lote — o
    token-ms é assíncrono (disparado fire-and-forget por registro a
    partir do Keycloak) e não faz parte do caminho síncrono desta
    chain; seu progresso é rastreável via ``ControleProvisionamento``
    (``hash_token_ms``), não pela ``situacao`` desta execução.

    Args:
        resultado_keycloak: Resultado da task de provisionamento no
            Keycloak.
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
    etapa, _ = LogEtapaETL.objects.update_or_create(
        execucao=execucao,
        nome_etapa=LogEtapaETL.NomeEtapa.SYNC_REC,
        defaults={"ordem_etapa": 6},
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

        # Countdown alinhado à janela máxima de retry do token-ms
        # individual (5 tentativas, backoff até _ATRASO_MAXIMO_REINTENTO)
        # — dá tempo real das tasks disparadas pelo Keycloak (agora
        # assíncronas, fora desta chain) confirmarem antes da limpeza.
        task_identidade_limpar_staging.apply_async(
            countdown=_ATRASO_MAXIMO_REINTENTO
        )

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


@shared_task(name="task_identidade_tratar_erro_pipeline")
def task_identidade_tratar_erro_pipeline(
    request: Any, exc: Exception, traceback: Any, id_execucao: str
) -> None:
    """Marca a ExecucaoETL como falha quando o chord de extração falha.

    Sem este handler, um ``ChordError`` (ex.: todas as tasks de
    extração esgotam os retries por SQL Server inacessível) propaga
    sem nenhum código chamar ``marcar_finalizada``, deixando a
    ExecucaoETL presa em ``executando`` para sempre — mesma classe de
    bug já corrigida para o retry de etapa individual, mas aqui no
    nível do chord inteiro.

    Args:
        request: Contexto da task que falhou (assinatura exigida pelo
            ``link_error`` do Celery).
        exc: Exceção que causou a falha (tipicamente ``ChordError``).
        traceback: Traceback da falha.
        id_execucao: UUID da ExecucaoETL associada.
    """
    from apps.controle_etl.models import ExecucaoETL  # noqa: PLC0415

    logger.error(
        "[%s] Pipeline falhou no chord de extração: %s",
        id_execucao,
        exc,
    )
    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    if execucao.situacao == ExecucaoETL.Situacao.EXECUTANDO:
        execucao.marcar_finalizada("falha")


@shared_task(bind=True, name="task_identidade_executar_pipeline")
def task_identidade_executar_pipeline(
    self: Any,
    id_execucao: str,
    data_referencia: str | None = None,
) -> None:
    """Orquestra o pipeline completo de ingestão de identidades.

    Executa extração paralela via chord, seguida da cadeia
    de resolução → provisionamento → token → registro operacional.

    Purga as filas de trabalho antes de montar o próprio chord —
    redundância de segurança contra resíduos de outro processamento
    (ex.: esta task raiz ficou na fila `celery` por um tempo e só
    começou a rodar depois de outra execução já ter sido cancelada
    via API, que também purga; ver
    `apps.controle_etl.views._cancelar_execucoes_nao_finalizadas_e_purgar_filas`).

    Args:
        id_execucao: UUID da ExecucaoETL associada.
        data_referencia: Data ISO opcional para replay.
    """
    from apps.controle_etl.models import ExecucaoETL  # noqa: PLC0415
    from apps.controle_etl.views import (  # noqa: PLC0415
        _cancelar_execucoes_nao_finalizadas_e_purgar_filas,
    )

    execucao = ExecucaoETL.objects.get(id_execucao=id_execucao)
    execucao.marcar_executando()

    _cancelar_execucoes_nao_finalizadas_e_purgar_filas(excluir_pk=execucao.pk)

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
        # Garante que clientes com o Keycloak já confirmado mas o
        # token-ms ainda pendente sejam reenviados mesmo sem dado
        # novo no watermark. Só cobre CoreSSO nesta versão.
        tarefas_extracao.append(task_reprocessar_pendencias.s(**kwargs))
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

    # A chain termina no Keycloak — o token-ms é carregado em lote,
    # disparado de forma assíncrona assim que o Keycloak confirma cada
    # lote, sem bloquear o fechamento da execução.
    callback = chain(
        task_identidade_resolver_identidade.s(id_execucao=id_execucao),
        task_provisionar_identidade_keycloak.s(id_execucao=id_execucao),
        task_sync_rec_etl.s(id_execucao=id_execucao),
    ).on_error(task_identidade_tratar_erro_pipeline.s(id_execucao=id_execucao))
    chord(tarefas_extracao)(callback)


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
