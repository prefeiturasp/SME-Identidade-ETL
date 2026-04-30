from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("staging", "0006_retroalimentacaocoresso_stagingperfilcoresso"),
    ]

    operations = [
        migrations.AddField(
            model_name="stagingsistema",
            name="kc_registration_access_token",
            field=models.TextField(
                blank=True,
                null=True,
                help_text="Registration access token gerado pelo Keycloak para auto-gerenciamento do cliente.",
            ),
        ),
    ]
