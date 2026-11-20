"""Verifica a confirmação do watermark junto à persistência da extração."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.controle_etl.models import MarcaDaguaExtracao
from apps.extracao.dtos import RegistroIdentidade
from apps.extracao.services.usuarios import CoreSSOService
from apps.staging.models import UsuarioTerceiroStaging


def _registros(total: int) -> Iterator[RegistroIdentidade]:
    for indice in range(total):
        yield RegistroIdentidade(
            fonte="coresso",
            tipo="terceiro",
            cpf=f"{indice:011}",
            nome=f"Pessoa {indice}",
        )


@pytest.mark.django_db
class TestBaseETLService:
    """Exercita o fluxo comum com a persistência real no staging."""

    @pytest.fixture(autouse=True)
    def sem_limite_local(self, settings: Any) -> None:
        """Evita que o limite do ambiente local interfira nos cenários."""
        settings.ETL_LOTE_MAXIMO = 0

    def test_confirma_apos_persistir_e_commit(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        inicio = timezone.now()
        with (
            patch.object(
                CoreSSOService,
                "_extrair_registros",
                return_value=_registros(2),
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            total = CoreSSOService().executar(id_execucao=str(uuid4()))
            assert total == 2
            assert UsuarioTerceiroStaging.objects.count() == 2
            assert not MarcaDaguaExtracao.objects.exists()
        marca = MarcaDaguaExtracao.objects.get(fonte="coresso")
        assert marca.total_processado == 2
        assert marca.ultimo_processado_em is not None
        assert inicio <= marca.ultimo_processado_em <= timezone.now()

    def test_falha_no_ultimo_lote_preserva_watermark(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        anterior = timezone.now() - timedelta(days=1)
        marca = MarcaDaguaExtracao.objects.create(
            fonte="coresso", ultimo_processado_em=anterior, total_processado=5
        )
        persistir = UsuarioTerceiroStaging.objects.bulk_create

        def falhar_no_ultimo_lote(lote: list, **kwargs: Any) -> list:
            if len(lote) == 1:
                raise DatabaseError("falha no último lote")
            return persistir(lote, **kwargs)

        with (
            patch.object(
                CoreSSOService,
                "_extrair_registros",
                return_value=_registros(501),
            ),
            patch.object(
                UsuarioTerceiroStaging.objects,
                "bulk_create",
                side_effect=falhar_no_ultimo_lote,
            ),
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            pytest.raises(DatabaseError, match="último lote"),
        ):
            CoreSSOService().executar(id_execucao=str(uuid4()))
        assert not callbacks
        assert UsuarioTerceiroStaging.objects.count() == 500
        marca.refresh_from_db()
        assert marca.ultimo_processado_em == anterior
        assert marca.total_processado == 5

    def test_falha_na_origem_nao_confirma_carga(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        def origem_com_falha() -> Iterator[RegistroIdentidade]:
            yield from _registros(1)
            raise ConnectionError("origem indisponível")

        with (
            patch.object(
                CoreSSOService,
                "_extrair_registros",
                return_value=origem_com_falha(),
            ),
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            pytest.raises(ConnectionError),
        ):
            CoreSSOService().executar(id_execucao=str(uuid4()))
        assert not callbacks
        assert not MarcaDaguaExtracao.objects.exists()

    def test_limite_parcial_nao_avanca_watermark(
        self, settings: Any, django_capture_on_commit_callbacks: Any
    ) -> None:
        settings.ETL_LOTE_MAXIMO = 3
        with (
            patch.object(
                CoreSSOService,
                "_extrair_registros",
                return_value=_registros(4),
            ),
            django_capture_on_commit_callbacks(execute=True) as callbacks,
        ):
            assert CoreSSOService().executar(id_execucao=str(uuid4())) == 3
        assert UsuarioTerceiroStaging.objects.count() == 3
        assert not callbacks
        assert not MarcaDaguaExtracao.objects.exists()

    def test_rollback_nao_confirma_watermark(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        with (
            patch.object(
                CoreSSOService,
                "_extrair_registros",
                return_value=_registros(1),
            ),
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            pytest.raises(ValueError, match="rollback"),
            transaction.atomic(),
        ):
            CoreSSOService().executar(id_execucao=str(uuid4()))
            raise ValueError("rollback")
        assert not callbacks
        assert not UsuarioTerceiroStaging.objects.exists()
        assert not MarcaDaguaExtracao.objects.exists()

    def test_leitura_em_streaming_nao_avanca_watermark(self) -> None:
        consumidos: list[int] = []

        def origem() -> Iterator[RegistroIdentidade]:
            for indice, registro in enumerate(_registros(3)):
                consumidos.append(indice)
                yield registro

        with patch.object(
            CoreSSOService, "_extrair_registros", return_value=origem()
        ):
            registros = CoreSSOService().extrair()
            next(registros)
            assert consumidos == [0]
            list(registros)
        assert consumidos == [0, 1, 2]
        assert not MarcaDaguaExtracao.objects.exists()

    def test_persistencia_com_falha_fecha_origem(self) -> None:
        fechada: list[bool] = []

        def origem() -> Iterator[RegistroIdentidade]:
            try:
                yield from _registros(3)
            finally:
                fechada.append(True)

        def persistir(registros: Iterator, **kwargs: Any) -> int:
            next(registros)
            raise DatabaseError("falha ao gravar")

        with (
            patch.object(
                CoreSSOService, "_extrair_registros", return_value=origem()
            ),
            patch(
                "apps.staging.tasks.persistir_extracao_staging",
                side_effect=persistir,
            ),
            pytest.raises(DatabaseError),
        ):
            CoreSSOService().executar(id_execucao=str(uuid4()))
        assert fechada == [True]
        assert not MarcaDaguaExtracao.objects.exists()

    def test_fonte_vazia_nao_cria_watermark(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        with (
            patch.object(
                CoreSSOService,
                "_extrair_registros",
                return_value=_registros(0),
            ),
            django_capture_on_commit_callbacks(execute=True) as callbacks,
        ):
            assert CoreSSOService().executar(id_execucao=str(uuid4())) == 0
        assert not callbacks
        assert not MarcaDaguaExtracao.objects.exists()

    def test_falha_na_segunda_pagina_nao_confirma_watermark(
        self, settings: Any, django_capture_on_commit_callbacks: Any
    ) -> None:
        settings.CORESSO_DB_SERVIDOR = ""
        settings.CORESSO_API_URL = "https://origem.example"
        resposta = MagicMock()
        resposta.json.return_value = {
            "results": [{"cpf": "12345678901", "nome": "Pessoa"}],
            "next": "pagina-2",
        }
        with (
            patch("httpx.Client") as cliente,
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            pytest.raises(httpx.ConnectError),
        ):
            cliente.return_value.__enter__.return_value.get.side_effect = [
                resposta,
                httpx.ConnectError("falha na página 2"),
            ]
            CoreSSOService().executar(id_execucao=str(uuid4()))
        assert not callbacks
        assert not MarcaDaguaExtracao.objects.exists()
