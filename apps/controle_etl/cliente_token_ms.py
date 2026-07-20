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

    O token-ms autentica requisições comparando o header configurável
    em ``API_KEY_HEADER`` (padrão ``X-API-Key``) contra ``API_KEY`` —
    mesmo padrão usado pelo próprio ETL para autenticar requisições
    que recebe. O ETL precisa enviar exatamente esse header ao chamar
    o token-ms.

    Returns:
        Cabeçalhos com Content-Type e a API Key quando disponível.
    """
    cabecalhos = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if settings.TOKEN_MS_API_KEY:
        cabecalhos[settings.TOKEN_MS_API_KEY_HEADER] = (
            settings.TOKEN_MS_API_KEY
        )
    return cabecalhos


def _e_retriavel(exc: Exception) -> bool:
    """Indica se o erro justifica uma nova tentativa.

    Erros de transporte/timeout são sempre retentáveis. Erros HTTP só
    são retentáveis quando o status indica falha transitória do
    servidor (ex.: 503) — status como 400/404 são erro de payload ou
    de rota e nunca vão se resolver sozinhos com retry.

    Args:
        exc: Exceção capturada na chamada ao token-ms.

    Returns:
        ``True`` se a chamada deve ser repetida.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _STATUS_RETRIAVEIS
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


def _aguardar_nova_tentativa(exc: Exception, tentativa: int) -> None:
    """Registra o erro e aguarda o backoff antes da próxima tentativa.

    Args:
        exc: Exceção que motivou a nova tentativa.
        tentativa: Número da tentativa que acabou de falhar (1-based).
    """
    atraso = min(_ATRASO_BASE * (2 ** (tentativa - 1)), _ATRASO_MAXIMO)
    logger.warning(
        "token-ms: erro transitório (tentativa %d/%d) — aguardando %.1fs: %s",
        tentativa,
        _MAX_TENTATIVAS,
        atraso,
        exc,
    )
    time.sleep(atraso)


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
        httpx.HTTPStatusError: Erro do token-ms — imediatamente se não
            for retentável (ex.: 400/404), ou após esgotar as
            tentativas em caso de falha transitória (ex.: 503).
        httpx.TransportError: Após esgotar as tentativas por falha de
            conexão.
        httpx.TimeoutException: Após esgotar as tentativas por timeout.
    """
    url = f"{settings.TOKEN_MS_URL.rstrip('/')}/api/v1/etl/push-batch"
    corpo = {"id_execucao": id_execucao, "usuarios": usuarios}

    tentativa = 0
    while True:
        try:
            with httpx.Client(timeout=settings.TOKEN_MS_TIMEOUT) as cliente:
                resposta = cliente.post(url, json=corpo, headers=_cabecalhos())
            resposta.raise_for_status()
            return resposta.json() if resposta.content else {"situacao": "ok"}
        except (
            httpx.TransportError,
            httpx.HTTPStatusError,
            httpx.TimeoutException,
        ) as exc:
            tentativa += 1
            if not _e_retriavel(exc) or tentativa >= _MAX_TENTATIVAS:
                logger.error(
                    "token-ms: lote falhou após %d tentativa(s): %s",
                    tentativa,
                    exc,
                )
                raise
            _aguardar_nova_tentativa(exc, tentativa)


def enviar_perfil(
    usuario_id: str,
    payload: dict,
) -> dict:
    """Sincroniza a projeção de perfis de um usuário no token-ms.

    Diferente de ``enviar_lote``, que publica atributos complementares
    em lote e sem alvo individual, esta função faz o upsert da
    projeção de autorização de UM usuário por vez — o token-ms
    substitui integralmente a lista de perfis a cada chamada
    (``PUT /perfis/{usuario_id}/``), então o payload deve trazer o
    conjunto completo e atual de perfis, nunca parcial.

    Args:
        usuario_id: UUID do usuário no Keycloak (``kc_user_id``).
        payload: Corpo da projeção (``login``, ``nome``, ``situacao``,
            ``perfis``, ``permissoes`` etc. — ver
            ``ProjecaoUsuarioSerializer`` do token-ms).

    Returns:
        ``{"situacao": "ok"}`` após sincronização bem-sucedida.

    Raises:
        httpx.HTTPStatusError: Erro do token-ms — imediatamente se não
            for retentável (ex.: 400), ou após esgotar as tentativas
            em caso de falha transitória (ex.: 503).
        httpx.TransportError: Após esgotar as tentativas por falha de
            conexão.
        httpx.TimeoutException: Após esgotar as tentativas por timeout.
    """
    url = (
        f"{settings.TOKEN_MS_URL.rstrip('/')}" f"/api/v1/perfis/{usuario_id}/"
    )

    tentativa = 0
    while True:
        try:
            with httpx.Client(timeout=settings.TOKEN_MS_TIMEOUT) as cliente:
                resposta = cliente.put(
                    url, json=payload, headers=_cabecalhos()
                )
            resposta.raise_for_status()
            return {"situacao": "ok"}
        except (
            httpx.TransportError,
            httpx.HTTPStatusError,
            httpx.TimeoutException,
        ) as exc:
            tentativa += 1
            if not _e_retriavel(exc) or tentativa >= _MAX_TENTATIVAS:
                logger.error(
                    "token-ms: sincronização de perfil de %s"
                    " falhou após %d tentativa(s): %s",
                    usuario_id,
                    tentativa,
                    exc,
                )
                raise
            _aguardar_nova_tentativa(exc, tentativa)


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
    lote: list[dict] = []

    for usuario in usuarios:
        lote.append(usuario)
        if len(lote) >= tamanho:
            enviar_lote(lote, id_execucao=id_execucao)
            total += len(lote)
            lotes += 1
            lote = []

    if lote:
        enviar_lote(lote, id_execucao=id_execucao)
        total += len(lote)
        lotes += 1

    return {"enviados": total, "lotes": lotes}
