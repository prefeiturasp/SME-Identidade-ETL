"""Tasks Celery de extração, transformação e deduplicação do staging ETL."""

import logging
from collections.abc import Iterable
from typing import Any

from celery import shared_task

logger = logging.getLogger("etl_identidade")

_MODELO_POR_TIPO = {
    "servidor": "UsuarioServidorStaging",
    "aluno": "UsuarioAlunoStaging",
    "terceiro": "UsuarioTerceiroStaging",
}

_CAMPOS_COMUNS = (
    "fonte",
    "cpf",
    "nome",
    "email",
    "situacao",
    "lotacao",
    "lotacao_nome",
    "dre",
    "ue",
)

_CAMPOS_POR_TIPO = {
    "servidor": ("rf", "matricula", "cargo", "funcao"),
    "aluno": ("cod_escola", "turma", "matricula"),
    "terceiro": ("tipo_acesso", "matricula"),
}

_TAMANHO_LOTE_PERSISTENCIA = 500


def _instanciar_staging(modelo: type, registro: Any, id_execucao: str) -> Any:
    """Monta a instância de staging (não salva) a partir de um registro."""
    tipo = registro.tipo
    campos = {
        "id_execucao": id_execucao,
        "situacao": "extraido",
        **{
            campo: getattr(registro, campo)
            for campo in _CAMPOS_COMUNS
            if campo != "situacao"
        },
        **{
            campo: getattr(registro, campo, None)
            for campo in _CAMPOS_POR_TIPO[tipo]
        },
    }
    return modelo(**campos)


def persistir_extracao_staging(
    registros: Iterable, *, id_execucao: str
) -> int:
    """Persistir RegistroIdentidade extraídos como staging records.

    Mapeia cada registro para o modelo de staging correspondente ao
    seu ``tipo`` (servidor, aluno ou terceiro), marcando ``situacao``
    como 'extraido' para posterior processamento por
    ``transformar_staging``.

    Grava em lotes de ``_TAMANHO_LOTE_PERSISTENCIA`` conforme consome
    ``registros``, em vez de acumular a fonte inteira em memória antes
    do primeiro ``bulk_create`` — para fontes grandes/represadas
    (ex.: SE1426 completo), acumular tudo antes de gravar já causou
    OOM/SIGKILL do worker em ambientes com memória limitada.

    Args:
        registros: Iterável (idealmente generator) de RegistroIdentidade.
        id_execucao: UUID da ExecucaoETL associada.

    Returns:
        Total de registros persistidos.
    """
    from apps.staging import models as modelos_staging  # noqa: PLC0415

    lotes_por_tipo: dict[str, list] = {
        "servidor": [],
        "aluno": [],
        "terceiro": [],
    }
    total = 0

    def _descarregar(tipo: str) -> None:
        nonlocal total
        lote = lotes_por_tipo[tipo]
        if not lote:
            return
        modelo = getattr(modelos_staging, _MODELO_POR_TIPO[tipo])
        modelo.objects.bulk_create(lote, batch_size=_TAMANHO_LOTE_PERSISTENCIA)
        total += len(lote)
        lotes_por_tipo[tipo] = []

    for registro in registros:
        tipo = registro.tipo
        if tipo not in lotes_por_tipo:
            logger.warning(
                "[%s] persistir_extracao_staging — tipo desconhecido: %s",
                id_execucao,
                tipo,
            )
            continue

        modelo = getattr(modelos_staging, _MODELO_POR_TIPO[tipo])
        lotes_por_tipo[tipo].append(
            _instanciar_staging(modelo, registro, id_execucao)
        )

        if len(lotes_por_tipo[tipo]) >= _TAMANHO_LOTE_PERSISTENCIA:
            _descarregar(tipo)

    for tipo in lotes_por_tipo:
        _descarregar(tipo)

    logger.info(
        "[%s] persistir_extracao_staging — %d registros persistidos",
        id_execucao,
        total,
    )
    return total


@shared_task(name="staging.tasks.transformar_staging")
def transformar_staging(id_execucao: str) -> dict:
    """Normaliza e valida registros de staging de uma execução.

    Percorre UsuarioServidorStaging, UsuarioAlunoStaging e
    UsuarioTerceiroStaging marcando cada registro como 'pronto'
    após validação básica de CPF e campos obrigatórios.

    Args:
        id_execucao: UUID da ExecucaoETL associada.

    Returns:
        Dicionário com totais por tipo de usuário.
    """
    from apps.staging.models import (  # noqa: PLC0415
        UsuarioAlunoStaging,
        UsuarioServidorStaging,
        UsuarioTerceiroStaging,
    )

    totais: dict[str, int] = {
        "servidor": 0,
        "aluno": 0,
        "terceiro": 0,
        "erros": 0,
    }

    for modelo, chave in (
        (UsuarioServidorStaging, "servidor"),
        (UsuarioAlunoStaging, "aluno"),
        (UsuarioTerceiroStaging, "terceiro"),
    ):
        qs = modelo.objects.filter(
            id_execucao=id_execucao, situacao="extraido"
        )
        prontos = erros = 0
        atualizacoes = []
        for usuario in qs.iterator(chunk_size=500):
            if not usuario.nome and not usuario.cpf:
                usuario.situacao = "erro"
                usuario.detalhe_erro = "nome e CPF ausentes"
                erros += 1
            else:
                usuario.situacao = "pronto"
                prontos += 1
            atualizacoes.append(usuario)

        if atualizacoes:
            modelo.objects.bulk_update(
                atualizacoes,  # type: ignore[arg-type]
                ["situacao", "detalhe_erro"],
                batch_size=500,
            )

        totais[chave] = prontos
        totais["erros"] += erros

    totais["total"] = totais["servidor"] + totais["aluno"] + totais["terceiro"]

    logger.info(
        "[%s] transformar_staging — %s",
        id_execucao,
        totais,
    )
    return totais


_PRIORIDADE_FONTE = {"se1426": 1, "eol_db": 2, "coresso": 3}

_TAMANHO_LOTE_DEDUP = 2000


def _resolver_vencedores(registros: Iterable[dict]) -> set[int]:
    """Determina, em streaming, o vencedor por CPF e por RF.

    Recebe dicts leves (``id``, ``cpf``, ``rf``, ``fonte``) em vez de
    instâncias completas do model — guarda em memória só a menor
    prioridade já vista por chave, não a lista inteira de registros.
    """
    vencedor_cpf: dict[str, tuple[int, int]] = {}
    vencedor_rf: dict[str, tuple[int, int]] = {}

    for registro in registros:
        prioridade = _PRIORIDADE_FONTE.get(registro["fonte"], 99)
        chave_cpf = (registro["cpf"] or "").strip()
        chave_rf = (registro["rf"] or "").strip()

        for chave, vencedores in (
            (chave_cpf, vencedor_cpf),
            (chave_rf, vencedor_rf),
        ):
            if not chave:
                continue
            atual = vencedores.get(chave)
            if atual is None or prioridade < atual[0]:
                vencedores[chave] = (prioridade, registro["id"])

    ids_cpf = {v[1] for v in vencedor_cpf.values()}
    ids_rf = {v[1] for v in vencedor_rf.values()}
    return ids_cpf | ids_rf


def _marcar_duplicatas(
    registros: Iterable[dict], vencedores_ids: set[int]
) -> int:
    """Marca como 'ignorado', em lotes, todo registro fora dos vencedores."""
    from apps.staging.models import UsuarioServidorStaging  # noqa: PLC0415

    ignorados = 0
    lote: list[int] = []
    for registro in registros:
        cpf = (registro["cpf"] or "").strip()
        rf = (registro["rf"] or "").strip()
        if not cpf and not rf:
            continue
        if registro["id"] not in vencedores_ids:
            lote.append(registro["id"])
            ignorados += 1
        if len(lote) >= _TAMANHO_LOTE_DEDUP:
            UsuarioServidorStaging.objects.filter(id__in=lote).update(
                situacao="ignorado"
            )
            lote = []

    if lote:
        UsuarioServidorStaging.objects.filter(id__in=lote).update(
            situacao="ignorado"
        )
    return ignorados


@shared_task(name="staging.tasks.deduplicar_identidades")
def deduplicar_identidades(
    resultado_transform: dict, id_execucao: str
) -> dict:
    """Deduplica servidores por CPF/RF entre fontes distintas.

    Mantém o registro de maior prioridade de fonte (se1426 > coresso)
    e marca duplicatas como 'ignorado'. Processa em streaming
    (``.iterator()`` + valores leves) para suportar execuções com
    milhões de registros sem carregar tudo em memória de uma vez —
    ver histórico de OOM em execuções de grande volume do CoreSSO.

    Args:
        resultado_transform: Resultado da task transformar_staging.
        id_execucao: UUID da ExecucaoETL associada.

    Returns:
        Dicionário com totais de deduplicação.
    """
    from apps.staging.models import UsuarioServidorStaging  # noqa: PLC0415

    base_qs = UsuarioServidorStaging.objects.filter(
        id_execucao=id_execucao, situacao="pronto"
    ).order_by("id")

    total = base_qs.count()

    vencedores_ids = _resolver_vencedores(
        base_qs.values("id", "cpf", "rf", "fonte").iterator(
            chunk_size=_TAMANHO_LOTE_DEDUP
        )
    )
    ignorados = _marcar_duplicatas(
        base_qs.values("id", "cpf", "rf").iterator(
            chunk_size=_TAMANHO_LOTE_DEDUP
        ),
        vencedores_ids,
    )
    total_deduplicado = total - ignorados

    logger.info(
        "[%s] deduplicar_identidades — %d ignorados",
        id_execucao,
        ignorados,
    )
    return {
        "ignorados": ignorados,
        "total_deduplicado": total_deduplicado,
        **resultado_transform,
    }
