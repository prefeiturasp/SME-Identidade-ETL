from __future__ import annotations

import logging
import time
from typing import Iterable

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    token = getattr(settings, "TOKEN_MS_INTERNAL_TOKEN", "") or settings.TOKEN_MS_TOKEN
    if token:
        h["X-Internal-Token"] = token
    return h


def send_batch(
    users: list[dict],
    *,
    execution_id: str,
    max_retries: int = 5,
    base_delay: float = 1.0,
) -> dict:
    url = f"{settings.TOKEN_MS_URL.rstrip('/')}/api/v1/etl/push-batch"
    body = {"execution_id": execution_id, "users": users}

    attempt = 0
    while True:
        try:
            with httpx.Client(timeout=settings.TOKEN_MS_TIMEOUT) as client:
                resp = client.post(url, json=body, headers=_headers())
            if resp.status_code in RETRYABLE_STATUS:
                raise httpx.HTTPStatusError(
                    f"token-ms {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.json() if resp.content else {"status": "ok"}
        except (httpx.TransportError, httpx.HTTPStatusError, httpx.TimeoutException) as e:
            attempt += 1
            if attempt > max_retries:
                logger.error("token-ms batch failed after %d retries: %s", max_retries, e)
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), 60.0)
            logger.warning(
                "token-ms transient error (attempt %d/%d) — retry in %.1fs: %s",
                attempt, max_retries, delay, e,
            )
            time.sleep(delay)


def send_all(
    users: Iterable[dict],
    *,
    execution_id: str,
    batch_size: int | None = None,
) -> dict:
    batch_size = batch_size or settings.TOKEN_MS_BATCH_SIZE
    total = 0
    batches = 0
    buffer: list[dict] = []

    for u in users:
        buffer.append(u)
        if len(buffer) >= batch_size:
            send_batch(buffer, execution_id=execution_id)
            total += len(buffer)
            batches += 1
            buffer = []

    if buffer:
        send_batch(buffer, execution_id=execution_id)
        total += len(buffer)
        batches += 1

    return {"sent": total, "batches": batches}
