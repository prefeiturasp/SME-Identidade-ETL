from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("staging", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="stagingusuario",
            name="rf",
            field=models.CharField(
                blank=True,
                help_text="Registro Funcional",
                max_length=255,
                null=True,
            ),
        ),
    ]
