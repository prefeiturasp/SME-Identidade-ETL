"""Migration: adiciona max_records_extract, user_types e skip_steps ao ETLExecution."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_etlexecution_max_records"),
    ]

    operations = [
        migrations.AddField(
            model_name="etlexecution",
            name="max_records_extract",
            field=models.IntegerField(
                blank=True,
                null=True,
                help_text="Limita o número de registros extraídos por fonte (steps 1 e 2). Nulo = sem limite.",
            ),
        ),
        migrations.AddField(
            model_name="etlexecution",
            name="user_types",
            field=models.CharField(
                default="all",
                max_length=100,
                help_text="Tipos de usuário a processar: 'all', 'servidor', 'aluno', 'terceiro' ou combinações separadas por vírgula.",
            ),
        ),
        migrations.AddField(
            model_name="etlexecution",
            name="skip_steps",
            field=models.JSONField(
                default=list,
                blank=True,
                help_text="Lista de nomes de steps a pular.",
            ),
        ),
    ]
