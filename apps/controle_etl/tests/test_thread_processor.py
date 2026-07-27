"""Testes para apps.controle_etl.libs.thread_processor."""

from __future__ import annotations

import time

import pytest

from apps.controle_etl.libs.thread_processor import (
    ThreadPoolProcessor,
    calcular_hash,
    decorar_para_hash,
)

# ---------------------------------------------------------------------------
# ThreadPoolProcessor — modo avulso
# ---------------------------------------------------------------------------


class TestThreadPoolProcessorAvulso:
    """Testa o processamento sem reutilização de executor entre chamadas."""

    def test_lista_vazia_retorna_vazio(self) -> None:
        processor = ThreadPoolProcessor(max_workers=2)
        assert processor.processar([], lambda x: x) == []

    def test_processa_preservando_ordem(self) -> None:
        processor = ThreadPoolProcessor(max_workers=4)
        resultado = processor.processar(list(range(10)), lambda x: x * 2)
        assert resultado == [x * 2 for x in range(10)]

    def test_erro_em_um_item_propaga(self) -> None:
        processor = ThreadPoolProcessor(max_workers=2)

        def _func(x: int) -> int:
            if x == 3:
                raise ValueError("item inválido")
            return x

        with pytest.raises(ValueError, match="item inválido"):
            processor.processar(list(range(5)), _func)

    def test_timeout_propaga_timeouterror(self) -> None:
        processor = ThreadPoolProcessor(max_workers=1, timeout=1)

        def _func(x: int) -> int:
            time.sleep(1.5)
            return x

        with pytest.raises(TimeoutError, match="Timeout"):
            processor.processar([1, 2], _func)


# ---------------------------------------------------------------------------
# ThreadPoolProcessor — modo gerenciador de contexto
# ---------------------------------------------------------------------------


class TestThreadPoolProcessorContexto:
    """Testa a reutilização do executor via `with`."""

    def test_reutiliza_executor_entre_chamadas(self) -> None:
        with ThreadPoolProcessor(max_workers=2) as processor:
            assert processor.processar([1, 2], lambda x: x + 1) == [2, 3]
            assert processor.processar([10], lambda x: x + 1) == [11]

    def test_shutdown_libera_executor(self) -> None:
        processor = ThreadPoolProcessor(max_workers=2)
        with processor:
            assert processor._executor is not None
        assert processor._executor is None

    def test_shutdown_sem_executor_nao_falha(self) -> None:
        processor = ThreadPoolProcessor(max_workers=2)
        processor.shutdown()
        assert processor._executor is None


# ---------------------------------------------------------------------------
# calcular_hash
# ---------------------------------------------------------------------------


class TestCalcularHash:
    """Testa o cálculo de hash em modo tupla/índices e dict/objeto."""

    def test_sem_campos_retorna_hash_vazio(self) -> None:
        h = calcular_hash({}, [])
        assert isinstance(h, str)
        assert len(h) == 64

    def test_campos_inteiros_em_tupla(self) -> None:
        h1 = calcular_hash(("a", 1, "b"), [0, 2])
        h2 = calcular_hash(("a", 1, "b"), [0, 2])
        assert h1 == h2

    def test_campos_string_em_dict_ordem_nao_importa(self) -> None:
        h1 = calcular_hash({"nome": "Ana", "cpf": "123"}, ["nome", "cpf"])
        h2 = calcular_hash({"cpf": "123", "nome": "Ana"}, ["cpf", "nome"])
        assert h1 == h2

    def test_campos_string_em_objeto(self) -> None:
        class Obj:
            nome = "Ana"
            cpf = "123"

        h = calcular_hash(Obj(), ["nome", "cpf"])
        assert isinstance(h, str)
        assert len(h) == 64

    def test_fields_mistos_lanca_typeerror(self) -> None:
        with pytest.raises(TypeError):
            calcular_hash({"a": 1}, [0, "campo"])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# decorar_para_hash
# ---------------------------------------------------------------------------


class TestDecorarParaHash:
    """Testa a decoração de linhas SQL e objetos para (id, hash, dados)."""

    def test_modo_tupla_sql(self) -> None:
        row = (42, "Ana", "123")
        id_destino, hash_hex, dados = decorar_para_hash(
            "tabela", 0, [1, 2], row
        )
        assert id_destino == "tabela:42"
        assert len(hash_hex) == 64
        assert dados == row

    def test_modo_dict_ou_objeto(self) -> None:
        obj = {"nome": "Ana"}
        id_destino, hash_hex, dados = decorar_para_hash(
            "tabela", ["nome"], (7, obj)
        )
        assert id_destino == "tabela:7"
        assert len(hash_hex) == 64
        assert dados is obj

    def test_assinatura_invalida_lanca_typeerror(self) -> None:
        with pytest.raises(TypeError):
            decorar_para_hash("tabela")  # type: ignore[call-overload]
