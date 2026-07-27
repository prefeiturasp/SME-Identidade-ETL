"""Processador paralelo de lotes e utilitários de hash para ETL.

Classes:
    ThreadPoolProcessor:
        Divide a lista de itens em ``max_workers`` lotes (um por worker) e
        submete cada lote como uma única future ao ``ThreadPoolExecutor``.
        Isso elimina o overhead de uma future por registro. Cada worker
        processa seu lote inteiro em loop Python puro, minimizando a
        contenção pelo GIL.

        Suporta uso como gerenciador de contexto para reutilização do
        pool de threads entre múltiplas chamadas de ``processar``,
        eliminando o custo de criação/destruição de threads a cada lote.

Funções:
    calcular_hash:
        Calcula SHA-256 dos campos de uma linha para controle incremental.

        Suporta dois modos:
            - campos como índices inteiros (tuplas/listas SQL)
            - campos como nomes string (dicts ou objetos Django)

        Campos string são ordenados automaticamente para garantir
        determinismo. Campos inteiros mantêm a ordem recebida.

    decorar_para_hash:
        Transforma uma linha em ``(id_destino, hash, dados)``.

        Modos de uso:

        Tuplas SQL (4 argumentos via ``partial``):
            func = partial(decorar_para_hash, tabela, pk_index, field_indexes)
            resultado = processor.processar(rows, func)

        Dicts ou objetos Django (3 argumentos via ``partial``):
            func = partial(decorar_para_hash, tabela, update_fields)
            resultado = processor.processar([(pk, obj), ...], func)
"""

import hashlib
import logging
import math
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from typing import Any, TypeVar, overload

from django.conf import settings

logger = logging.getLogger(__name__)
T = TypeVar("T")


class ThreadPoolProcessor:
    """Processador paralelo por lotes para pipelines ETL.

    Divide os itens recebidos em ``max_workers`` lotes e submete cada
    lote como uma única future ao ``ThreadPoolExecutor``. Isso reduz
    o overhead de scheduling comparado a uma future por item e diminui
    a contenção pelo GIL ao manter cada worker ocupado em loop contínuo.

    Uso como gerenciador de contexto (recomendado para múltiplos lotes):
        with ThreadPoolProcessor(max_workers=4) as processor:
            for lote in lotes:
                processor.processar(lote, func)

    Nesse modo, o pool de threads é criado uma única vez e reutilizado
    entre todas as chamadas de ``processar``, eliminando o overhead de
    criação de threads por lote.

    Uso avulso (compatibilidade legada):
        processor = ThreadPoolProcessor()
        resultado = processor.processar(items, func)

    Nesse modo, um executor temporário é criado e destruído a cada
    chamada de ``processar``.
    """

    def __init__(
        self,
        max_workers: int | None = None,
        timeout: int | None = None,
        prefixo_log: str = "ETL",
    ) -> None:
        """Inicializa o processador.

        Args:
            max_workers: Número de workers paralelos.
            timeout: Tempo máximo de execução em segundos.
            prefixo_log: Prefixo utilizado nas mensagens de log.
        """
        self.max_workers = max_workers or settings.THREAD_POOL_MAX_WORKERS
        self.timeout = timeout or settings.THREAD_POOL_CHUNK_TIMEOUT
        self.prefixo_log = prefixo_log
        self._executor: ThreadPoolExecutor | None = None

    def __enter__(self) -> "ThreadPoolProcessor":
        """Cria o executor persistente para reutilização entre chamadas."""
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        return self

    def __exit__(self, *_: Any) -> None:
        """Encerra o executor e libera os recursos do pool."""
        self.shutdown()

    def shutdown(self) -> None:
        """Encerra o executor de forma segura, aguardando tarefas pendentes."""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def processar(
        self, items: Sequence[Any], func: Callable[[Any], T]
    ) -> list[T]:
        """Processa itens em paralelo por lotes, preservando a ordem.

        Quando utilizado como gerenciador de contexto, reutiliza o pool
        de threads já criado. Caso contrário, cria um executor temporário
        por chamada (comportamento legado).

        Args:
            items: Sequência de itens a processar.
            func: Função aplicada a cada item.

        Returns:
            Lista de resultados na mesma ordem dos itens de entrada.

        Raises:
            TimeoutError: Se o tempo de execução exceder ``self.timeout``.
        """
        if not items:
            return []

        if self._executor is not None:
            return self._processar_com_executor(self._executor, items, func)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            return self._processar_com_executor(executor, items, func)

    def _processar_com_executor(
        self,
        executor: ThreadPoolExecutor,
        items: Sequence[Any],
        func: Callable[[Any], T],
    ) -> list[T]:
        """Executa o processamento usando o executor fornecido.

        Args:
            executor: Pool de threads a utilizar.
            items: Sequência de itens a processar.
            func: Função aplicada a cada item.

        Returns:
            Lista de resultados na mesma ordem dos itens de entrada.

        Raises:
            TimeoutError: Se o tempo de execução exceder ``self.timeout``.
        """
        total = len(items)
        inicio = time.monotonic()

        batch_size = math.ceil(total / self.max_workers)
        batches: list[tuple[int, Sequence[Any]]] = [
            (start, items[start : start + batch_size])
            for start in range(0, total, batch_size)
        ]

        resultados: list[T | None] = [None] * total
        erro_capturado: Exception | None = None

        futures = {
            executor.submit(self._processar_lote, start, batch, func): start
            for start, batch in batches
        }

        try:
            for future in as_completed(futures, timeout=self.timeout):
                start = futures[future]
                try:
                    batch_start, batch_result = future.result()
                    resultados[
                        batch_start : batch_start + len(batch_result)
                    ] = batch_result
                except Exception as exc:
                    logger.exception(
                        "[%s] Erro no lote iniciado em %d: %s",
                        self.prefixo_log,
                        start,
                        exc,
                    )
                    if erro_capturado is None:
                        erro_capturado = exc
        except TimeoutError as exc:
            for future in futures:
                future.cancel()

            msg = (
                f"[{self.prefixo_log}] Timeout após {self.timeout}s "
                f"processando {len(items)} itens"
            )
            logger.error(msg)
            raise TimeoutError(msg) from exc

        elapsed = time.monotonic() - inicio
        self._log_throughput(total, elapsed)

        if erro_capturado is not None:
            raise erro_capturado

        return resultados  # type: ignore[return-value]

    @staticmethod
    def _processar_lote(
        start: int,
        batch: Sequence[Any],
        func: Callable[[Any], T],
    ) -> tuple[int, list[T]]:
        """Processa um lote de itens sequencialmente.

        Args:
            start: Índice inicial do lote na lista original.
            batch: Subconjunto de itens a processar.
            func: Função aplicada a cada item.

        Returns:
            Tupla contendo o índice inicial e a lista de resultados.
        """
        return start, [func(item) for item in batch]

    def _log_throughput(self, total: int, elapsed: float) -> None:
        """Registra métricas de throughput no log.

        Args:
            total: Número total de itens processados.
            elapsed: Tempo total em segundos.
        """
        taxa = total / elapsed if elapsed > 0 else float("inf")
        logger.info(
            "[%s] throughput: %d itens em %.2fs (%.2f itens/s)",
            self.prefixo_log,
            total,
            elapsed,
            taxa,
        )


def _is_int_fields(fields: Sequence[Any]) -> bool:
    """Verifica se todos os campos são inteiros."""
    return bool(fields) and all(isinstance(f, int) for f in fields)


def _is_str_fields(fields: Sequence[Any]) -> bool:
    """Verifica se todos os campos são strings."""
    return bool(fields) and all(isinstance(f, str) for f in fields)


@overload
def calcular_hash(obj: Sequence[Any], fields: Sequence[int]) -> str: ...
@overload
def calcular_hash(obj: dict[str, Any] | Any, fields: Sequence[str]) -> str: ...


def calcular_hash(obj: Any, fields: Sequence[int] | Sequence[str]) -> str:
    """Calcula o hash SHA-256 de campos selecionados.

    Suporta dois modos:

    1. Campos como índices inteiros:
       ``obj`` é tratado como sequência (tupla/lista).

    2. Campos como nomes string:
       ``obj`` é tratado como ``dict`` ou objeto.

    Regras:
        - Campos string são ordenados para garantir determinismo.
        - Campos inteiros mantêm a ordem fornecida.

    Args:
        obj: Linha ou objeto a ser serializado.
        fields: Índices ou nomes de campos.

    Returns:
        Hash SHA-256 em formato hexadecimal.

    Raises:
        TypeError: Se ``fields`` não for homogêneo.
    """
    if not fields:
        conteudo = b""
        return hashlib.sha256(conteudo).hexdigest()

    if _is_int_fields(fields):
        conteudo = "|".join(str(obj[idx]) for idx in fields).encode("utf-8")
        return hashlib.sha256(conteudo).hexdigest()

    if _is_str_fields(fields):
        # Garantimos determinismo ordenando os nomes dos campos
        sorted_fields = sorted(fields)
        if isinstance(obj, dict):
            conteudo = "|".join(
                f"{field}={obj.get(field)}" for field in sorted_fields
            ).encode("utf-8")
        else:
            conteudo = "|".join(
                f"{field}={getattr(obj, str(field))}"
                for field in sorted_fields
            ).encode("utf-8")
        return hashlib.sha256(conteudo).hexdigest()

    raise TypeError("fields deve conter somente int ou somente str")


@overload
def decorar_para_hash(
    tabela: str,
    pk_index: int,
    field_indexes: Sequence[int],
    row: Sequence[Any],
) -> tuple[str, str, Sequence[Any]]: ...
@overload
def decorar_para_hash(
    tabela: str,
    update_fields: Sequence[str],
    item: tuple[Any, Any],
) -> tuple[str, str, Any]: ...


def decorar_para_hash(tabela: str, *args: Any) -> tuple[str, str, Any]:  # type: ignore[misc]
    """Transforma dados em ``(id_destino, hash, dados)``.

    Modos suportados:

    1. Tuplas SQL:
        ``decorar_para_hash(tabela, pk_index, field_indexes, row)``

    2. Dicts ou objetos:
        ``decorar_para_hash(tabela, update_fields, (pk, obj))``

    Args:
        tabela: Nome lógico da tabela.
        *args: Argumentos conforme o modo de uso.

    Returns:
        Tupla no formato ``(id_destino, hash_hex, dados_originais)``.

    Raises:
        TypeError: Se a assinatura não corresponder a um modo válido.
    """
    if len(args) == 3 and isinstance(args[0], int):
        pk_index, field_indexes, row = args
        pk_val = row[pk_index]
        return (f"{tabela}:{pk_val}", calcular_hash(row, field_indexes), row)

    if len(args) == 2:
        update_fields, item = args
        pk_val, obj = item
        return (f"{tabela}:{pk_val}", calcular_hash(obj, update_fields), obj)

    raise TypeError(
        "Assinatura inválida para decorar_para_hash. "
        "Use (tabela, pk_index, field_indexes, row) "
        "ou (tabela, update_fields, (pk, obj))."
    )
