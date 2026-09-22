# SME-Identidade-ETL

Microsserviço ETL responsável por ingerir, reconciliar e provisionar
identidades da SME-SP (servidores, alunos e terceiros) a partir das bases
legadas (SE1426, CoreSSO, EOL_DB) no Keycloak e no token-ms.

---

## Arquitetura geral

```{graphviz}
digraph G {
    rankdir=LR;
    node [shape=box, style="rounded"];

    SE1426  [label="SE1426\n(SQL Server)"];
    CORESSO [label="CoreSSO\n(SQL Server)"];
    EOL     [label="EOL_DB\n(SQL Server)"];

    ETL     [label="SME-Identidade-ETL\n(Django + Celery)"];
    STG     [label="Staging DB\n(PostgreSQL)"];
    KC      [label="Keycloak"];
    TMS     [label="token-ms"];

    SE1426  -> ETL;
    CORESSO -> ETL;
    EOL     -> ETL;
    ETL     -> STG [label="Extract/Transform"];
    STG     -> ETL [label="Load"];
    ETL     -> KC  [label="upsert"];
    ETL     -> TMS [label="atributos"];
}
```

---

## O que o projeto implementa

- extração paralela de 3 fontes via Celery chord
- staging intermediário em PostgreSQL (servidor, aluno, terceiro)
- resolução de identidade com deduplicação e crossref por CPF/RF
- provisionamento idempotente no Keycloak via Admin API
- carga de atributos complementares no token-ms
- controle de execução auditável (`ExecucaoETL`, `LogEtapaETL`)
- checkpoint por etapa para retomada após falha
- watermark incremental por fonte
- API REST de controle e consulta

---

## Navegação

```{toctree}
:maxdepth: 2

arquitetura/visao_geral
pipeline/index
controle/index
operacao/index
api
```
