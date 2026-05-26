"""Migration: adiciona max_records ao ETLExecution para limitar carga em testes."""
from django.db import migrations, models


class Migration(migrations.Migration):
    """Adiciona campo max_records para limitar registros sincronizados no Keycloak."""

    dependencies = [
        ("core", "0002_etlexecution_load_keycloak_load_token_ms"),
    ]

    operations = [
        migrations.AddField(
            model_name="etlexecution",
            name="max_records",
            field=models.IntegerField(
                null=True,
                blank=True,
                help_text="Limita o número de usuários sincronizados no step 6 (KC). Nulo = sem limite.",
            ),
        ),
    ]
