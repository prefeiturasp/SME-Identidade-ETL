"""Renomeia staging_dedup_result → identidade_dedup_result.

Motivo: a tabela de resultado final de deduplicação não é tabela de staging —
é um artefato compartilhável com outros microsserviços (audit-ms, admin-ms).
O prefixo "staging_" causa confusão semântica.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("staging", "0002_fix_rf_max_length"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="dedupresult",
            table="identidade_dedup_result",
        ),
    ]
