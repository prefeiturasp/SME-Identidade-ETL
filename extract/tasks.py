"""Tasks e utilitários de extração de dados para o pipeline ETL.

Este módulo concentra as rotinas responsáveis pela extração de dados
provenientes dos sistemas legados da SME, incluindo:

- SE1426 (servidores);
- EOL (servidores, lotações e alunos);
- CoreSSO (usuários, sistemas e perfis).

As extrações podem ocorrer via acesso direto ao banco SQL Server
(utilizando FreeTDS/pyodbc) ou via APIs REST, dependendo da
configuração disponível no ambiente.

Os dados extraídos são persistidos nas tabelas de staging para
posterior transformação, deduplicação e carga nos sistemas de destino,
como Keycloak e Token-MS.
"""

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
    """Monta a string de conexão utilizada para acesso ao banco SE1426.

    Utiliza as configurações definidas no settings da aplicação para
    construir uma conexão compatível com SQL Server via FreeTDS.

    Returns:
        String de conexão pronta para uso pelo pyodbc.
    """
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

def _extract_se1426_sql(execution_id: str) -> int:
    """Extrai servidores do banco SE1426 e persiste os registros no staging.

    Realiza leitura em lotes para reduzir consumo de memória e cria
    registros de staging utilizando inserções em massa.

    Args:
        execution_id: Identificador da execução ETL associada.

    Returns:
        Quantidade total de registros extraídos.
    """
    import pyodbc

    from staging.models import StagingUsuarioServidor

    batch_size = 500

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
        cursor = conn.cursor()
        cursor.execute(query)

        total_extracted = 0
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            cols = [d[0] for d in cursor.description]
            staging_records = []
            for row in rows:
                item = dict(zip(cols, row, strict=True))
                staging_records.append(
                    StagingUsuarioServidor(
                        rf=item.get("rf"),
                        nome=item.get("nome"),
                        cpf=item.get("cpf"),
                        email=item.get("email"),
                        situacao=(
                            item.get("situacao", "").lower()
                            if item.get("situacao")
                            else None
                        ),
                        cargo=item.get("cargo") or None,
                        funcao=item.get("funcao") or None,
                        lotacao=item.get("cod_unidade") or None,
                        dre=item.get("cod_dre") or None,
                        source=StagingUsuarioServidor.Source.SE1426,
                        execution_id=execution_id,
                        raw_data={
                            k: str(v) if v is not None else None for k,
                            v in item.items()
                        },
                    )
                )

            StagingUsuarioServidor.objects.bulk_create(staging_records, batch_size=500)
            total_extracted += len(staging_records)

    finally:
        conn.close()

    return total_extracted

def _fetch_eol_db_rows():
    """Executa a query no banco EOL e retorna os registros em streaming.

    Utiliza fetchmany para reduzir consumo de memoria e processar
    os dados em blocos.

    Yields:
        Tuplas representando linhas retornadas pela query SQL.
    """
    import pyodbc

    batch_size = 500

    query = """
        SELECT
            sc.cd_registro_funcional AS rf,
            s.cd_cpf_pessoa AS cpf,
            s.nm_pessoa AS nome,
            s.situacao AS situacao,
            RTRIM(LTRIM(ISNULL(c.dc_cargo, ''))) AS desc_cargo,
            CAST(ISNULL(cba.cd_cargo, '') AS VARCHAR(20)) AS cod_cargo,
            CAST(l.cd_unidade_educacao AS VARCHAR(20)) AS cod_unidade,
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

    conn = pyodbc.connect(
        _build_se1426_conn_str(),
        timeout=settings.SE1426_DB_TIMEOUT,
    )

    try:
        cursor = conn.cursor()
        cursor.execute(query)

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            yield from rows

    finally:
        conn.close()

def _aggregate_eol_servidores(rows):
    """Agrega os registros de servidores por RF.

    Consolida dados duplicados e agrupa:
    - unidades de lotacao
    - DREs associadas
    - dados cadastrais basicos

    Args:
        rows: Iteravel de linhas retornadas do banco EOL.

    Returns:
        Dicionário indexado pelo RF contendo os dados consolidados
        de cada servidor.
    """
    servidores = {}

    for row in rows:
        rf, cpf, nome, situacao, desc_cargo, cod_cargo, cod_unidade, cod_dre = row

        srv = servidores.setdefault(
            rf,
            {
                "rf": rf,
                "cpf": cpf,
                "nome": nome,
                "situacao": situacao,
                "desc_cargo": desc_cargo or None,
                "cod_cargo": cod_cargo or None,
                "unidades": [],
                "dres": [],
            },
        )

        if cod_unidade:
            srv["unidades"].append(cod_unidade)

        if cod_dre and cod_dre not in srv["dres"]:
            srv["dres"].append(cod_dre)

    return servidores

def _persist_eol_servidores(servidores: dict, execution_id: str) -> None:
    """Persiste os servidores agregados no staging em batch.

    Realiza insercao em lote para otimizar performance e reduzir
    chamadas ao banco de dados.

    Args:
        servidores: Dicionario com servidores agregados por RF.
        execution_id: Identificador da execucao ETL.

    Returns:
        None
    """
    from staging.models import StagingUsuarioServidor

    batch = []

    for srv in servidores.values():
        batch.append(
            StagingUsuarioServidor(
                rf=srv["rf"],
                cpf=srv["cpf"],
                nome=srv["nome"],
                situacao=srv["situacao"].lower() if srv["situacao"] else None,
                cargo=srv["desc_cargo"],
                lotacao=srv["unidades"][0] if srv["unidades"] else None,
                dre=srv["dres"][0] if srv["dres"] else None,
                source=StagingUsuarioServidor.Source.EOL_DB,
                execution_id=execution_id,
                raw_data={
                    "rf": srv["rf"],
                    "cpf": srv["cpf"],
                    "cod_cargo": srv["cod_cargo"],
                    "desc_cargo": srv["desc_cargo"],
                    "unidades": srv["unidades"],
                    "dres": srv["dres"],
                    "fonte": "eol_db_sql",
                },
            )
        )

        if len(batch) >= 500:
            StagingUsuarioServidor.objects.bulk_create(batch, batch_size=500)
            batch = []

    if batch:
        StagingUsuarioServidor.objects.bulk_create(batch, batch_size=500)

def _extract_eol_db_sql(execution_id: str) -> int:
    """Orquestra a extracao de servidores do banco EOL e persiste no staging.

    Executa o fluxo completo:
    - consulta os dados no banco EOL
    - agrega os registros por servidor
    - persiste os dados no staging em batch

    Args:
        execution_id: Identificador da execucao ETL.

    Returns:
        Quantidade de servidores unicos processados.
    """
    rows = _fetch_eol_db_rows()
    servidores = _aggregate_eol_servidores(rows)
    _persist_eol_servidores(servidores, execution_id)

    return len(servidores)

@shared_task(bind=True, name="extract.tasks.extract_se1426", max_retries=3)
def extract_se1426(self, execution_id: str):
    """Task Celery responsável pela extração de servidores do SE1426.

    A extração pode ocorrer por acesso direto ao banco SQL Server ou por
    API REST, conforme a configuração disponível no ambiente.

    Args:
        self: Instância da task Celery.
        execution_id: Identificador da execução ETL.

    Returns:
        Quantidade de registros extraídos.
    """
    from core.models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)
    step, _created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name=ETLStepLog.StepName.EXTRACT_SE1426,
        defaults={"step_order": 1},
    )
    if not _created:
        step.status = ETLStepLog.StepStatus.RUNNING
        step.error_detail = None
        step.finished_at = None
        step.records_out = 0
        step.save(update_fields=[
            "status",
            "error_detail",
            "finished_at",
            "records_out"
        ])

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
            total_extracted = _extract_se1426_sql(execution_id)

        elif settings.SE1426_API_TOKEN:
            logger.info("[%s] SE1426: modo API REST", execution_id)
            step.metadata = {"mode": "api_rest", "url": settings.SE1426_API_URL}
            step.save(update_fields=["metadata"])
            total_extracted = _extract_se1426_api(execution_id)

        else:
            logger.warning(
                "[%s] SE1426: nenhuma fonte configurada — skipping",
                execution_id
            )
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

        logger.info(
            "[%s] Step 1 concluido: %d records from SE1426",
            execution_id,
            total_extracted
        )
        return total_extracted

    except Exception as e:
        logger.exception("[%s] Step 1 FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)[:2000]
        step.finished_at = timezone.now()
        step.save()
        if self.request.retries >= self.max_retries:
            # Não aborta a execução — chord continua com os demais sources
            logger.error(
                "[%s] extract_se1426 esgotou retry — pipeline continua sem esta fonte",
                execution_id
            )
            return 0
        raise self.retry(exc=e, countdown=120) from e

def _extract_se1426_api(execution_id: str) -> int:
    """Extrai servidores da API do SE1426 e persiste os registros no staging.

    A consulta é realizada de forma paginada, processando os resultados
    em lotes até que todas as páginas sejam consumidas.

    Args:
        execution_id: Identificador da execução ETL.

    Returns:
        Quantidade total de registros extraídos.
    """
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
            StagingUsuarioServidor.objects.bulk_create(
                staging_records,
                ignore_conflicts=False
            )
            total_extracted += len(staging_records)
            has_more = bool(data.get("next")) or len(results) == 500
            page += 1

    return total_extracted

@shared_task(bind=True, name="extract.tasks.extract_eol_db", max_retries=3)
def extract_eol_db(self, execution_id: str):
    """Task Celery responsável pela extração de servidores e lotações do EOL.

    Realiza a leitura dos dados diretamente do banco SQL Server e persiste
    os registros consolidados na camada de staging.

    Args:
        self: Instância da task Celery.
        execution_id: Identificador da execução ETL.

    Returns:
        Quantidade de servidores processados.
    """
    from core.models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)
    step, _created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name=ETLStepLog.StepName.EXTRACT_EOL_DB,
        defaults={"step_order": 1},
    )
    if not _created:
        step.status = ETLStepLog.StepStatus.RUNNING
        step.error_detail = None
        step.finished_at = None
        step.records_out = 0
        step.save(update_fields=[
            "status",
            "error_detail",
            "finished_at",
            "records_out"
        ])

    logger.info("[%s] Step 1b: Extract EOL_DB (lotações/unidades)", execution_id)

    try:
        if not settings.SE1426_DB_SERVER:
            logger.warning(
                "[%s] EOL_DB: SE1426_DB_SERVER não configurado — skipping",
                execution_id
            )
            step.status = "skipped"
            step.metadata = {
                "note": "SE1426_DB_SERVER ausente — EOL_DB usa mesmo servidor SE1426"
            }
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

        total_extracted = _extract_eol_db_sql(execution_id)

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
            logger.error(
                "[%s] extract_eol_db esgotou retry — pipeline continua sem esta fonte",
                execution_id
            )
            return 0
        raise self.retry(exc=e, countdown=120) from e

def eol_alunos_query() -> str:
    """Retorna a query SQL utilizada para extrair alunos do EOL.

    Returns:
        String contendo a query SQL completa utilizada na extração.
    """
    return """
        SELECT
            CAST(a.cd_aluno AS VARCHAR(20)) AS matricula,
            a.nm_aluno AS nome,
            CONVERT(VARCHAR(10), a.dt_nascimento_aluno, 120) AS data_nascimento,
            CAST(te.cd_escola AS VARCHAR(20)) AS cod_escola,
            CAST(te.cd_turma_escola AS VARCHAR(20)) AS turma,
            CAST(ue.cd_unidade_administrativa_referencia AS VARCHAR(20)) AS cod_dre
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

def _process_eol_alunos(cursor, execution_id: str) -> int:
    """Processa cursor do EOL e persiste alunos no staging.

    Args:
        cursor: Cursor ativo do banco EOL.
        execution_id: ID da execução ETL.

    Returns:
        Total de registros processados.
    """
    from staging.models import StagingUsuarioAluno

    batch_size = 500
    total = 0

    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break

        records = [_build_aluno_record(row, execution_id) for row in rows]

        StagingUsuarioAluno.objects.bulk_create(records, batch_size=batch_size)

        total += len(records)

        logger.info(
            "[%s] EOL alunos: %d extraídos (lote de %d)",
            execution_id,
            total,
            len(records),
        )

    return total

def _build_aluno_record(row, execution_id: str):
    """Converte uma linha do EOL em um objeto StagingUsuarioAluno.

    Args:
        row: Linha retornada pelo cursor do banco EOL contendo os dados do aluno.
        execution_id: Identificador da execução ETL.

    Returns:
        Instância de StagingUsuarioAluno preenchida com os dados normalizados.
    """
    from staging.models import StagingUsuarioAluno

    matricula = _safe_strip(row.matricula)
    nome = _safe_strip(row.nome)
    cod_escola = _safe_strip(row.cod_escola)
    turma = _safe_strip(row.turma)
    cod_dre = _safe_strip(row.cod_dre)

    data_nascimento = _parse_date(row.data_nascimento)

    return StagingUsuarioAluno(
        matricula=matricula,
        nome=nome,
        data_nascimento=data_nascimento,
        cod_escola=cod_escola,
        turma=turma,
        dre=cod_dre,
        ue=cod_escola,
        situacao="ativo",
        source=StagingUsuarioAluno.Source.EOL_DB,
        execution_id=execution_id,
        raw_data={
            "matricula": matricula,
            "cod_escola": cod_escola,
            "turma": turma,
            "cod_dre": cod_dre,
            "data_nascimento": row.data_nascimento,
            "fonte": "eol_db_alunos",
        },
    )

def _safe_strip(value):
    """Normaliza valores de string removendo espaços em branco.

    Converte valores válidos para string e remove espaços no início e fim.
    Retorna None caso o valor seja nulo ou vazio.

    Args:
        value: Valor de entrada a ser normalizado.

    Returns:
        String limpa ou None se o valor não for válido.
    """
    return str(value).strip() if value else None

def _parse_date(value):
    """Converte uma string de data no formato YYYY-MM-DD para objeto date.

    Args:
        value: String representando uma data no formato ISO (YYYY-MM-DD).

    Returns:
        Objeto date correspondente ou None caso a conversão falhe.
    """
    if not value:
        return None

    try:
        year, month, day = value.split("-")
        return date(int(year), int(month), int(day))
    except Exception:
        return None

def _extract_eol_alunos_sql(execution_id: str) -> int:
    """Extrai alunos do EOL e persiste no staging em lotes.

    Args:
        execution_id: Identificador da execução ETL.

    Returns:
        Total de alunos extraídos.
    """
    import pyodbc

    conn = pyodbc.connect(_build_se1426_conn_str(), timeout=settings.SE1426_DB_TIMEOUT)

    try:
        cursor = conn.cursor()
        cursor.execute(eol_alunos_query())

        return _process_eol_alunos(cursor, execution_id)

    finally:
        conn.close()

@shared_task(bind=True, name="extract.tasks.extract_eol_alunos", max_retries=3)
def extract_eol_alunos(self, execution_id: str):
    """Task Celery responsável pela extração de alunos ativos do EOL.

    Os dados são obtidos diretamente do banco SQL Server e armazenados
    nas tabelas de staging para processamento posterior.

    Args:
        self: Instância da task Celery.
        execution_id: Identificador da execução ETL.

    Returns:
        Quantidade de alunos extraídos.
    """
    from core.models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)
    step, _created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name="extract_eol_alunos",
        defaults={"step_order": 1},
    )
    if not _created:
        step.status = ETLStepLog.StepStatus.RUNNING
        step.error_detail = None
        step.finished_at = None
        step.records_out = 0
        step.save(update_fields=[
            "status",
            "error_detail",
            "finished_at",
            "records_out"
        ])

    logger.info("[%s] Step 1c: Extract EOL_DB alunos", execution_id)

    try:
        if not settings.SE1426_DB_SERVER:
            logger.warning(
                "[%s] EOL alunos: SE1426_DB_SERVER não configurado — skipping",
                execution_id
            )
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

        total_extracted = _extract_eol_alunos_sql(execution_id)

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
            logger.error(
                "[%s] extract_eol_alunos esgotou retry — pipeline continua",
                execution_id
            )
            return 0
        raise self.retry(exc=e, countdown=120) from e

_CORESSO_CPF_TYPE_ID = "2CEEED03-63EB-E011-9B36-00155D033206"

def _build_coresso_conn_str() -> str:
    """Monta a string de conexão utilizada para acesso ao banco CoreSSO.

    Returns:
        String de conexão compatível com SQL Server via FreeTDS.
    """
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

def _coresso_query() -> str:
    """Retorna a query SQL utilizada para extracao de dados do CoreSSO.

    Returns:
        String contendo a query SQL completa.
    """
    return f"""
        SELECT
            u.usu_login        AS rf,
            u.usu_email        AS email,
            p.pes_nome         AS nome,
            doc.psd_numero     AS cpf,
            u.usu_situacao     AS situacao,
            u.usu_dataAlteracao AS data_alteracao
        FROM SYS_Usuario u
        LEFT JOIN PES_Pessoa p
            ON u.pes_id = p.pes_id
        LEFT JOIN PES_PessoaDocumento doc
            ON p.pes_id = doc.pes_id
            AND doc.tdo_id = '{_CORESSO_CPF_TYPE_ID}'
        WHERE u.usu_situacao = 1
        ORDER BY u.usu_id
    """

def _build_coresso_record(row, execution_id: str):
    """Converte uma linha retornada do CoreSSO em um objeto de staging.

    Realiza o mapeamento dos campos do banco para os modelos de staging,
    normalizando dados e definindo o tipo de usuario como servidor ou terceiro.

    Args:
        row: Linha retornada pela query SQL do CoreSSO.
        execution_id: Identificador da execução ETL atual.

    Returns:
        Instância de StagingUsuarioServidor ou StagingUsuarioTerceiro.
    """
    rf = _safe_strip(row.rf)
    cpf = _safe_strip(row.cpf)
    nome = _safe_strip(row.nome)
    email = _safe_strip(row.email)

    situacao = "ativo" if row.situacao == 1 else "inativo"

    raw = {
        "rf": rf,
        "cpf": cpf,
        "nome": nome,
        "email": email,
        "situacao": row.situacao,
        "data_alteracao": str(row.data_alteracao) if row.data_alteracao else None,
        "fonte": "coresso_sql",
    }

    base_kwargs = {
        "cpf": cpf,
        "nome": nome,
        "email": email,
        "situacao": situacao,
        "source": "coresso",
        "execution_id": execution_id,
        "raw_data": raw,
    }

    if rf:
        from staging.models import StagingUsuarioServidor
        return StagingUsuarioServidor(rf=rf, **base_kwargs)

    from staging.models import StagingUsuarioTerceiro
    return StagingUsuarioTerceiro(
        tipo_acesso="legado-coresso",
        **base_kwargs
    )

def _persist_coresso_batch(records: list, batch_size: int) -> int:
    """Persiste um lote de registros do CoreSSO no banco de staging.

    Separa os registros por tipo (servidor e terceiro) e realiza inserção
    em massa utilizando bulk_create para otimizar performance.

    Args:
        records: Lista de objetos de staging a serem persistidos.
        batch_size: Tamanho do lote utilizado no bulk_create.

    Returns:
        Quantidade total de registros persistidos.
    """
    if not records:
        return 0

    from staging.models import StagingUsuarioServidor, StagingUsuarioTerceiro

    servidores = [r for r in records if isinstance(r, StagingUsuarioServidor)]
    terceiros = [r for r in records if isinstance(r, StagingUsuarioTerceiro)]

    if servidores:
        StagingUsuarioServidor.objects.bulk_create(servidores, batch_size=batch_size)

    if terceiros:
        StagingUsuarioTerceiro.objects.bulk_create(terceiros, batch_size=batch_size)

    return len(records)

def _extract_coresso_sql(execution_id: str) -> int:
    """Extrai usuarios do CoreSSO e persiste no staging.

    Args:
        execution_id: Identificador da execução ETL.

    Returns:
        Total de registros processados.
    """
    import pyodbc

    batch_size = 500
    conn_str = _build_coresso_conn_str()
    total = 0

    with pyodbc.connect(conn_str, timeout=settings.CORESSO_DB_TIMEOUT) as conn:
        conn.timeout = settings.CORESSO_DB_TIMEOUT
        cursor = conn.cursor()
        cursor.execute(_coresso_query())

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            records = [
                _build_coresso_record(row, execution_id)
                for row in rows
            ]

            total += _persist_coresso_batch(records, batch_size)

            logger.info(
                "[%s] CORESSO SQL: %d registros extraídos (lote de %d)",
                execution_id,
                total,
                len(records),
            )

    return total

def _extract_coresso_api(execution_id: str) -> int:
    """Extrai usuários do CoreSSO por meio da API REST.

    Os registros retornados são convertidos para modelos de staging
    e persistidos em lote.

    Args:
        execution_id: Identificador da execução ETL.

    Returns:
        Quantidade total de registros processados.
    """
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
        base = {
            "cpf": item.get("cpf"),
            "nome": item.get("nome"),
            "email": item.get("email"),
            "situacao": item.get("situacao", "migrado"),
            "source": "coresso",
            "execution_id": execution_id,
            "raw_data": {**item, "fonte": "coresso_api"},
        }
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
    """Task Celery responsável pela extração de usuários do CoreSSO.

    A extração pode ocorrer diretamente do banco SQL Server ou por meio
    da API REST configurada como fallback.

    Args:
        self: Instância da task Celery.
        execution_id: Identificador da execução ETL.

    Returns:
        Quantidade de registros extraídos.
    """
    from core.models import ETLExecution, ETLStepLog

    execution = ETLExecution.objects.get(id=execution_id)
    step, _created = ETLStepLog.objects.get_or_create(
        execution=execution,
        step_name=ETLStepLog.StepName.EXTRACT_CORESSO,
        defaults={"step_order": 2},
    )
    if not _created:
        step.status = ETLStepLog.StepStatus.RUNNING
        step.error_detail = None
        step.finished_at = None
        step.records_out = 0
        step.save(update_fields=[
            "status",
            "error_detail",
            "finished_at",
            "records_out"
        ])

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
            total_extracted = _extract_coresso_sql(execution_id)

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
            "[%s] Step 2 concluido: %d records from CORESSO",
            execution_id,
            total_extracted
        )
        return total_extracted

    except Exception as e:
        logger.exception("[%s] Step 2 FAILED: %s", execution_id, e)
        step.status = "failed"
        step.error_detail = str(e)[:2000]
        step.finished_at = timezone.now()
        step.save()
        if self.request.retries >= self.max_retries:
            logger.error(
                "[%s] extract_coresso esgotou retry — pipeline continua sem esta fonte",
                execution_id
            )
            return 0
        raise self.retry(exc=e, countdown=60) from e

def _slugify_sigla(nome: str) -> str:
    """Gera uma sigla normalizada para uso em identificadores técnicos.

    Remove acentuação, caracteres especiais e converte o valor para
    um formato compatível com slugs.

    Args:
        nome: Nome original do sistema ou perfil.

    Returns:
        String normalizada para utilização como slug.
    """
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or f"sistema-{abs(hash(nome)) % 100000}"

@shared_task(
    bind=True, name="extract.tasks.extract_coresso_sistemas", max_retries=2
)
def extract_coresso_sistemas(self, execution_id: str | None = None):
    """Extrai sistemas ativos do CoreSSO para a tabela de staging.

    Consulta a tabela SYS_Sistema e mantém os registros sincronizados
    na camada de staging por meio de operações update_or_create.

    Args:
        self: Instância da task Celery.
        execution_id: Identificador opcional da execução ETL.

    Returns:
        Quantidade total de sistemas processados.
    """
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
    """Extrai perfis e grupos ativos do CoreSSO para a tabela de staging.

    Consulta os grupos vinculados aos sistemas ativos e mantém os
    registros sincronizados por meio de operações update_or_create.

    Args:
        self: Instância da task Celery.
        execution_id: Identificador opcional da execução ETL.

    Returns:
        Quantidade total de perfis processados.
    """
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
    """Consulta os grupos atribuídos a um usuário no CoreSSO.

    Recupera todos os grupos ativos associados ao login informado,
    incluindo informações do sistema ao qual cada grupo pertence.

    Args:
        login: Login do usuário no CoreSSO.

    Returns:
        Lista de dicionários contendo identificadores e nomes dos
        grupos e sistemas associados ao usuário.
    """
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
