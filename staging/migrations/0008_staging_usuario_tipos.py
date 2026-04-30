# Generated migration — separação de staging_usuario em 3 tabelas por tipo.
# Cria: staging_usuario_servidor, staging_usuario_aluno, staging_usuario_terceiro.
# Migra dados existentes de staging_usuario para as novas tabelas.
# Torna DedupResult.winner nullable (era NOT NULL com CASCADE).

import uuid

import django.db.models.deletion
from django.db import migrations, models


def migrate_existing_data(apps, schema_editor):
    """Migra staging_usuario legado para as novas tabelas por tipo."""
    StagingUsuario = apps.get_model("staging", "StagingUsuario")
    StagingUsuarioServidor = apps.get_model("staging", "StagingUsuarioServidor")
    StagingUsuarioAluno = apps.get_model("staging", "StagingUsuarioAluno")
    StagingUsuarioTerceiro = apps.get_model("staging", "StagingUsuarioTerceiro")

    BATCH = 500

    servidores, alunos, terceiros = [], [], []

    for u in StagingUsuario.objects.iterator(chunk_size=1000):
        base = dict(
            id=u.id,
            cpf=u.cpf,
            email=u.email,
            nome=u.nome,
            data_nascimento=u.data_nascimento,
            situacao=u.situacao,
            source=u.source,
            status=u.status,
            execution_id=u.execution_id,
            raw_data=u.raw_data,
            error_detail=u.error_detail,
            extracted_at=u.extracted_at,
            transformed_at=u.transformed_at,
        )

        if u.rf:
            servidores.append(StagingUsuarioServidor(
                **base,
                rf=u.rf,
                cargo=u.cargo,
                funcao=u.funcao,
                lotacao=u.lotacao,
                lotacao_nome=u.lotacao_nome,
                dre=u.dre,
                ue=u.ue,
            ))
        elif u.matricula:
            alunos.append(StagingUsuarioAluno(
                **base,
                matricula=u.matricula,
                cod_escola=u.cod_escola,
                turma=u.turma,
                dre=u.dre,
                ue=u.ue,
            ))
        else:
            terceiros.append(StagingUsuarioTerceiro(
                **base,
                tipo_acesso="legado-coresso",
            ))

        if len(servidores) >= BATCH:
            StagingUsuarioServidor.objects.bulk_create(servidores, batch_size=BATCH, ignore_conflicts=True)
            servidores = []
        if len(alunos) >= BATCH:
            StagingUsuarioAluno.objects.bulk_create(alunos, batch_size=BATCH, ignore_conflicts=True)
            alunos = []
        if len(terceiros) >= BATCH:
            StagingUsuarioTerceiro.objects.bulk_create(terceiros, batch_size=BATCH, ignore_conflicts=True)
            terceiros = []

    if servidores:
        StagingUsuarioServidor.objects.bulk_create(servidores, batch_size=BATCH, ignore_conflicts=True)
    if alunos:
        StagingUsuarioAluno.objects.bulk_create(alunos, batch_size=BATCH, ignore_conflicts=True)
    if terceiros:
        StagingUsuarioTerceiro.objects.bulk_create(terceiros, batch_size=BATCH, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("staging", "0007_stagingsistema_kc_registration_access_token"),
    ]

    operations = [
        # ── 1. Criar tabela de servidores ────────────────────────────────
        migrations.CreateModel(
            name="StagingUsuarioServidor",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("cpf", models.CharField(blank=True, max_length=14, null=True)),
                ("email", models.EmailField(blank=True, max_length=254, null=True)),
                ("nome", models.CharField(blank=True, max_length=255, null=True)),
                ("data_nascimento", models.DateField(blank=True, null=True)),
                ("situacao", models.CharField(blank=True, max_length=50, null=True)),
                ("source", models.CharField(choices=[("se1426", "SE1426 (PRODAM)"), ("eol_db", "EOL_DB"), ("coresso", "CORESSO")], max_length=20)),
                ("status", models.CharField(choices=[("raw", "Bruto (recém extraído)"), ("transformed", "Transformado"), ("ready", "Pronto para carga"), ("loaded", "Carregado"), ("skipped", "Ignorado (dedup)"), ("error", "Erro")], default="raw", max_length=20)),
                ("execution_id", models.UUIDField(help_text="ID da execução ETL que extraiu este registro")),
                ("raw_data", models.JSONField(blank=True, default=dict)),
                ("error_detail", models.TextField(blank=True, null=True)),
                ("extracted_at", models.DateTimeField(auto_now_add=True)),
                ("transformed_at", models.DateTimeField(blank=True, null=True)),
                ("rf", models.CharField(blank=True, help_text="Registro Funcional", max_length=255, null=True)),
                ("cargo", models.CharField(blank=True, max_length=255, null=True)),
                ("funcao", models.CharField(blank=True, max_length=255, null=True)),
                ("lotacao", models.CharField(blank=True, help_text="Código da UE/DRE", max_length=255, null=True)),
                ("lotacao_nome", models.CharField(blank=True, max_length=500, null=True)),
                ("dre", models.CharField(blank=True, max_length=100, null=True)),
                ("ue", models.CharField(blank=True, max_length=100, null=True)),
            ],
            options={
                "verbose_name": "Staging Usuário Servidor",
                "verbose_name_plural": "Staging Usuários Servidores",
                "db_table": "staging_usuario_servidor",
                "ordering": ["-extracted_at"],
            },
        ),
        migrations.AddIndex(
            model_name="stagingusuarioservidor",
            index=models.Index(fields=["rf"], name="idx_stg_srv_rf"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioservidor",
            index=models.Index(fields=["cpf"], name="idx_stg_srv_cpf"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioservidor",
            index=models.Index(fields=["source", "status"], name="idx_stg_srv_src_status"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioservidor",
            index=models.Index(fields=["execution_id"], name="idx_stg_srv_exec"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioservidor",
            index=models.Index(fields=["execution_id", "status"], name="idx_stg_srv_exec_status"),
        ),

        # ── 2. Criar tabela de alunos ─────────────────────────────────────
        migrations.CreateModel(
            name="StagingUsuarioAluno",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("cpf", models.CharField(blank=True, max_length=14, null=True)),
                ("email", models.EmailField(blank=True, max_length=254, null=True)),
                ("nome", models.CharField(blank=True, max_length=255, null=True)),
                ("data_nascimento", models.DateField(blank=True, null=True)),
                ("situacao", models.CharField(blank=True, max_length=50, null=True)),
                ("source", models.CharField(choices=[("se1426", "SE1426 (PRODAM)"), ("eol_db", "EOL_DB"), ("coresso", "CORESSO")], max_length=20)),
                ("status", models.CharField(choices=[("raw", "Bruto (recém extraído)"), ("transformed", "Transformado"), ("ready", "Pronto para carga"), ("loaded", "Carregado"), ("skipped", "Ignorado (dedup)"), ("error", "Erro")], default="raw", max_length=20)),
                ("execution_id", models.UUIDField(help_text="ID da execução ETL que extraiu este registro")),
                ("raw_data", models.JSONField(blank=True, default=dict)),
                ("error_detail", models.TextField(blank=True, null=True)),
                ("extracted_at", models.DateTimeField(auto_now_add=True)),
                ("transformed_at", models.DateTimeField(blank=True, null=True)),
                ("matricula", models.CharField(blank=True, max_length=20, null=True)),
                ("cod_escola", models.CharField(blank=True, max_length=20, null=True)),
                ("turma", models.CharField(blank=True, max_length=50, null=True)),
                ("dre", models.CharField(blank=True, max_length=100, null=True)),
                ("ue", models.CharField(blank=True, max_length=100, null=True)),
            ],
            options={
                "verbose_name": "Staging Usuário Aluno",
                "verbose_name_plural": "Staging Usuários Alunos",
                "db_table": "staging_usuario_aluno",
                "ordering": ["-extracted_at"],
            },
        ),
        migrations.AddIndex(
            model_name="stagingusuarioaluno",
            index=models.Index(fields=["matricula"], name="idx_stg_aluno_matricula"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioaluno",
            index=models.Index(fields=["cpf"], name="idx_stg_aluno_cpf"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioaluno",
            index=models.Index(fields=["source", "status"], name="idx_stg_aluno_src_status"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioaluno",
            index=models.Index(fields=["execution_id"], name="idx_stg_aluno_exec"),
        ),

        # ── 3. Criar tabela de terceiros ──────────────────────────────────
        migrations.CreateModel(
            name="StagingUsuarioTerceiro",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("cpf", models.CharField(blank=True, max_length=14, null=True)),
                ("email", models.EmailField(blank=True, max_length=254, null=True)),
                ("nome", models.CharField(blank=True, max_length=255, null=True)),
                ("data_nascimento", models.DateField(blank=True, null=True)),
                ("situacao", models.CharField(blank=True, max_length=50, null=True)),
                ("source", models.CharField(choices=[("se1426", "SE1426 (PRODAM)"), ("eol_db", "EOL_DB"), ("coresso", "CORESSO")], max_length=20)),
                ("status", models.CharField(choices=[("raw", "Bruto (recém extraído)"), ("transformed", "Transformado"), ("ready", "Pronto para carga"), ("loaded", "Carregado"), ("skipped", "Ignorado (dedup)"), ("error", "Erro")], default="raw", max_length=20)),
                ("execution_id", models.UUIDField(help_text="ID da execução ETL que extraiu este registro")),
                ("raw_data", models.JSONField(blank=True, default=dict)),
                ("error_detail", models.TextField(blank=True, null=True)),
                ("extracted_at", models.DateTimeField(auto_now_add=True)),
                ("transformed_at", models.DateTimeField(blank=True, null=True)),
                ("tipo_acesso", models.CharField(blank=True, help_text="Ex: convidado, parceiro, legado-coresso", max_length=50, null=True)),
            ],
            options={
                "verbose_name": "Staging Usuário Terceiro",
                "verbose_name_plural": "Staging Usuários Terceiros",
                "db_table": "staging_usuario_terceiro",
                "ordering": ["-extracted_at"],
            },
        ),
        migrations.AddIndex(
            model_name="stagingusuarioterceiro",
            index=models.Index(fields=["cpf"], name="idx_stg_terc_cpf"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioterceiro",
            index=models.Index(fields=["source", "status"], name="idx_stg_terc_src_status"),
        ),
        migrations.AddIndex(
            model_name="stagingusuarioterceiro",
            index=models.Index(fields=["execution_id"], name="idx_stg_terc_exec"),
        ),

        # ── 4. Tornar DedupResult.winner nullable ─────────────────────────
        migrations.AlterField(
            model_name="dedupresult",
            name="winner",
            field=models.ForeignKey(
                blank=True,
                help_text="Registro vencedor — legado (StagingUsuario); null para novos registros",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dedup_won",
                to="staging.stagingusuario",
            ),
        ),

        # ── 5. Migrar dados existentes ────────────────────────────────────
        migrations.RunPython(migrate_existing_data, migrations.RunPython.noop),
    ]
