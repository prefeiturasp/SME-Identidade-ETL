"""Cliente HTTP para publicação de atributos no token-ms."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable

import httpx
from django.conf import settings

logger = logging.getLogger("etl_identidade")

_STATUS_RETRIAVEIS = {408, 425, 429, 500, 502, 503, 504}
_MAX_TENTATIVAS = 5
_ATRASO_BASE = 1.0
_ATRASO_MAXIMO = 60.0


def _cabecalhos() -> dict[str, str]:
    """Monta os cabeçalhos de autenticação para o token-ms.

    Returns:
        Cabeçalhos com Content-Type e X-Internal-Token quando disponível.
    """
    cabecalhos = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    token = (
        getattr(settings, "TOKEN_MS_TOKEN_INTERNO", "")
        or settings.TOKEN_MS_TOKEN
    )
    if token:
        cabecalhos["X-Internal-Token"] = token
    return cabecalhos


def enviar_lote(
    usuarios: list[dict],
    *,
    id_execucao: str,
) -> dict:
    """Envia um lote de usuários para o token-ms com reintento exponencial.

    Args:
        usuarios: Lista de payloads de atributos de usuário.
        id_execucao: ID da execução ETL associada.

    Returns:
        Resposta JSON do token-ms ou ``{"situacao": "ok"}`` se sem corpo.

    Raises:
        httpx.HTTPStatusError: Após esgotar todas as tentativas.
    """
    url = f"{settings.TOKEN_MS_URL.rstrip('/')}/api/v1/etl/push-batch"
    corpo = {"id_execucao": id_execucao, "usuarios": usuarios}

    tentativa = 0
    while True:
        try:
            with httpx.Client(timeout=settings.TOKEN_MS_TIMEOUT) as cliente:
                resposta = cliente.post(url, json=corpo, headers=_cabecalhos())
            if resposta.status_code in _STATUS_RETRIAVEIS:
                raise httpx.HTTPStatusError(
                    f"token-ms {resposta.status_code}",
                    request=resposta.request,
                    response=resposta,
                )
            resposta.raise_for_status()
            return resposta.json() if resposta.content else {"situacao": "ok"}
        except (
            httpx.TransportError,
            httpx.HTTPStatusError,
            httpx.TimeoutException,
        ) as exc:
            tentativa += 1
            if tentativa >= _MAX_TENTATIVAS:
                logger.error(
                    "token-ms: lote falhou após %d tentativas: %s",
                    _MAX_TENTATIVAS,
                    exc,
                )
                raise
            atraso = min(_ATRASO_BASE * (2 ** (tentativa - 1)), _ATRASO_MAXIMO)
            logger.warning(
                "token-ms: erro transitório"
                " (tentativa %d/%d) — aguardando %.1fs: %s",
                tentativa,
                _MAX_TENTATIVAS,
                atraso,
                exc,
            )
            time.sleep(atraso)


def enviar_todos(
    usuarios: Iterable[dict],
    *,
    id_execucao: str,
    tamanho_lote: int | None = None,
) -> dict:
    """Envia todos os usuários em lotes para o token-ms.

    Args:
        usuarios: Iterável de payloads de atributos.
        id_execucao: ID da execução ETL associada.
        tamanho_lote: Tamanho de cada lote
            (usa TOKEN_MS_TAMANHO_LOTE se None).

    Returns:
        Dicionário com ``enviados`` e ``lotes``.
    """
    tamanho = tamanho_lote or settings.TOKEN_MS_TAMANHO_LOTE
    total = 0
    lotes = 0
    buffer: list[dict] = []

    for usuario in usuarios:
        buffer.append(usuario)
        if len(buffer) >= tamanho:
            enviar_lote(buffer, id_execucao=id_execucao)
            total += len(buffer)
            lotes += 1
            buffer = []

    if buffer:
        enviar_lote(buffer, id_execucao=id_execucao)
        total += len(buffer)
        lotes += 1

    return {"enviados": total, "lotes": lotes}
