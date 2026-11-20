"""Fluxo comum de extração e confirmação do watermark."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterator
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.extracao.dtos import RegistroIdentidade

logger = logging.getLogger("etl_identidade")


class BaseETLService(ABC):
    """Coordena a leitura em streaming e a confirmação após persistência."""

    fonte: str

    def __init__(self) -> None:
        self._total_lido = 0
        self._concluida = False
        self._limitada = False
        self._iniciada_em = timezone.now()

    @abstractmethod
    def _extrair_registros(
        self, desde: datetime | None, apenas_ativos: bool
    ) -> Iterator[RegistroIdentidade]:
        """Produz os registros conforme as regras da fonte."""
        raise NotImplementedError

    def extrair(
        self,
        data_referencia: str | None = None,
        apenas_ativos: bool = False,
    ) -> Generator[RegistroIdentidade, None, None]:
        """Lê registros sem alterar o watermark.

        Args:
            data_referencia: Data ISO que substitui a marca persistida.
            apenas_ativos: Aplica os filtros disponíveis na fonte.

        Yields:
            Registros de identidade, limitados por ETL_LOTE_MAXIMO.
        """
        self._total_lido = 0
        self._concluida = False
        self._limitada = False
        self._iniciada_em = timezone.now()
        desde = (
            datetime.fromisoformat(data_referencia)
            if data_referencia
            else _obter_watermark(self.fonte)
        )
        logger.info("Extração %s — desde=%s", self.fonte, desde)
        limite = settings.ETL_LOTE_MAXIMO
        registros = self._extrair_registros(desde, apenas_ativos)
        try:
            for registro in registros:
                if limite and self._total_lido >= limite:
                    self._limitada = True
                    logger.info(
                        "%s: limite ETL_LOTE_MAXIMO=%d atingido",
                        self.fonte,
                        limite,
                    )
                    break
                self._total_lido += 1
                yield registro
            else:
                self._concluida = True
        finally:
            fechar = getattr(registros, "close", None)
            if fechar:
                fechar()

    def executar(
        self,
        *,
        id_execucao: str,
        data_referencia: str | None = None,
        apenas_ativos: bool = False,
    ) -> int:
        """Persista a extração e confirme o watermark após sucesso.

        Args:
            id_execucao: Identificador da execução no staging.
            data_referencia: Data ISO para repetir a leitura.
            apenas_ativos: Aplica os filtros disponíveis na fonte.

        Returns:
            Total de registros persistidos no staging.

        Raises:
            ValueError: Se a persistência não confirmar todos os registros.
        """
        from apps.staging.tasks import persistir_extracao_staging

        registros = self.extrair(data_referencia, apenas_ativos)
        try:
            total = persistir_extracao_staging(
                registros, id_execucao=id_execucao
            )
        finally:
            registros.close()
        if total != self._total_lido or not (
            self._concluida or self._limitada
        ):
            raise ValueError("A persistência não confirmou toda a extração.")
        if self._concluida and total:
            inicio = self._iniciada_em
            pagina = total // settings.ETL_CHUNK_SIZE
            transaction.on_commit(
                lambda: _atualizar_watermark(
                    self.fonte, inicio, total, ultima_pagina=pagina
                )
            )
        return total


_CPF_TAMANHO = 11


def _sanitizar_cpf(valor: object) -> str | None:
    """Normaliza CPF vindo de fontes legadas para 11 dígitos numéricos.

    Fontes como o CoreSSO podem armazenar o CPF com máscara
    (``000.000.000-00``) ou outros caracteres não numéricos, o que
    estoura o ``max_length=11`` da coluna de staging. Remove tudo que
    não for dígito antes de persistir.
    """
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos[:_CPF_TAMANHO] or None


def _sanitizar_email(valor: object) -> str | None:
    """Descarta e-mails sem domínio válido vindos de fontes legadas.

    O CoreSSO armazena, para uma fração dos usuários, um valor sem
    domínio (ex.: ``8bde384cd48ce311b1fe782bcb3d2d76@``, aparentando
    ser um GUID/hash truncado) no lugar do e-mail — o Keycloak rejeita
    esse formato com 400 ``error-invalid-email`` e o registro nunca é
    provisionado. Como não é um erro transitório (retry não resolve),
    descarta o valor aqui: o usuário segue sendo criado/atualizado no
    Keycloak, só sem e-mail, até a origem corrigir o dado.
    """
    email = str(valor or "").strip()
    usuario, _, dominio = email.partition("@")
    if not usuario or not dominio or "." not in dominio:
        return None
    if " " in email:
        return None
    return email


def _obter_watermark(fonte: str) -> datetime | None:
    """Retorna o último timestamp processado para a fonte.

    Args:
        fonte: Identificador da fonte (se1426, coresso, eol_alunos).

    Returns:
        datetime ou None se nunca processado.
    """
    from apps.controle_etl.models import MarcaDaguaExtracao

    marca = MarcaDaguaExtracao.objects.filter(fonte=fonte).first()
    return marca.ultimo_processado_em if marca else None


def _atualizar_watermark(
    fonte: str,
    ultimo_processado_em: datetime,
    total_processado: int,
    ultima_pagina: int = 0,
) -> None:
    """Atualiza o watermark após extração bem-sucedida.

    Args:
        fonte: Identificador da fonte.
        ultimo_processado_em: Timestamp do último registro processado.
        total_processado: Total de registros processados na execução.
        ultima_pagina: Número da última página processada.
    """
    from apps.controle_etl.models import MarcaDaguaExtracao

    marca, _ = MarcaDaguaExtracao.objects.get_or_create(fonte=fonte)
    marca.ultimo_processado_em = ultimo_processado_em
    marca.ultima_pagina = ultima_pagina
    marca.total_processado = (marca.total_processado or 0) + total_processado
    marca.save(
        update_fields=[
            "ultimo_processado_em",
            "ultima_pagina",
            "total_processado",
            "atualizado_em",
        ]
    )


def _paginar_api(
    url: str,
    cabecalhos: dict,
    timeout: int,
    desde: datetime | None,
    rotulo: str,
) -> Iterator[dict]:
    import httpx

    pagina = 1
    while True:
        params: dict = {
            "page": pagina,
            "page_size": settings.ETL_CHUNK_SIZE,
        }
        if desde:
            params["data_alteracao_apos"] = desde.strftime("%Y-%m-%d")

        try:
            with httpx.Client(timeout=timeout) as cliente:
                resposta = cliente.get(url, headers=cabecalhos, params=params)
            resposta.raise_for_status()
        except Exception as exc:
            logger.error("%s API: erro na página %d: %s", rotulo, pagina, exc)
            raise

        dados = resposta.json()
        itens = dados if isinstance(dados, list) else dados.get("results", [])
        if not itens:
            break

        yield from itens

        if isinstance(dados, list) or not dados.get("next"):
            break
        pagina += 1
