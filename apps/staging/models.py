"""Models de staging intermediário do pipeline ETL.

Armazena registros extraídos das fontes (SE1426, CoreSSO, EOL_DB)
antes da carga nos sistemas de destino (Keycloak, token-ms).
Registros são particionados por id_execucao e limpos após a carga.
"""

from django.db import models


class _UsuarioStagingBase(models.Model):
    """Campos comuns a todos os tipos de usuário em staging."""

    id_execucao = models.UUIDField(db_index=True)
    fonte = models.CharField(max_length=50)
    cpf = models.CharField(max_length=11, blank=True, null=True)
    rf = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    nome = models.CharField(max_length=255, blank=True, null=True)
    email = models.CharField(max_length=255, blank=True, null=True)
    situacao = models.CharField(max_length=50, blank=True, null=True)
    detalhe_erro = models.TextField(blank=True, null=True)
    # situacao no pipeline: extraido → pronto → carregado / ignorado / erro
    lotacao = models.CharField(max_length=50, blank=True, null=True)
    lotacao_nome = models.CharField(max_length=255, blank=True, null=True)
    dre = models.CharField(max_length=50, blank=True, null=True)
    ue = models.CharField(max_length=50, blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UsuarioServidorStaging(_UsuarioStagingBase):
    """Servidor público extraído do SE1426 ou CoreSSO."""

    matricula = models.CharField(max_length=50, blank=True, null=True)
    cargo = models.CharField(max_length=100, blank=True, null=True)
    funcao = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = "staging_usuario_servidor"
        indexes = [
            models.Index(fields=["id_execucao", "situacao"]),
            models.Index(fields=["cpf"]),
        ]

    def __str__(self) -> str:
        return f"Servidor rf={self.rf} cpf={self.cpf} [{self.situacao}]"


class VinculoServidorStaging(models.Model):
    """Um vínculo funcional vigente de um servidor (SE1426).

    Um servidor pode ter múltiplos vínculos simultâneos e independentes
    (cargo base, cargo sobreposto/comissionado e função/atividade —
    inclusive mais de um cargo base ao mesmo tempo), cada um com sua
    própria unidade e DRE.
    """

    servidor = models.ForeignKey(
        UsuarioServidorStaging,
        on_delete=models.CASCADE,
        related_name="vinculos",
    )
    id_execucao = models.UUIDField(db_index=True)
    tipo_vinculo = models.CharField(max_length=20)
    codigo_vinculo_origem = models.CharField(max_length=50)
    cargo_codigo = models.CharField(max_length=50, blank=True, null=True)
    cargo_nome = models.CharField(max_length=255, blank=True, null=True)
    unidade_codigo = models.CharField(max_length=50, blank=True, null=True)
    unidade_nome = models.CharField(max_length=255, blank=True, null=True)
    dre_codigo = models.CharField(max_length=50, blank=True, null=True)
    situacao = models.CharField(max_length=50, blank=True, null=True)
    data_inicio = models.CharField(max_length=50, blank=True, null=True)
    vigente = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staging_vinculo_servidor"
        indexes = [
            models.Index(fields=["id_execucao"]),
            models.Index(fields=["servidor", "tipo_vinculo"]),
        ]

    def __str__(self) -> str:
        return (
            f"Vinculo {self.tipo_vinculo} servidor_id={self.servidor_id}"
            f" origem={self.codigo_vinculo_origem}"
        )


class UsuarioAlunoStaging(_UsuarioStagingBase):
    """Aluno extraído do EOL_DB."""

    cod_escola = models.CharField(max_length=20, blank=True, null=True)
    turma = models.CharField(max_length=50, blank=True, null=True)
    matricula = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = "staging_usuario_aluno"
        indexes = [
            models.Index(fields=["id_execucao", "situacao"]),
            models.Index(fields=["cpf"]),
        ]

    def __str__(self) -> str:
        return (
            f"Aluno cpf={self.cpf} escola={self.cod_escola} [{self.situacao}]"
        )


class UsuarioTerceiroStaging(_UsuarioStagingBase):
    """Terceirizado ou usuário externo."""

    tipo_acesso = models.CharField(max_length=50, blank=True, null=True)
    matricula = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = "staging_usuario_terceiro"
        indexes = [
            models.Index(fields=["id_execucao", "situacao"]),
            models.Index(fields=["cpf"]),
        ]

    def __str__(self) -> str:
        return (
            f"Terceiro cpf={self.cpf}"
            f" tipo={self.tipo_acesso} [{self.situacao}]"
        )


class SistemaStaging(models.Model):
    """Sistema CoreSSO pendente de provisionamento como client Keycloak."""

    class SituacaoProvisionamento(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        PROVISIONADO = "provisionado", "Provisionado"
        ERRO = "erro", "Erro"

    coresso_sis_id = models.IntegerField(unique=True, db_index=True)
    nome = models.CharField(max_length=255)
    sigla = models.CharField(max_length=50, blank=True, null=True)
    url_callback = models.CharField(max_length=500, blank=True, null=True)
    url_logout = models.CharField(max_length=500, blank=True, null=True)
    situacao = models.IntegerField(default=1)
    kc_client_id = models.CharField(max_length=255, blank=True, null=True)
    kc_client_uuid = models.CharField(max_length=100, blank=True, null=True)
    kc_realm = models.CharField(max_length=100, blank=True, null=True)
    situacao_provisionamento = models.CharField(
        max_length=20,
        choices=SituacaoProvisionamento.choices,
        default=SituacaoProvisionamento.PENDENTE,
    )
    detalhe_erro = models.TextField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staging_sistema"

    def __str__(self) -> str:
        return (
            f"Sistema {self.sigla or self.nome}"
            f" (sis_id={self.coresso_sis_id})"
        )


class PerfilCoressoStaging(models.Model):
    """Perfil/grupo CoreSSO pendente de provisionamento como role Keycloak."""

    class SituacaoProvisionamento(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        PROVISIONADO = "provisionado", "Provisionado"
        ERRO = "erro", "Erro"

    coresso_gru_id = models.CharField(
        max_length=50, unique=True, db_index=True
    )
    coresso_sis_id = models.IntegerField(db_index=True)
    sistema = models.ForeignKey(
        SistemaStaging,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="perfis",
    )
    nome = models.CharField(max_length=255)
    kc_role_nome = models.CharField(max_length=255, blank=True, null=True)
    kc_role_id = models.CharField(max_length=100, blank=True, null=True)
    situacao = models.IntegerField(default=1)
    situacao_provisionamento = models.CharField(
        max_length=20,
        choices=SituacaoProvisionamento.choices,
        default=SituacaoProvisionamento.PENDENTE,
    )
    detalhe_erro = models.TextField(blank=True, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staging_perfil_coresso"

    def __str__(self) -> str:
        return f"Perfil {self.nome} (gru_id={self.coresso_gru_id})"
