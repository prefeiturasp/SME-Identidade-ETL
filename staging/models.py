import uuid

from django.db import models


class StagingUsuarioBase(models.Model):

    class Source(models.TextChoices):
        SE1426 = "se1426", "SE1426 (PRODAM)"
        EOL_DB = "eol_db", "EOL_DB"
        CORESSO = "coresso", "CORESSO"

    class Status(models.TextChoices):
        RAW = "raw", "Bruto (recém extraído)"
        TRANSFORMED = "transformed", "Transformado"
        READY = "ready", "Pronto para carga"
        LOADED = "loaded", "Carregado"
        SKIPPED = "skipped", "Ignorado (dedup)"
        ERROR = "error", "Erro"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cpf = models.CharField(max_length=14, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    nome = models.CharField(max_length=255, blank=True, null=True)
    data_nascimento = models.DateField(blank=True, null=True)
    situacao = models.CharField(max_length=50, blank=True, null=True)
    source = models.CharField(max_length=20, choices=Source.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RAW)
    execution_id = models.UUIDField(help_text="ID da execução ETL que extraiu este registro")
    raw_data = models.JSONField(default=dict, blank=True)
    error_detail = models.TextField(blank=True, null=True)
    extracted_at = models.DateTimeField(auto_now_add=True)
    transformed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        abstract = True


class StagingUsuarioServidor(StagingUsuarioBase):

    rf = models.CharField(max_length=255, blank=True, null=True, help_text="Registro Funcional")
    cargo = models.CharField(max_length=255, blank=True, null=True)
    funcao = models.CharField(max_length=255, blank=True, null=True)
    lotacao = models.CharField(max_length=255, blank=True, null=True, help_text="Código da UE/DRE")
    lotacao_nome = models.CharField(max_length=500, blank=True, null=True)
    dre = models.CharField(max_length=100, blank=True, null=True)
    ue = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = "staging_usuario_servidor"
        ordering = ["-extracted_at"]
        verbose_name = "Staging Usuário Servidor"
        verbose_name_plural = "Staging Usuários Servidores"
        indexes = [
            models.Index(fields=["rf"], name="idx_stg_srv_rf"),
            models.Index(fields=["cpf"], name="idx_stg_srv_cpf"),
            models.Index(fields=["source", "status"], name="idx_stg_srv_src_status"),
            models.Index(fields=["execution_id"], name="idx_stg_srv_exec"),
            models.Index(fields=["execution_id", "status"], name="idx_stg_srv_exec_status"),
        ]

    def __str__(self):
        return f"[srv/{self.source}] {self.rf or self.cpf} — {self.nome}"


class StagingUsuarioAluno(StagingUsuarioBase):

    matricula = models.CharField(max_length=20, blank=True, null=True)
    cod_escola = models.CharField(max_length=20, blank=True, null=True)
    turma = models.CharField(max_length=50, blank=True, null=True)
    dre = models.CharField(max_length=100, blank=True, null=True)
    ue = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        db_table = "staging_usuario_aluno"
        ordering = ["-extracted_at"]
        verbose_name = "Staging Usuário Aluno"
        verbose_name_plural = "Staging Usuários Alunos"
        indexes = [
            models.Index(fields=["matricula"], name="idx_stg_aluno_matricula"),
            models.Index(fields=["cpf"], name="idx_stg_aluno_cpf"),
            models.Index(fields=["source", "status"], name="idx_stg_aluno_src_status"),
            models.Index(fields=["execution_id"], name="idx_stg_aluno_exec"),
        ]

    def __str__(self):
        return f"[aluno/{self.source}] {self.matricula or self.cpf} — {self.nome}"


class StagingUsuarioTerceiro(StagingUsuarioBase):

    tipo_acesso = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="Ex: convidado, parceiro, legado-coresso",
    )

    class Meta:
        db_table = "staging_usuario_terceiro"
        ordering = ["-extracted_at"]
        verbose_name = "Staging Usuário Terceiro"
        verbose_name_plural = "Staging Usuários Terceiros"
        indexes = [
            models.Index(fields=["cpf"], name="idx_stg_terc_cpf"),
            models.Index(fields=["source", "status"], name="idx_stg_terc_src_status"),
            models.Index(fields=["execution_id"], name="idx_stg_terc_exec"),
        ]

    def __str__(self):
        return f"[terc/{self.source}] {self.cpf or self.email} — {self.nome}"



class StagingPerfil(models.Model):
    """Dados de perfil/cargo extraídos para mapeamento de roles Keycloak.

"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Mapeamento
    cargo_codigo = models.CharField(max_length=50)
    cargo_nome = models.CharField(max_length=255)
    funcao_codigo = models.CharField(max_length=50, blank=True, null=True)
    funcao_nome = models.CharField(max_length=255, blank=True, null=True)

    # Role Keycloak mapeada
    keycloak_role = models.CharField(
        max_length=100,
        help_text="Nome da role Keycloak correspondente",
    )
    keycloak_group_path = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Path do grupo Keycloak, e.g. /SME/DRE-BT",
    )

    # Controle
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=50, default="manual")
    execution_id = models.UUIDField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staging_perfil"
        verbose_name = "Staging Perfil"
        verbose_name_plural = "Staging Perfis"
        unique_together = [("cargo_codigo", "funcao_codigo")]

    def __str__(self):
        return f"{self.cargo_codigo} → {self.keycloak_role}"


class StagingLotacao(models.Model):
    """Dados de lotação para mapeamento de grupos (hierarquia DRE/UE).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    codigo = models.CharField(max_length=50, unique=True)
    nome = models.CharField(max_length=500)
    tipo = models.CharField(
        max_length=20,
        help_text="dre, ue, sme",
    )
    dre_codigo = models.CharField(max_length=50, blank=True, null=True)
    dre_nome = models.CharField(max_length=255, blank=True, null=True)

    # Group path no Keycloak
    keycloak_group_path = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="E.g. /SME/DRE-BT/EMEF-ABC",
    )
    keycloak_group_id = models.CharField(max_length=255, blank=True, null=True)

    is_active = models.BooleanField(default=True)
    execution_id = models.UUIDField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staging_lotacao"
        verbose_name = "Staging Lotação"
        verbose_name_plural = "Staging Lotações"
        indexes = [
            models.Index(fields=["tipo"], name="idx_stg_lotacao_tipo"),
            models.Index(fields=["dre_codigo"], name="idx_stg_lotacao_dre"),
        ]

    def __str__(self):
        return f"[{self.tipo}] {self.codigo} — {self.nome}"


class StagingSistema(models.Model):

    class Status(models.TextChoices):
        RAW = "raw", "Bruto (extraído)"
        READY = "ready", "Pronto p/ Keycloak"
        LOADED = "loaded", "Cliente criado no Keycloak"
        ERROR = "error", "Erro"
        SKIPPED = "skipped", "Pulado (inativo)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    coresso_sis_id = models.IntegerField(unique=True, db_index=True)
    nome = models.CharField(max_length=255)
    descricao = models.TextField(blank=True, null=True)
    sigla = models.CharField(max_length=50, blank=True, null=True)
    url_callback = models.CharField(max_length=2000, blank=True, null=True)
    url_logout = models.CharField(max_length=2000, blank=True, null=True)
    tipo_autenticacao = models.IntegerField(blank=True, null=True)
    situacao = models.IntegerField(default=1)

    kc_client_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="clientId no Keycloak (e.g. sgp-prod)",
    )
    kc_client_uuid = models.CharField(max_length=255, blank=True, null=True)
    kc_realm = models.CharField(max_length=100, blank=True, null=True)
    kc_registration_access_token = models.TextField(
        blank=True,
        null=True,
        help_text="Registration access token gerado pelo Keycloak para auto-gerenciamento do cliente.",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RAW,
    )
    error_detail = models.TextField(blank=True, null=True)
    raw_data = models.JSONField(default=dict, blank=True)
    execution_id = models.UUIDField(blank=True, null=True)

    extracted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staging_sistema"
        verbose_name = "Staging Sistema"
        verbose_name_plural = "Staging Sistemas"
        indexes = [
            models.Index(fields=["status"], name="idx_stg_sistema_status"),
            models.Index(fields=["sigla"], name="idx_stg_sistema_sigla"),
        ]

    def __str__(self):
        return f"[{self.coresso_sis_id}] {self.nome} → {self.kc_client_id or '-'}"


class StagingPerfilCoreSSO(models.Model):

    class Status(models.TextChoices):
        RAW = "raw", "Bruto"
        READY = "ready", "Pronto"
        LOADED = "loaded", "Role criada no Keycloak"
        ERROR = "error", "Erro"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    coresso_gru_id = models.CharField(max_length=64, unique=True, db_index=True)
    nome = models.CharField(max_length=255)
    coresso_sis_id = models.IntegerField(db_index=True)
    coresso_vis_id = models.IntegerField(blank=True, null=True)
    situacao = models.IntegerField(default=1)

    sistema = models.ForeignKey(
        StagingSistema,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="perfis",
    )

    kc_role_name = models.CharField(max_length=255, blank=True, null=True)
    kc_role_id = models.CharField(max_length=255, blank=True, null=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RAW
    )
    error_detail = models.TextField(blank=True, null=True)
    execution_id = models.UUIDField(blank=True, null=True)

    extracted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staging_perfil_coresso"
        verbose_name = "Staging Perfil CoreSSO"
        verbose_name_plural = "Staging Perfis CoreSSO"
        indexes = [
            models.Index(fields=["coresso_sis_id"], name="idx_stg_perfco_sis"),
            models.Index(fields=["status"], name="idx_stg_perfco_status"),
        ]

    def __str__(self):
        return f"[{self.coresso_sis_id}/{self.coresso_gru_id[:8]}] {self.nome}"


class RetroalimentacaoCoreSSO(models.Model):

    class Tipo(models.TextChoices):
        USER_CREATED = "user_created", "Usuário criado"
        USER_UPDATED = "user_updated", "Usuário atualizado"
        ROLE_ASSIGNED = "role_assigned", "Role atribuída"
        ROLE_REVOKED = "role_revoked", "Role revogada"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        DELIVERED = "delivered", "Entregue"
        FAILED = "failed", "Falha"
        SKIPPED = "skipped", "Pulado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    tipo = models.CharField(max_length=30, choices=Tipo.choices, db_index=True)
    rf = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    cpf = models.CharField(max_length=14, blank=True, null=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    delivery_attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, null=True)
    execution_id = models.UUIDField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "retroalim_coresso"
        verbose_name = "Retroalimentação CoreSSO"
        verbose_name_plural = "Retroalimentações CoreSSO"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "tipo"], name="idx_retro_status_tipo"),
        ]

    def __str__(self):
        return f"[{self.tipo}] {self.rf or self.cpf} ({self.status})"


class DedupResult(models.Model):

    class MatchType(models.TextChoices):
        CPF_EXACT = "cpf_exact", "CPF exato"
        RF_EXACT = "rf_exact", "RF exato"
        CPF_RF_CROSS = "cpf_rf_cross", "CPF↔RF cruzado (mesma pessoa, fontes diferentes)"
        MANUAL = "manual", "Resolução manual"

    class Decision(models.TextChoices):
        MERGE = "merge", "Mesclar (enriquecer dados)"
        KEEP_WINNER = "keep_winner", "Manter registro vencedor"
        SKIP_DUPLICATE = "skip_duplicate", "Ignorar duplicata"
        CONFLICT = "conflict", "Conflito (requer revisão)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    dedup_key = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Chave canônica: 'cpf:12345678901' ou 'rf:123456'",
    )

    winner = models.UUIDField(
        null=True,
        blank=True,
        help_text="UUID do registro vencedor em staging_usuario_servidor/aluno/terceiro",
    )
    winner_type = models.CharField(
        max_length=20, blank=True, null=True,
        help_text="Tipo do vencedor: servidor | aluno | terceiro",
    )
    loser = models.UUIDField(
        null=True,
        blank=True,
        help_text="UUID do registro perdedor (marcado como SKIPPED)",
    )
    loser_type = models.CharField(
        max_length=20, blank=True, null=True,
        help_text="Tipo do perdedor: servidor | aluno | terceiro",
    )

    match_type = models.CharField(max_length=20, choices=MatchType.choices)
    decision = models.CharField(max_length=20, choices=Decision.choices)

    merged_fields = models.JSONField(
        default=list,
        blank=True,
        help_text="Lista de campos enriquecidos do loser para o winner",
    )

    cpf = models.CharField(max_length=11, blank=True, null=True, db_index=True)
    rf = models.CharField(max_length=20, blank=True, null=True, db_index=True)

    execution_id = models.UUIDField(db_index=True)
    confidence = models.FloatField(
        default=1.0,
        help_text="Grau de confiança do match (1.0 = exato, <1.0 = heurístico)",
    )
    reviewed = models.BooleanField(default=False, help_text="Revisado manualmente?")
    review_note = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identidade_dedup_result"
        ordering = ["-created_at"]
        verbose_name = "Resultado de Deduplicação"
        verbose_name_plural = "Resultados de Deduplicação"
        indexes = [
            models.Index(fields=["execution_id", "match_type"], name="idx_dedup_exec_type"),
            models.Index(fields=["decision"], name="idx_dedup_decision"),
        ]

    def __str__(self):
        return f"[{self.match_type}] {self.dedup_key} → {self.decision}"
