from django.db import migrations, models


class Migration(migrations.Migration):
    """Adiciona índice composto (execution_id, status) na staging_usuario.

    O índice já foi criado via CONCURRENTLY direto no DB. Esta migração
    registra o estado no Django para consistência. Usa IF NOT EXISTS
    para ser idempotente.
    """

    dependencies = [("staging", "0003_rename_dedup_table")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="CREATE INDEX IF NOT EXISTS idx_stg_usuario_exec_status ON staging_usuario (execution_id, status);",
                    reverse_sql="DROP INDEX IF EXISTS idx_stg_usuario_exec_status;",
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="stagingusuario",
                    index=models.Index(
                        fields=["execution_id", "status"],
                        name="idx_stg_usuario_exec_status",
                    ),
                ),
            ],
        ),
    ]
