"""Modelos operacionais do SYNC_REC_DB.

Persistências de controle técnico do pipeline ETL:
execuções, watermark, checkpoints, rastreio de tentativas
e controle de provisionamento por idempotência.
"""

import uuid

from django.db import models
from django.utils import timezone

_Seconds = float


class ExecucaoETL(models.Model):
    """Registro de uma execução do pipeline ETL."""

    class Situacao(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        EXECUTANDO = "executando", "Executando"
        SUCESSO = "sucesso", "Sucesso"
        PARCIAL = "parcial", "Parcial (com erros)"
        FALHA = "falha", "Falha"
        CANCELADO = "cancelado", "Cancelado"

    class TipoDisparo(models.TextChoices):
        AGENDADO = "agendado", "Agendado (Beat)"
        MANUAL = "manual", "Manual (API)"

    id = models.BigAutoField(primary_key=True)
    id_execucao = models.UUIDField(
        unique=True,
        default=uuid.uuid4,
        editable=False,
    )
    fonte = models.CharField(
        max_length=20,
        default="todos",
        help_text="Fonte: todos | se1426 | coresso | eol_alunos",
    )
    realm_destino = models.CharField(max_length=100, default="sme-apps")
    tipo_disparo = models.CharField(
        max_length=20,
        choices=TipoDisparo.choices,
        default=TipoDisparo.AGENDADO,
    )
    situacao = models.CharField(
        max_length=20,
        choices=Situacao.choices,
        default=Situacao.PENDENTE,
    )
    total_extraido = models.IntegerField(default=0)
    total_transformado = models.IntegerField(default=0)
    total_carregado = models.IntegerField(default=0)
    total_erros = models.IntegerField(default=0)
    total_ignorados = models.IntegerField(default=0)
    iniciado_em = models.DateTimeField(null=True, blank=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    id_tarefa_celery = models.CharField(max_length=255, blank=True, null=True)
    observacao = models.TextField(blank=True, null=True)
    disparado_por = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        app_label = "controle_etl"
        db_table = "etl_execucao"
        ordering = ["-criado_em"]
        verbose_name = "Execução ETL"
        verbose_name_plural = "Execuções ETL"
        indexes = [
            models.Index(fields=["-criado_em"], name="idx_execucao_criado"),
            models.Index(fields=["situacao"], name="idx_execucao_situacao"),
            models.Index(fields=["fonte"], name="idx_execucao_fonte"),
        ]

    def __str__(self) -> str:
        return (
            f"Execução-{self.id_execucao.hex[:8]}"
            f" [{self.situacao}] {self.fonte}"
        )

    @property
    def duracao_segundos(self) -> float | None:
        """Duração da execução em segundos."""
        if self.iniciado_em and self.finalizado_em:
            delta = self.finalizado_em - self.iniciado_em
            resultado: _Seconds = delta.total_seconds()
            return resultado
        return None

    def marcar_executando(self) -> None:
        """Marca a execução como em andamento."""
        self.situacao = self.Situacao.EXECUTANDO
        self.iniciado_em = timezone.now()
        self.save(update_fields=["situacao", "iniciado_em", "atualizado_em"])

    def marcar_finalizada(self, situacao: str = "sucesso") -> None:
        """Finaliza a execução com a situação informada."""
        self.situacao = situacao
        self.finalizado_em = timezone.now()
        self.save(
            update_fields=[
                "situacao",
                "finalizado_em",
                "total_extraido",
                "total_transformado",
                "total_carregado",
                "total_erros",
                "total_ignorados",
                "atualizado_em",
            ]
        )


class LogEtapaETL(models.Model):
    """Log de uma etapa individual do pipeline ETL."""

    class NomeEtapa(models.TextChoices):
        EXTRAIR_SE1426 = (
            "task_identidade_extrair_se1426",
            "1. Extrair SE1426",
        )
        EXTRAIR_CORESSO = (
            "task_identidade_extrair_coresso",
            "2. Extrair CoreSSO",
        )
        EXTRAIR_EOL_ALUNOS = (
            "task_identidade_extrair_eol_alunos",
            "2b. Extrair EOL Alunos",
        )
        RESOLVER_IDENTIDADE = (
            "task_identidade_resolver_identidade",
            "3. Resolver Identidade",
        )
        PROVISIONAR_KEYCLOAK = (
            "task_provisionar_identidade_keycloak",
            "4. Provisionar Keycloak",
        )
        CARREGAR_TOKEN = (
            "task_carregar_atributos_token",
            "5. Carregar Atributos Token",
        )
        SYNC_REC = (
            "task_sync_rec_etl",
            "6. Registro Operacional",
        )

    class Situacao(models.TextChoices):
        EXECUTANDO = "executando", "Executando"
        SUCESSO = "sucesso", "Sucesso"
        FALHA = "falha", "Falha"
        IGNORADO = "ignorado", "Ignorado"

    id = models.BigAutoField(primary_key=True)
    execucao = models.ForeignKey(
        ExecucaoETL,
        on_delete=models.CASCADE,
        related_name="etapas",
    )
    nome_etapa = models.CharField(max_length=60, choices=NomeEtapa.choices)
    ordem_etapa = models.PositiveSmallIntegerField()
    situacao = models.CharField(
        max_length=20,
        choices=Situacao.choices,
        default=Situacao.EXECUTANDO,
    )
    registros_entrada = models.IntegerField(default=0)
    registros_saida = models.IntegerField(default=0)
    registros_erro = models.IntegerField(default=0)
    iniciado_em = models.DateTimeField(auto_now_add=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)
    detalhe_erro = models.TextField(blank=True, null=True)
    metadados = models.JSONField(default=dict, blank=True)

    class Meta:
        app_label = "controle_etl"
        db_table = "etl_log_etapa"
        ordering = ["execucao", "ordem_etapa"]
        verbose_name = "Log de Etapa ETL"
        verbose_name_plural = "Logs de Etapas ETL"
        unique_together = [("execucao", "nome_etapa")]

    def __str__(self) -> str:
        return (
            f"{self.execucao.id_execucao.hex[:8]}"
            f" → {self.nome_etapa} [{self.situacao}]"
        )


class ControleProvisionamento(models.Model):
    """Controle de idempotência para provisionamento no Keycloak.

    Garante que cada entidade seja provisionada apenas uma vez
    por conteúdo, evitando duplicidade em reprocessamentos.
    """

    class TipoEntidade(models.TextChoices):
        USUARIO = "usuario", "Usuário"
        GRUPO = "grupo", "Grupo"
        ROLE = "role", "Role"
        CLIENT = "client", "Client"

    id = models.BigAutoField(primary_key=True)
    tipo_entidade = models.CharField(
        max_length=20, choices=TipoEntidade.choices
    )
    sistema_origem = models.CharField(
        max_length=50,
        help_text="Sistema de origem: se1426, coresso",
    )
    id_origem = models.CharField(
        max_length=255,
        help_text="Identificador na fonte (RF, CPF, matrícula)",
    )
    id_destino = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="ID no Keycloak",
    )
    realm_destino = models.CharField(max_length=100, default="sme-apps")
    hash_conteudo = models.CharField(
        max_length=64,
        help_text="SHA-256 dos campos significativos",
    )
    versao = models.PositiveIntegerField(default=1)
    sincronizado_em = models.DateTimeField(auto_now=True)
    ultima_execucao = models.ForeignKey(
        ExecucaoETL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registros_provisionamento",
    )
    ativo = models.BooleanField(default=True)
    erro_sincronizacao = models.TextField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "controle_etl"
        db_table = "etl_controle_provisionamento"
        verbose_name = "Controle de Provisionamento"
        verbose_name_plural = "Controles de Provisionamento"
        unique_together = [
            ("tipo_entidade", "sistema_origem", "id_origem", "realm_destino")
        ]
        indexes = [
            models.Index(
                fields=["tipo_entidade", "sistema_origem"],
                name="idx_prov_entidade_sistema",
            ),
            models.Index(fields=["id_origem"], name="idx_prov_id_origem"),
            models.Index(fields=["hash_conteudo"], name="idx_prov_hash"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.tipo_entidade}:{self.sistema_origem}"
            f":{self.id_origem} v{self.versao}"
        )


class MarcaDaguaExtracao(models.Model):
    """Controle de watermark por fonte de dados.

    Persiste o ponto de parada de cada extração para
    permitir cargas incrementais (delta) nas execuções seguintes.
    """

    fonte = models.CharField(
        max_length=30,
        primary_key=True,
        help_text="Identificador da fonte: se1426 | coresso | eol_alunos",
    )
    ultimo_processado_em = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp do último registro processado com sucesso",
    )
    ultima_pagina = models.IntegerField(
        default=0,
        help_text="Última página processada na extração paginada",
    )
    total_processado = models.IntegerField(
        default=0,
        help_text="Total acumulado de registros processados",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "controle_etl"
        db_table = "etl_marca_dagua_extracao"
        verbose_name = "Marca D'Água de Extração"
        verbose_name_plural = "Marcas D'Água de Extração"

    def __str__(self) -> str:
        return (
            f"[{self.fonte}] último: {self.ultimo_processado_em}"
            f" pág: {self.ultima_pagina}"
        )


class CheckpointEtl(models.Model):
    """Checkpoint de retomada por execução ETL.

    Permite que uma execução interrompida seja retomada
    do ponto de parada, sem reprocessar registros já tratados.
    """

    id_execucao = models.UUIDField(
        unique=True,
        help_text="UUID da ExecucaoETL associada",
    )
    etapa = models.CharField(
        max_length=60,
        help_text="Nome da etapa (nome_tarefa da spec)",
    )
    pagina_atual = models.IntegerField(
        default=0,
        help_text="Índice da página ao interromper",
    )
    ultimo_id_processado = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Último ID processado antes da interrupção",
    )
    estado_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Estado arbitrário da etapa para retomada",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "controle_etl"
        db_table = "etl_checkpoint"
        verbose_name = "Checkpoint ETL"
        verbose_name_plural = "Checkpoints ETL"

    def __str__(self) -> str:
        return (
            f"Checkpoint {self.id_execucao.hex[:8]}"
            f" [{self.etapa}] pág {self.pagina_atual}"
        )


class RastreioTentativa(models.Model):
    """Rastreio de tentativas (retries) por tarefa e execução.

    Registra cada tentativa de uma tarefa, permitindo
    auditoria de falhas transitórias e análise de estabilidade.
    """

    id = models.BigAutoField(primary_key=True)
    id_execucao = models.UUIDField(db_index=True)
    nome_tarefa = models.CharField(max_length=60)
    numero_tentativa = models.PositiveSmallIntegerField()
    iniciado_em = models.DateTimeField(auto_now_add=True)
    erro = models.TextField(blank=True, null=True)
    duracao_segundos = models.FloatField(null=True, blank=True)

    class Meta:
        app_label = "controle_etl"
        db_table = "etl_rastreio_tentativa"
        verbose_name = "Rastreio de Tentativa"
        verbose_name_plural = "Rastreios de Tentativas"
        indexes = [
            models.Index(
                fields=["id_execucao", "nome_tarefa"],
                name="idx_rastreio_exec_tarefa",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.nome_tarefa} tentativa {self.numero_tentativa}"
            f" [{self.id_execucao.hex[:8]}]"
        )
