"""Estende ControleProvisionamento com hashes por estágio.

Renomeia ``hash_conteudo`` (só Keycloak) para ``hash_keycloak`` e
adiciona ``hash_extracao``/``hash_token_ms`` — cada estágio do
pipeline (extração, Keycloak, token-ms) passa a decidir
independentemente se precisa reexecutar, sem depender só de 1 hash.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "controle_etl",
            "0002_delete_perfilprovisionado_delete_sistemaprovisionado",
        ),
    ]

    operations = [
        migrations.RenameField(
            model_name="controleprovisionamento",
            old_name="hash_conteudo",
            new_name="hash_keycloak",
        ),
        migrations.AlterField(
            model_name="controleprovisionamento",
            name="hash_keycloak",
            field=models.CharField(
                max_length=64,
                help_text="SHA-256 do payload enviado ao Keycloak",
            ),
        ),
        migrations.AddField(
            model_name="controleprovisionamento",
            name="hash_extracao",
            field=models.CharField(
                max_length=64,
                blank=True,
                null=True,
                help_text=(
                    "SHA-256 dos campos do staging pós-extração/resolução"
                ),
            ),
        ),
        migrations.AddField(
            model_name="controleprovisionamento",
            name="hash_token_ms",
            field=models.CharField(
                max_length=64,
                blank=True,
                null=True,
                help_text="SHA-256 do payload enviado ao token-ms",
            ),
        ),
        migrations.RemoveIndex(
            model_name="controleprovisionamento",
            name="idx_prov_hash",
        ),
        migrations.AddIndex(
            model_name="controleprovisionamento",
            index=models.Index(
                fields=["hash_keycloak"], name="idx_prov_hash"
            ),
        ),
    ]
