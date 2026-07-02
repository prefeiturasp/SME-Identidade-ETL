# Pipeline ETL de Identidades

O pipeline é disparado via API REST (`POST /api/v1/etl/execucoes/`) ou pelo
comando `executar_etl`. A orquestração é feita pela task
`task_identidade_executar_pipeline` em `apps/controle_etl/tasks.py`.

---

## Etapas

```{toctree}
:maxdepth: 2

extracao
staging
resolucao
keycloak
token_ms
```

---

## Fluxo de execução

```{graphviz}
digraph G {
    rankdir=LR;
    node [shape=box, style="rounded"];

    DISP [label="Disparo\n(API / CLI)"];
    EXT  [label="Extracao\n(chord paralelo)\nEtapa 1"];
    STG  [label="Persistir\nStaging\nEtapa 2"];
    RES  [label="Resolver\nIdentidade\nEtapa 3"];
    KC   [label="Provisionar\nKeycloak\nEtapa 4"];
    TMS  [label="Carregar\ntoken-ms\nEtapa 5"];
    FIM  [label="Fechar\nExecucao\nEtapa 6"];

    DISP -> EXT;
    EXT  -> STG [label="chord\ncallback"];
    STG  -> RES;
    RES  -> KC;
    KC   -> TMS;
    TMS  -> FIM;
}
```

---

## Mapeamento de tasks por etapa

| Etapa | Task Celery | Arquivo |
|---|---|---|
| 1a — Extrair SE1426 | `task_identidade_extrair_se1426` | `apps/controle_etl/tasks.py` |
| 1b — Extrair CoreSSO | `task_identidade_extrair_coresso` | `apps/controle_etl/tasks.py` |
| 1c — Extrair EOL | `task_identidade_extrair_eol_alunos` | `apps/controle_etl/tasks.py` |
| 2 — Resolver identidade | `task_identidade_resolver_identidade` | `apps/controle_etl/tasks.py` |
| 3 — Provisionar Keycloak | `task_provisionar_identidade_keycloak` | `apps/controle_etl/tasks.py` |
| 4 — Carregar token-ms | `task_carregar_atributos_token` | `apps/controle_etl/tasks.py` |
| 5 — Fechar execução | `task_sync_rec_etl` | `apps/controle_etl/tasks.py` |
| 6 — Limpar staging | `task_identidade_limpar_staging` | `apps/controle_etl/tasks.py` |

---

## Retry e tolerância a falha

Todas as tasks de carga usam:

- `max_retries=5`
- backoff exponencial: `min(60 * 2^(tentativa-1), 600)` segundos
- cada tentativa registra um `RastreioTentativa` em `sync_rec_db`
- em caso de falha, `LogEtapaETL` marca a etapa como `FALHA` e a
  `ExecucaoETL` permanece em `executando` até esgotarem as tentativas
