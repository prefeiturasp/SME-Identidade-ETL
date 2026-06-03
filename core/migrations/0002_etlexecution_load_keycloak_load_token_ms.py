"""Migration: adiciona load_keycloak e load_token_ms ao ETLExecution."""
from django.db import migrations, models


class Migration(migrations.Migration):
    """Adiciona campos de controle de carga ao ETLExecution."""

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="etlexecution",
            name="load_keycloak",
            field=models.BooleanField(
                default=False,
                help_text="Se True, o step 6 envia usuários para o Keycloak",
            ),
        ),
        migrations.AddField(
            model_name="etlexecution",
            name="load_token_ms",
            field=models.BooleanField(
                default=True,
                help_text="Se True, o step 7 envia payload para o Token-MS",
            ),
        ),
    ]
