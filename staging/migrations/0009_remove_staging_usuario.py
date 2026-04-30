# Migration 0009 — Remove staging_usuario (tabela legada) e atualiza DedupResult.
# DedupResult.winner e DedupResult.loser eram FKs para staging_usuario;
# agora são UUIDFields simples (sem FK) com campos de tipo auxiliares.

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("staging", "0008_staging_usuario_tipos"),
    ]

    operations = [
        # 1. Remover FKs de DedupResult para staging_usuario
        migrations.RemoveField(
            model_name="dedupresult",
            name="winner",
        ),
        migrations.RemoveField(
            model_name="dedupresult",
            name="loser",
        ),

        # 2. Adicionar novos campos UUIDField sem FK
        migrations.AddField(
            model_name="dedupresult",
            name="winner",
            field=models.UUIDField(
                blank=True,
                null=True,
                help_text="UUID do registro vencedor em staging_usuario_servidor/aluno/terceiro",
            ),
        ),
        migrations.AddField(
            model_name="dedupresult",
            name="winner_type",
            field=models.CharField(
                blank=True,
                max_length=20,
                null=True,
                help_text="Tipo do vencedor: servidor | aluno | terceiro",
            ),
        ),
        migrations.AddField(
            model_name="dedupresult",
            name="loser",
            field=models.UUIDField(
                blank=True,
                null=True,
                help_text="UUID do registro perdedor (marcado como SKIPPED)",
            ),
        ),
        migrations.AddField(
            model_name="dedupresult",
            name="loser_type",
            field=models.CharField(
                blank=True,
                max_length=20,
                null=True,
                help_text="Tipo do perdedor: servidor | aluno | terceiro",
            ),
        ),

        # 3. Remover o modelo StagingUsuario (tabela staging_usuario)
        migrations.DeleteModel(
            name="StagingUsuario",
        ),
    ]
