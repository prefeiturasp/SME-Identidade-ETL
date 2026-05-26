"""Tasks Celery para extrair dados do SE1426, EOL_DB e CoreSSO para as tabelas de staging."""
import logging
import re
import unicodedata
from datetime import date

import httpx
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _build_se1426_conn_str() -> str:
    return (
        f"DRIVER={{FreeTDS}};"
        f"SERVER={settings.SE1426_DB_SERVER};"
        f"PORT=1433;"
        f"DATABASE={settings.SE1426_DB_NAME};"
        f"UID={settings.SE1426_DB_USER};"
        f"PWD={settings.SE1426_DB_PASSWORD};"
        f"TDS_Version=7.4;"
        f"ClientCharset=UTF-8;"
    )


def _row_to_servidor_se1426(item: dict, execution_id: str):
    """Converte um row do SE1426 em StagingUsuarioServidor."""
    from staging.models import StagingUsuarioServidor

    situacao_raw = item.get("situacao", "")
    return StagingUsuarioServidor(
        rf=item.get("rf"),
        nome=item.get("nome"),
        cpf=item.get("cpf"),
        email=item.get("email"),
        situacao=situacao_raw.lower() if situacao_raw else None,
        cargo=item.get("cargo") or None,
        funcao=item.get("funcao") or None,
        lotacao=item.get("cod_unidade") or None,
        dre=item.get("cod_dre") or None,
        source=StagingUsuarioServidor.Source.SE1426,
        execution_id=execution_id,
        raw_data={k: str(v) if v is not None else None for k, v in item.items()},
    )


def _extract_se1426_sql(execution_id: str, max_records: int | None = None) -> int:
    import pyodbc

    from staging.models import StagingUsuarioServidor

    BATCH_SIZE = settings.ETL_EXTRACT_BATCH_SIZE

    query = """
        SELECT
            s.cd_registro_funcional  AS rf,
            s.nm_pessoa              AS nome,
            s.cd_cpf_pessoa          AS cpf,
            s.situacao               AS situacao,
            e.dc_dispositivo         AS email,
            RTRIM(LTRIM(ISNULL(c.dc_cargo, '')))                     AS cargo,
            RTRIM(LTRIM(ISNULL(tf.dc_tipo_funcao, '')))              AS funcao,
            CAST(l.cd_unidade_educacao AS VARCHAR(20))               AS cod_unidade,
            CAST(ue.cd_unidade_administrativa_referencia AS VARCHAR(20)) AS cod_dre
        FROM v_servidor_sme_serap s
        LEFT JOIN v_servidor_cotic sc
            ON sc.cd_registro_funcional = s.cd_registro_funcional
        LEFT JOIN v_servidor_email_cotic e
            ON e.cd_servidor = sc.cd_servidor
            AND e.dt_fim IS NULL
        LEFT JOIN v_cargo_base_cotic cba
            ON cba.cd_servidor = sc.cd_servidor
            AND cba.dt_fim_nomeacao IS NULL
            AND cba.dt_cancelamento IS NULL
        LEFT JOIN cargo c
            ON c.cd_cargo = cba.cd_cargo
        LEFT JOIN funcao_atividade_cargo_servidor fa
            ON fa.cd_cargo_base_servidor = cba.cd_cargo_base_servidor
            AND fa.dt_fim_funcao_atividade IS NULL
        LEFT JOIN tipo_funcao_atividade tf
            ON tf.cd_tipo_funcao = fa.cd_tipo_funcao
        LEFT JOIN lotacao_servidor l
            ON l.cd_cargo_base_servidor = cba.cd_cargo_base_servidor
            AND l.dt_fim IS NULL
            AND l.dt_cancelamento IS NULL
        LEFT JOIN v_cadastro_unidade_educacao ue
            ON ue.cd_unidade_educacao = l.cd_unidade_educacao
    """

    conn = pyodbc.connect(_build_se1426_conn_str(), timeout=settings.SE1426_DB_TIMEOUT)
    try:
        _cnt = conn.cursor()
        _cnt.execute("SELECT COUNT(*) FROM v_servidor_sme_serap WITH (NOLOCK)")
        total_fonte = _cnt.fetchone()[0]
        _cnt.close()
        logger.info("[%s] SE1426: %d registros na fonte (pré-extração)", execution_id, total_fonte)

        cursor = conn.cursor()
        cursor.execute(query)

        total_extracted = 0
        while True:
            rows = cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break

            cols = [d[0] for d in cursor.description]
            staging_records = [
                _row_to_servidor_se1426(dict(zip(cols, row)), execution_id)
                for row in rows
            ]

            StagingUsuarioServidor.objects.bulk_create(staging_records, batch_size=BATCH_SIZE)
            total_extracted += len(staging_records)
            if max_records and total_extracted >= max_records:
                logger.info(
                    "[%s] SE1426: limite de %d registros atingido — interrompendo extração",
                    execution_id, max_records,
                )
                break

    finally:
        conn.close()

    return total_extracted


def _accumulate_eol_db_row(row, servidores: dict) -> None:
    """Acumula dados de um row EOL_DB no dict servidores (rf → dados)."""
    rf, cpf, nome, situacao, desc_cargo, cod_cargo, cod_unidade, cod_dre = row
    if rf not in servidores:
        servidores[rf] = {
            "rf": rf, "cpf": cpf, "nome": nome, "situacao": situacao,
            "desc_cargo": desc_cargo or None, "cod_cargo": cod_cargo or None,
            "unidades": [], "dres": [],
        }
    if cod_unidade:
        servidores[rf]["unidades"].append(cod_unidade)
    if cod_dre and cod_dre not in servidores[rf]["dres"]:
        servidores[rf]["dres"].append(cod_dre)


def _srv_dict_to_staging(srv: dict, execution_id: str):
    """Converte um dict de servidor EOL_DB em StagingUsuarioServidor."""
    from staging.models import StagingUsuarioServidor

    unidades = srv["unidades"]
    dres = srv["dres"]
    return StagingUsuarioServidor(
        rf=srv["rf"],
        cpf=srv["cpf"],
        nome=srv["nome"],
        situacao=srv["situacao"].lower() if srv["situacao"] else None,
        cargo=srv["desc_cargo"],
        lotacao=str(unidades[0]) if unidades else None,
        dre=str(dres[0]) if dres else None,
        source=StagingUsuarioServidor.Source.EOL_DB,
        execution_id=execution_id,
        raw_data={
            "rf": srv["rf"], "cpf": srv["cpf"],
            "cod_cargo": srv["cod_cargo"], "desc_cargo": srv["desc_cargo"],
            "unidades": unidades, "dres": dres, "fonte": "eol_db_sql",
        },
    )


def _extract_eol_db_sql(execution_id: str, max_records: int | None = None) -> int:
    import pyodbc

    from staging.models import StagingUsuarioServidor

    BATCH_SIZE = settings.ETL_EXTRACT_BATCH_SIZE

    query = """
        SELECT
            sc.cd_registro_funcional                                AS rf,
            s.cd_cpf_pessoa                                         AS cpf,
            s.nm_pessoa                                             AS nome,
            s.situacao                                              AS situacao,
            RTRIM(LTRIM(ISNULL(c.dc_cargo, '')))                    AS desc_cargo,
            CAST(ISNULL(cba.cd_cargo, '') AS VARCHAR(20))           AS cod_cargo,
            CAST(l.cd_unidade_educacao AS VARCHAR(20))              AS cod_unidade,
            CAST(ue.cd_unidade_administrativa_referencia AS VARCHAR(20)) AS cod_dre
        FROM v_servidor_sme_serap s
        INNER JOIN v_servidor_cotic sc
            ON sc.cd_registro_funcional = s.cd_registro_funcional
        LEFT JOIN v_cargo_base_cotic cba
            ON cba.cd_servidor = sc.cd_servidor
            AND cba.dt_fim_nomeacao IS NULL
            AND cba.dt_cancelamento IS NULL
        LEFT JOIN cargo c
            ON c.cd_cargo = cba.cd_cargo
        LEFT JOIN lotacao_servidor l
            ON l.cd_cargo_base_servidor = cba.cd_cargo_base_servidor
            AND l.dt_fim IS NULL
            AND l.dt_cancelamento IS NULL
        LEFT JOIN v_cadastro_unidade_educacao ue
            ON ue.cd_unidade_educacao = l.cd_unidade_educacao
        WHERE s.situacao = 'Ativo'
    """

    conn = pyodbc.connect(_build_se1426_conn_str(), timeout=settings.SE1426_DB_TIMEOUT)
    try:
        _cnt = conn.cursor()
        _cnt.execute(
            "SELECT COUNT(DISTINCT cd_registro_funcional) FROM v_servidor_sme_serap WITH (NOLOCK) WHERE situacao = 'Ativo'"
        )
        total_fonte = _cnt.fetchone()[0]
        _cnt.close()
        logger.info("[%s] EOL_DB: %d servidores únicos na fonte (pré-extração)", execution_id, total_fonte)

        cursor = conn.cursor()
        cursor.execute(query)

        servidores: dict = {}  # rf → {cpf, nome, situacao, cargo, unidades, dres}

        while True:
            rows = cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break
            for row in rows:
                _accumulate_eol_db_row(row, servidores)

    finally:
        conn.close()

    staging_records = []
    for srv in servidores.values():
        staging_records.append(_srv_dict_to_staging(srv, execution_id))
        if len(staging_records) >= BATCH_SIZE:
            StagingUsuarioServidor.objects.bulk_create(staging_records, batch_size=BATCH_SIZE)
            staging_records = []
            if max_records and len(servidores) >= max_records:
                logger.info(
                    "[%s] EOL_DB: limite de %d registros atingido — interrompendo extração",
                    execution_id, max_records,
                )
                break

    if staging_records:
        StagingUsuarioServidor.objects.bulk_create(staging_records, batch_size=BATCH_SIZE)

    return len(servidores)


@shared_task(bind=True, name="extract.tasks.extract_se1426", max_retries=3)
def extract_se1426(self, execution_id: str):
    """Extrai dados de servidores do banco SE1426 (PRODAM) para staging."""
    from core.models import ETLExecution, ETLStepLog
    from core.tasks import ExecutionCancelledError, _check_cancelled

    _check_cancelled(execution_id)

    execution = ETLExecution.objects.get(id=execution_id)
    step, _created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name=ETLStepLog.StepName.EXTRACT_SE1426,
        defaults={"step_order": 1},
    )
    if not _created:
        from staging.models import StagingUsuarioServidor
        deleted, _ = StagingUsuarioServidor.objects.filter(
            execution_id=execution_id, source=StagingUsuarioServidor.Source.SE1426
        ).delete()
        logger.warning(
            "[%s] extract_se1426: restart detectado — %d registros de staging removidos antes de re-extrair",
            execution_id, deleted,
        )
        step.status = ETLStepLog.StepStatus.RUNNING
        step.error_detail = None
        step.finished_at = None
        step.records_out = 0
        step.save(update_fields=["status", "error_detail", "finished_at", "records_out"])

    logger.info("[%s] Step 1: Extract SE1426", execution_id)

    try:
        if settings.SE1426_DB_SERVER:
            logger.info("[%s] SE1426: modo SQL Server direto", execution_id)
            step.metadata = {
                "mode": "sql_direct",
                "server": settings.SE1426_DB_SERVER,
                "database": settings.SE1426_DB_NAME,
            }
            step.save(update_fields=["metadata"])
            total_extracted = _extract_se1426_sql(execution_id, max_records=execution.max_records_extract)

        elif settings.SE1426_API_TOKEN:
            logger.info("[%s] SE1426: modo API REST", execution_id)
            step.metadata = {"mode": "api_rest", "url": settings.SE1426_API_URL}
            step.save(update_fields=["metadata"])
            total_extracted = _extract_se1426_api(execution_id)

        else:
            logger.warning("[%s] SE1426: nenhuma fonte configurada — skipping", execution_id)
            step.status = "skipped"
            step.metadata = {"note": "SE1426_DB_SERVER e SE1426_API_TOKEN ausentes"}
            step.finished_at = timezone.now()
            step.save()
            return 0

        step.records_out = total_extracted
        step.status = "success"
        step.finished_at = timezone.now()
        step.save()

        execution.total_extracted += total_extracted
        execution.save(update_fields=["total_extracted", "updated_at"])

        logger.info("[%s] Step 1 concluido: %d records from SE1426", execution_id, total_extracted)
        return total_extracted

    except Exception as e:
        logger.exception("[%s] Step 1 FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)[:2000]
        step.finished_at = timezone.now()
        step.save()
        if self.request.retries >= self.max_retries:
            # Não aborta a execução — chord continua com os demais sources
            logger.error("[%s] extract_se1426 esgotou retries — pipeline continua sem esta fonte", execution_id)
            return 0
        raise self.retry(exc=e, countdown=120)


def _extract_se1426_api(execution_id: str) -> int:
    yesterday = (timezone.now() - timezone.timedelta(days=1)).strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {settings.SE1426_API_TOKEN}",
        "Content-Type": "application/json",
    }

    page = 1
    total_extracted = 0
    has_more = True

    with httpx.Client(timeout=settings.SE1426_API_TIMEOUT) as client:
        while has_more:
            url = (
                f"{settings.SE1426_API_URL}/servidores/"
                f"?data_referencia={yesterday}&page={page}&page_size=500"
            )
            try:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as e:
                if page == 1:
                    raise
                logger.warning("SE1426 API error on page %d: %s", page, e)
                break

            results = data.get("results", data.get("data", []))
            if not results:
                break

            from staging.models import StagingUsuarioServidor
            staging_records = [
                StagingUsuarioServidor(
                    rf=item.get("rf", item.get("registro_funcional")),
                    cpf=item.get("cpf"),
                    email=item.get("email"),
                    nome=item.get("nome", item.get("nome_servidor")),
                    cargo=item.get("cargo", item.get("desc_cargo")),
                    funcao=item.get("funcao", item.get("desc_funcao")),
                    situacao=item.get("situacao", item.get("situacao_funcional")),
                    lotacao=item.get("lotacao", item.get("cod_lotacao")),
                    lotacao_nome=item.get("lotacao_nome", item.get("desc_lotacao")),
                    source=StagingUsuarioServidor.Source.SE1426,
                    execution_id=execution_id,
                    raw_data=item,
                )
                for item in results
            ]
            StagingUsuarioServidor.objects.bulk_create(staging_records, ignore_conflicts=False)
            total_extracted += len(staging_records)
            has_more = bool(data.get("next")) or len(results) == 500
            page += 1

    return total_extracted


@shared_task(bind=True, name="extract.tasks.extract_eol_db", max_retries=3)
def extract_eol_db(self, execution_id: str):
    """Extrai dados de servidores do EOL_DB (SQL Server) para staging."""
    from core.models import ETLExecution, ETLStepLog
    from core.tasks import _check_cancelled

    _check_cancelled(execution_id)

    execution = ETLExecution.objects.get(id=execution_id)
    step, _created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name=ETLStepLog.StepName.EXTRACT_EOL_DB,
        defaults={"step_order": 1},
    )
    if not _created:
        from staging.models import StagingUsuarioServidor
        deleted, _ = StagingUsuarioServidor.objects.filter(
            execution_id=execution_id, source=StagingUsuarioServidor.Source.EOL_DB
        ).delete()
        logger.warning(
            "[%s] extract_eol_db: restart detectado — %d registros de staging removidos antes de re-extrair",
            execution_id, deleted,
        )
        step.status = ETLStepLog.StepStatus.RUNNING
        step.error_detail = None
        step.finished_at = None
        step.records_out = 0
        step.save(update_fields=["status", "error_detail", "finished_at", "records_out"])

    logger.info("[%s] Step 1b: Extract EOL_DB (lotações/unidades)", execution_id)

    try:
        if not settings.SE1426_DB_SERVER:
            logger.warning("[%s] EOL_DB: SE1426_DB_SERVER não configurado — skipping", execution_id)
            step.status = "skipped"
            step.metadata = {"note": "SE1426_DB_SERVER ausente — EOL_DB usa mesmo servidor SE1426"}
            step.finished_at = timezone.now()
            step.save()
            return 0

        logger.info("[%s] EOL_DB: modo SQL Server direto (FreeTDS)", execution_id)
        step.metadata = {
            "mode": "sql_direct",
            "server": settings.SE1426_DB_SERVER,
            "database": settings.SE1426_DB_NAME,
            "tabela": "lotacao_servidor",
        }
        step.save(update_fields=["metadata"])

        total_extracted = _extract_eol_db_sql(execution_id, max_records=execution.max_records_extract)

        step.records_out = total_extracted
        step.status = "success"
        step.finished_at = timezone.now()
        step.save()

        execution.total_extracted += total_extracted
        execution.save(update_fields=["total_extracted", "updated_at"])

        logger.info(
            "[%s] Step 1b concluido: %d servidores com lotação do EOL_DB",
            execution_id, total_extracted,
        )
        return total_extracted

    except Exception as e:
        logger.exception("[%s] Step 1b FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)[:2000]
        step.finished_at = timezone.now()
        step.save()
        if self.request.retries >= self.max_retries:
            logger.error("[%s] extract_eol_db esgotou retries — pipeline continua sem esta fonte", execution_id)
            return 0
        raise self.retry(exc=e, countdown=120)


def _parse_date_str(date_str: str | None) -> date | None:
    """Converte string YYYY-MM-DD em date, retorna None se inválida."""
    if not date_str:
        return None
    try:
        parts = date_str.split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return None


def _row_to_aluno_sql(row, execution_id: str):
    """Converte um row EOL alunos em StagingUsuarioAluno."""
    from staging.models import StagingUsuarioAluno

    matricula = str(row.matricula).strip() if row.matricula else None
    nome = str(row.nome).strip() if row.nome else None
    data_nasc_str = str(row.data_nascimento).strip() if row.data_nascimento else None
    cod_escola = str(row.cod_escola).strip() if row.cod_escola else None
    turma = str(row.turma).strip() if row.turma else None
    cod_dre = str(row.cod_dre).strip() if row.cod_dre else None
    return StagingUsuarioAluno(
        matricula=matricula,
        nome=nome,
        data_nascimento=_parse_date_str(data_nasc_str),
        cod_escola=cod_escola,
        turma=turma,
        dre=cod_dre,
        ue=cod_escola,
        situacao="ativo",
        source=StagingUsuarioAluno.Source.EOL_DB,
        execution_id=execution_id,
        raw_data={
            "matricula": matricula, "cod_escola": cod_escola, "turma": turma,
            "cod_dre": cod_dre, "data_nascimento": data_nasc_str, "fonte": "eol_db_alunos",
        },
    )


def _extract_eol_alunos_sql(execution_id: str, max_records: int | None = None) -> int:
    import pyodbc

    from staging.models import StagingUsuarioAluno

    BATCH_SIZE = settings.ETL_EXTRACT_BATCH_SIZE

    query = """
        SELECT
            CAST(a.cd_aluno AS VARCHAR(20))                                AS matricula,
            a.nm_aluno                                                     AS nome,
            CONVERT(VARCHAR(10), a.dt_nascimento_aluno, 120)               AS data_nascimento,
            CAST(te.cd_escola AS VARCHAR(20))                              AS cod_escola,
            CAST(te.cd_turma_escola AS VARCHAR(20))                        AS turma,
            CAST(ue.cd_unidade_administrativa_referencia AS VARCHAR(20))   AS cod_dre
        FROM (
            SELECT
                matr.cd_aluno,
                mte.cd_turma_escola,
                ROW_NUMBER() OVER (
                    PARTITION BY matr.cd_aluno
                    ORDER BY matr.dt_status_matricula DESC
                ) AS rn
            FROM v_matricula_cotic matr WITH (NOLOCK)
            INNER JOIN matricula_turma_escola mte WITH (NOLOCK)
                ON mte.cd_matricula = matr.cd_matricula
            WHERE matr.st_matricula IN (1, 6, 10, 13)
              AND mte.cd_situacao_aluno IN (1, 6, 10, 13)
              AND matr.an_letivo = YEAR(GETDATE())
        ) ranked
        INNER JOIN v_aluno_cotic a WITH (NOLOCK)
            ON a.cd_aluno = ranked.cd_aluno
        INNER JOIN turma_escola te WITH (NOLOCK)
            ON te.cd_turma_escola = ranked.cd_turma_escola
        LEFT JOIN v_cadastro_unidade_educacao ue WITH (NOLOCK)
            ON ue.cd_unidade_educacao = te.cd_escola
        WHERE ranked.rn = 1
    """

    conn = pyodbc.connect(_build_se1426_conn_str(), timeout=settings.SE1426_DB_TIMEOUT)
    try:
        _cnt = conn.cursor()
        _cnt.execute(
            """
            SELECT COUNT(DISTINCT matr.cd_aluno)
            FROM v_matricula_cotic matr WITH (NOLOCK)
            INNER JOIN matricula_turma_escola mte WITH (NOLOCK)
                ON mte.cd_matricula = matr.cd_matricula
            WHERE matr.st_matricula IN (1, 6, 10, 13)
              AND mte.cd_situacao_aluno IN (1, 6, 10, 13)
              AND matr.an_letivo = YEAR(GETDATE())
            """
        )
        total_fonte = _cnt.fetchone()[0]
        _cnt.close()
        logger.info("[%s] EOL alunos: %d alunos únicos na fonte (pré-extração)", execution_id, total_fonte)

        cursor = conn.cursor()
        cursor.execute(query)

        total_extracted = 0
        while True:
            rows = cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break

            staging_records = [
                _row_to_aluno_sql(row, execution_id) for row in rows
            ]

            StagingUsuarioAluno.objects.bulk_create(staging_records, batch_size=BATCH_SIZE)
            total_extracted += len(staging_records)
            logger.info(
                "[%s] EOL alunos: %d extraídos (lote de %d)",
                execution_id, total_extracted, len(staging_records),
            )
            if max_records and total_extracted >= max_records:
                logger.info(
                    "[%s] EOL alunos: limite de %d registros atingido — interrompendo extração",
                    execution_id, max_records,
                )
                break
    finally:
        conn.close()

    return total_extracted


@shared_task(bind=True, name="extract.tasks.extract_eol_alunos", max_retries=3)
def extract_eol_alunos(self, execution_id: str):
    """Extrai dados de alunos do EOL_DB para staging."""
    from core.models import ETLExecution, ETLStepLog
    from core.tasks import _check_cancelled

    _check_cancelled(execution_id)

    execution = ETLExecution.objects.get(id=execution_id)
    step, _created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name="extract_eol_alunos",
        defaults={"step_order": 1},
    )
    if not _created:
        from staging.models import StagingUsuarioAluno
        deleted, _ = StagingUsuarioAluno.objects.filter(
            execution_id=execution_id
        ).delete()
        logger.warning(
            "[%s] extract_eol_alunos: restart detectado — %d registros de staging removidos antes de re-extrair",
            execution_id, deleted,
        )
        step.status = ETLStepLog.StepStatus.RUNNING
        step.error_detail = None
        step.finished_at = None
        step.records_out = 0
        step.save(update_fields=["status", "error_detail", "finished_at", "records_out"])

    logger.info("[%s] Step 1c: Extract EOL_DB alunos", execution_id)

    try:
        if not settings.SE1426_DB_SERVER:
            logger.warning("[%s] EOL alunos: SE1426_DB_SERVER não configurado — skipping", execution_id)
            step.status = "skipped"
            step.metadata = {"note": "SE1426_DB_SERVER ausente"}
            step.finished_at = timezone.now()
            step.save()
            return 0

        step.metadata = {
            "mode": "sql_direct",
            "server": settings.SE1426_DB_SERVER,
            "database": settings.SE1426_DB_NAME,
            "tabela": "v_aluno_cotic + v_matricula_cotic",
        }
        step.save(update_fields=["metadata"])

        total_extracted = _extract_eol_alunos_sql(execution_id, max_records=execution.max_records_extract)

        step.records_out = total_extracted
        step.status = "success"
        step.finished_at = timezone.now()
        step.save()

        execution.total_extracted += total_extracted
        execution.save(update_fields=["total_extracted", "updated_at"])

        logger.info(
            "[%s] Step 1c concluido: %d alunos extraídos do EOL_DB",
            execution_id, total_extracted,
        )
        return total_extracted

    except Exception as e:
        logger.exception("[%s] Step 1c FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)[:2000]
        step.finished_at = timezone.now()
        step.save()
        if self.request.retries >= self.max_retries:
            logger.error("[%s] extract_eol_alunos esgotou retries — pipeline continua sem esta fonte", execution_id)
            return 0
        raise self.retry(exc=e, countdown=120)


_CORESSO_CPF_TYPE_ID = "2CEEED03-63EB-E011-9B36-00155D033206"


def _build_coresso_conn_str() -> str:
    server_host = settings.CORESSO_DB_SERVER.split("\\")[0].strip()
    return (
        f"DRIVER={{FreeTDS}};"
        f"SERVER={server_host};"
        f"PORT=1433;"
        f"DATABASE={settings.CORESSO_DB_NAME};"
        f"UID={settings.CORESSO_DB_USER};"
        f"PWD={settings.CORESSO_DB_PASSWORD};"
        f"TDS_Version=7.4;"
        f"ClientCharset=UTF-8;"
    )


def _row_to_coresso_record(row, execution_id: str):
    """Converte um row CoreSSO em StagingUsuarioServidor ou StagingUsuarioTerceiro."""
    rf = str(row.rf).strip() if row.rf else None
    cpf = str(row.cpf).strip() if row.cpf else None
    nome = str(row.nome).strip() if row.nome else None
    email = str(row.email).strip() if row.email else None
    situacao = "ativo" if row.situacao == 1 else "inativo"
    raw = {
        "rf": rf, "cpf": cpf, "nome": nome, "email": email,
        "situacao": row.situacao,
        "data_alteracao": str(row.data_alteracao) if row.data_alteracao else None,
        "fonte": "coresso_sql",
    }
    base_kwargs = dict(
        cpf=cpf, nome=nome, email=email, situacao=situacao,
        source="coresso", execution_id=execution_id, raw_data=raw,
    )
    if rf:
        from staging.models import StagingUsuarioServidor
        return StagingUsuarioServidor(rf=rf, **base_kwargs)
    from staging.models import StagingUsuarioTerceiro
    return StagingUsuarioTerceiro(tipo_acesso="legado-coresso", **base_kwargs)


def _persist_coresso_batch(staging_records: list, batch_size: int) -> None:
    """Persiste um lote CoreSSO separando servidores de terceiros."""
    from staging.models import StagingUsuarioServidor, StagingUsuarioTerceiro

    srvs = [r for r in staging_records if isinstance(r, StagingUsuarioServidor)]
    tercs = [r for r in staging_records if isinstance(r, StagingUsuarioTerceiro)]
    if srvs:
        StagingUsuarioServidor.objects.bulk_create(srvs, batch_size=batch_size)
    if tercs:
        StagingUsuarioTerceiro.objects.bulk_create(tercs, batch_size=batch_size)


def _extract_coresso_sql(execution_id: str, max_records: int | None = None) -> int:
    import pyodbc

    BATCH_SIZE = settings.ETL_EXTRACT_BATCH_SIZE
    exclude_ids: list[int] = settings.CORESSO_EXCLUDE_SISTEMA_IDS

    # Cláusula NOT EXISTS: exclui usuários cujo ÚNICO acesso seja a sistemas na lista.
    # Ou seja, se o usuário tiver acesso a qualquer OUTRO sistema além dos excluídos,
    # ele continua sendo extraído normalmente.
    if exclude_ids:
        ids_placeholder = ",".join(str(i) for i in exclude_ids)
        exclude_filter = f"""
        AND EXISTS (
            SELECT 1
            FROM SYS_UsuarioGrupo ug2 WITH (NOLOCK)
            INNER JOIN SYS_Grupo g2 WITH (NOLOCK)
                ON g2.gru_id = ug2.gru_id AND g2.gru_situacao = 1
            INNER JOIN SYS_Sistema s2 WITH (NOLOCK)
                ON s2.sis_id = g2.sis_id AND s2.sis_situacao = 1
                AND s2.sis_id NOT IN ({ids_placeholder})
            WHERE ug2.usu_id = u.usu_id AND ug2.usg_situacao = 1
        )"""
        logger.info(
            "[%s] CORESSO: excluindo usuários exclusivos dos sistemas %s",
            execution_id, ids_placeholder,
        )
    else:
        exclude_filter = ""

    conn_str = _build_coresso_conn_str()
    # Filtra apenas usuários com pelo menos um perfil ativo em um sistema ativo.
    # Sem este filtro a query retorna TODOS os usuários de toda a prefeitura (~4M),
    # mas o ETL SME só precisa de quem tem acesso a algum sistema SME cadastrado.
    query = f"""
        SELECT DISTINCT
            u.usu_login         AS rf,
            u.usu_email         AS email,
            p.pes_nome          AS nome,
            doc.psd_numero      AS cpf,
            u.usu_situacao      AS situacao,
            u.usu_dataAlteracao AS data_alteracao
        FROM SYS_Usuario u WITH (NOLOCK)
        INNER JOIN SYS_UsuarioGrupo ug WITH (NOLOCK)
            ON u.usu_id = ug.usu_id
            AND ug.usg_situacao = 1
        INNER JOIN SYS_Grupo g WITH (NOLOCK)
            ON g.gru_id = ug.gru_id
            AND g.gru_situacao = 1
        INNER JOIN SYS_Sistema s WITH (NOLOCK)
            ON s.sis_id = g.sis_id
            AND s.sis_situacao = 1
        LEFT JOIN PES_Pessoa p WITH (NOLOCK)
            ON u.pes_id = p.pes_id
        LEFT JOIN PES_PessoaDocumento doc WITH (NOLOCK)
            ON p.pes_id = doc.pes_id
            AND doc.tdo_id = '{_CORESSO_CPF_TYPE_ID}'
        WHERE u.usu_situacao = 1
        {exclude_filter}
    """

    total_extracted = 0

    with pyodbc.connect(conn_str, timeout=settings.CORESSO_DB_TIMEOUT) as conn:
        conn.timeout = settings.CORESSO_DB_TIMEOUT
        _cnt = conn.cursor()
        _cnt.execute(
            f"""
            SELECT COUNT(DISTINCT u.usu_id)
            FROM SYS_Usuario u WITH (NOLOCK)
            INNER JOIN SYS_UsuarioGrupo ug WITH (NOLOCK)
                ON u.usu_id = ug.usu_id AND ug.usg_situacao = 1
            INNER JOIN SYS_Grupo g WITH (NOLOCK)
                ON g.gru_id = ug.gru_id AND g.gru_situacao = 1
            INNER JOIN SYS_Sistema s WITH (NOLOCK)
                ON s.sis_id = g.sis_id AND s.sis_situacao = 1
            WHERE u.usu_situacao = 1
            {exclude_filter}
            """
        )
        total_fonte = _cnt.fetchone()[0]
        _cnt.close()
        logger.info("[%s] CORESSO: %d usuários ativos com perfil em sistema ativo (pré-extração)", execution_id, total_fonte)

        cursor = conn.cursor()
        cursor.execute(query)

        while True:
            rows = cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break

            staging_records = [_row_to_coresso_record(row, execution_id) for row in rows]
            _persist_coresso_batch(staging_records, BATCH_SIZE)
            total_extracted += len(staging_records)
            logger.info(
                "[%s] CORESSO SQL: %d registros extraídos (lote de %d)",
                execution_id,
                total_extracted,
                len(staging_records),
            )
            if max_records and total_extracted >= max_records:
                logger.info(
                    "[%s] CORESSO: limite de %d registros atingido — interrompendo extração",
                    execution_id, max_records,
                )
                break

    return total_extracted


def _extract_coresso_api(execution_id: str) -> int:
    headers = {"Authorization": f"Token {settings.CORESSO_API_TOKEN}"}

    with httpx.Client(timeout=120) as client:
        response = client.get(
            f"{settings.CORESSO_API_URL}/export/usuarios/",
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    results = data if isinstance(data, list) else data.get("results", [])

    from staging.models import StagingUsuarioServidor, StagingUsuarioTerceiro
    srvs, tercs = [], []
    for item in results:
        rf = item.get("rf")
        base = dict(
            cpf=item.get("cpf"),
            nome=item.get("nome"),
            email=item.get("email"),
            situacao=item.get("situacao", "migrado"),
            source="coresso",
            execution_id=execution_id,
            raw_data={**item, "fonte": "coresso_api"},
        )
        if rf:
            srvs.append(StagingUsuarioServidor(rf=rf, cargo=item.get("cargo"), **base))
        else:
            tercs.append(StagingUsuarioTerceiro(tipo_acesso="legado-coresso", **base))

    if srvs:
        StagingUsuarioServidor.objects.bulk_create(srvs, batch_size=1000)
    if tercs:
        StagingUsuarioTerceiro.objects.bulk_create(tercs, batch_size=1000)
    return len(srvs) + len(tercs)


@shared_task(bind=True, name="extract.tasks.extract_coresso", max_retries=2)
def extract_coresso(self, execution_id: str):
    """Extrai dados de usuarios terceiros do CoreSSO para staging."""
    from core.models import ETLExecution, ETLStepLog
    from core.tasks import _check_cancelled

    _check_cancelled(execution_id)

    execution = ETLExecution.objects.get(id=execution_id)
    step, _created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name=ETLStepLog.StepName.EXTRACT_CORESSO,
        defaults={"step_order": 2},
    )
    if not _created:
        from staging.models import StagingUsuarioServidor, StagingUsuarioTerceiro
        del_srv, _ = StagingUsuarioServidor.objects.filter(
            execution_id=execution_id, source=StagingUsuarioServidor.Source.CORESSO
        ).delete()
        del_terc, _ = StagingUsuarioTerceiro.objects.filter(
            execution_id=execution_id, source=StagingUsuarioTerceiro.Source.CORESSO
        ).delete()
        logger.warning(
            "[%s] extract_coresso: restart detectado — %d servidores e %d terceiros removidos antes de re-extrair",
            execution_id, del_srv, del_terc,
        )
        step.status = ETLStepLog.StepStatus.RUNNING
        step.error_detail = None
        step.finished_at = None
        step.records_out = 0
        step.save(update_fields=["status", "error_detail", "finished_at", "records_out"])

    logger.info("[%s] Step 2: Extract CORESSO", execution_id)

    try:
        if settings.CORESSO_DB_SERVER:
            logger.info("[%s] CORESSO: modo SQL Server direto (FreeTDS)", execution_id)
            step.metadata = {
                "mode": "sql_direct",
                "server": settings.CORESSO_DB_SERVER.split("\\")[0],
                "database": settings.CORESSO_DB_NAME,
            }
            step.save(update_fields=["metadata"])
            total_extracted = _extract_coresso_sql(execution_id, max_records=execution.max_records_extract)

        elif settings.CORESSO_API_URL:
            logger.info("[%s] CORESSO: modo API REST (fallback)", execution_id)
            step.metadata = {"mode": "api_rest", "url": settings.CORESSO_API_URL}
            step.save(update_fields=["metadata"])
            total_extracted = _extract_coresso_api(execution_id)

        else:
            logger.info(
                "[%s] CORESSO: nenhuma fonte configurada — skipping", execution_id
            )
            step.status = "skipped"
            step.metadata = {
                "note": "Nenhuma fonte CORESSO configurada "
                "(CORESSO_DB_SERVER e CORESSO_API_URL ausentes)"
            }
            step.finished_at = timezone.now()
            step.save()
            return 0

        step.records_out = total_extracted
        step.status = "success"
        step.finished_at = timezone.now()
        step.save()

        execution.total_extracted += total_extracted
        execution.save(update_fields=["total_extracted", "updated_at"])

        logger.info(
            "[%s] Step 2 concluido: %d records from CORESSO", execution_id, total_extracted
        )
        return total_extracted

    except Exception as e:
        logger.exception("[%s] Step 2 FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)[:2000]
        step.finished_at = timezone.now()
        step.save()
        if self.request.retries >= self.max_retries:
            logger.error("[%s] extract_coresso esgotou retries — pipeline continua sem esta fonte", execution_id)
            return 0
        raise self.retry(exc=e, countdown=60)


def _slugify_sigla(nome: str) -> str:
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or f"sistema-{abs(hash(nome)) % 100000}"


@shared_task(
    bind=True, name="extract.tasks.extract_coresso_sistemas", max_retries=2
)
def extract_coresso_sistemas(self, execution_id: str | None = None):
    """Extrai sistemas do CoreSSO (SYS_Sistema) para a tabela staging_sistema."""
    import pyodbc

    from staging.models import StagingSistema

    if not settings.CORESSO_DB_SERVER:
        logger.warning("CORESSO_DB_SERVER ausente — pulando extração de sistemas")
        return 0

    conn_str = _build_coresso_conn_str()
    query = """
        SELECT
            sis_id,
            sis_nome,
            sis_descricao,
            sis_caminho           AS url_callback,
            sis_caminhoLogout     AS url_logout,
            sis_tipoAutenticacao,
            sis_situacao
        FROM SYS_Sistema
        WHERE sis_situacao = 1
        ORDER BY sis_id
    """

    total = 0
    with pyodbc.connect(conn_str, timeout=settings.CORESSO_DB_TIMEOUT) as conn:
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()

    for row in rows:
        sis_id = int(row.sis_id)
        nome = (row.sis_nome or "").strip()
        sigla = _slugify_sigla(nome)
        defaults = {
            "nome": nome,
            "descricao": row.sis_descricao,
            "sigla": sigla,
            "url_callback": row.url_callback,
            "url_logout": row.url_logout,
            "tipo_autenticacao": row.sis_tipoAutenticacao,
            "situacao": row.sis_situacao,
            "execution_id": execution_id,
            "status": StagingSistema.Status.READY,
            "raw_data": {
                "sis_id": sis_id,
                "sis_nome": nome,
                "sis_situacao": row.sis_situacao,
            },
        }
        StagingSistema.objects.update_or_create(
            coresso_sis_id=sis_id, defaults=defaults
        )
        total += 1

    logger.info("Sistemas CoreSSO extraídos: %d", total)
    return total


@shared_task(
    bind=True, name="extract.tasks.extract_coresso_perfis", max_retries=2
)
def extract_coresso_perfis(self, execution_id: str | None = None):
    """Extract CoreSSO profiles/groups (GRU_Grupo) into staging_perfil_coresso."""
    import pyodbc

    from staging.models import StagingPerfilCoreSSO, StagingSistema

    if not settings.CORESSO_DB_SERVER:
        logger.warning("CORESSO_DB_SERVER ausente — pulando extração de perfis")
        return 0

    conn_str = _build_coresso_conn_str()
    query = """
        SELECT
            CAST(g.gru_id AS VARCHAR(64)) AS gru_id,
            g.gru_nome,
            g.sis_id,
            g.vis_id,
            g.gru_situacao
        FROM SYS_Grupo g
        INNER JOIN SYS_Sistema s ON s.sis_id = g.sis_id
        WHERE g.gru_situacao = 1 AND s.sis_situacao = 1
        ORDER BY g.sis_id, g.gru_nome
    """

    total = 0
    sistemas_cache = {
        s.coresso_sis_id: s for s in StagingSistema.objects.all()
    }

    with pyodbc.connect(conn_str, timeout=settings.CORESSO_DB_TIMEOUT) as conn:
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()

    for row in rows:
        gru_id = (row.gru_id or "").upper()
        nome = (row.gru_nome or "").strip()
        sis_id = int(row.sis_id)
        sistema = sistemas_cache.get(sis_id)
        kc_role_name = _slugify_sigla(nome) or f"role-{gru_id[:8].lower()}"
        defaults = {
            "nome": nome,
            "coresso_sis_id": sis_id,
            "coresso_vis_id": int(row.vis_id) if row.vis_id is not None else None,
            "situacao": int(row.gru_situacao or 1),
            "sistema": sistema,
            "kc_role_name": kc_role_name,
            "execution_id": execution_id,
            "status": StagingPerfilCoreSSO.Status.READY,
        }
        StagingPerfilCoreSSO.objects.update_or_create(
            coresso_gru_id=gru_id, defaults=defaults
        )
        total += 1

    logger.info("Perfis CoreSSO extraídos: %d", total)
    return total


def fetch_coresso_groups_for_login(login: str) -> list[dict]:
    """Consulta o CoreSSO e retorna a lista de grupos atribuidos a um determinado login de usuario."""
    import pyodbc

    if not settings.CORESSO_DB_SERVER or not login:
        return []

    conn_str = _build_coresso_conn_str()
    query = """
        SELECT
            CAST(g.gru_id AS VARCHAR(64)) AS gru_id,
            g.gru_nome,
            g.sis_id,
            s.sis_nome
        FROM SYS_Usuario u
        INNER JOIN SYS_UsuarioGrupo ug
            ON u.usu_id = ug.usu_id AND ug.usg_situacao = 1
        INNER JOIN SYS_Grupo g
            ON g.gru_id = ug.gru_id AND g.gru_situacao = 1
        INNER JOIN SYS_Sistema s
            ON s.sis_id = g.sis_id AND s.sis_situacao = 1
        WHERE u.usu_situacao = 1 AND u.usu_login = ?
    """
    out: list[dict] = []
    with pyodbc.connect(conn_str, timeout=settings.CORESSO_DB_TIMEOUT) as conn:
        cur = conn.cursor()
        cur.execute(query, login)
        for row in cur.fetchall():
            out.append({
                "gru_id": (row.gru_id or "").upper(),
                "gru_nome": (row.gru_nome or "").strip(),
                "sis_id": int(row.sis_id),
                "sis_nome": (row.sis_nome or "").strip(),
            })
    return out
