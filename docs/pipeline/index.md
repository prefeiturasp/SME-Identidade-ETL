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

A `chain` síncrona do pipeline termina no Keycloak — a carga no
token-ms é disparada **fire-and-forget**, por usuário, a partir do
sucesso individual no Keycloak, numa fila própria
(`etl_carga_token_ms`), sem bloquear o fechamento da `ExecucaoETL`. Se
todas as tasks de extração de um chord falham (esgotam os retries),
`task_identidade_tratar_erro_pipeline` (`link_error` do Celery) marca
a execução como `falha` — evita que ela fique presa em `executando`
indefinidamente.

```{graphviz}
digraph G {
    rankdir=LR;
    node [shape=box, style="rounded"];

    DISP [label="Disparo\n(API / CLI)"];
    EXT  [label="Extracao\n(chord paralelo)\nEtapas 1-2b"];
    STG  [label="Persistir\nStaging"];
    RES  [label="Resolver\nIdentidade\nEtapa 3"];
    KC   [label="Provisionar\nKeycloak\nEtapa 4"];
    FIM  [label="Registro\nOperacional\nEtapa 6"];
    TMS  [label="Carregar\ntoken-ms\nEtapa 5\n(por usuario,\nfila separada)"];
    ERR  [label="Falha no chord\nde extracao"];

    DISP -> EXT;
    EXT  -> STG [label="chord\ncallback"];
    STG  -> RES;
    RES  -> KC;
    KC   -> FIM;
    KC   -> TMS [style=dashed, label="fire-and-forget\npor usuario confirmado"];
    EXT  -> ERR [style=dashed, label="link_error"];
    ERR  -> FIM [style=dashed, label="marca falha"];
}
```

---

## Mapeamento de tasks por etapa

Numeração conforme `LogEtapaETL.NomeEtapa` (`apps/controle_etl/models.py`):

| Etapa | Task Celery | Fila | Arquivo |
|---|---|---|---|
| 1 — Extrair SE1426 | `task_identidade_extrair_se1426` | `etl_extracao` | `apps/controle_etl/tasks.py` |
| 2 — Extrair CoreSSO | `task_identidade_extrair_coresso` | `etl_extracao` | `apps/controle_etl/tasks.py` |
| 2b — Extrair EOL Alunos | `task_identidade_extrair_eol_alunos` | `etl_extracao` | `apps/controle_etl/tasks.py` |
| 3 — Resolver identidade | `task_identidade_resolver_identidade` | `etl_transformacao` | `apps/controle_etl/tasks.py` |
| 4 — Provisionar Keycloak | `task_provisionar_identidade_keycloak` | `etl_carga_keycloak` | `apps/controle_etl/tasks.py` |
| 5 — Carregar atributos token (individual, fora da chain) | `task_carregar_atributo_token_individual` | `etl_carga_token_ms` | `apps/controle_etl/tasks.py` |
| 5 — Carregar atributos token (lote, fallback manual) | `task_carregar_atributos_token` | `etl_carga_token_ms` | `apps/controle_etl/tasks.py` |
| 6 — Registro operacional | `task_sync_rec_etl` | `celery` | `apps/controle_etl/tasks.py` |
| — Limpar staging (agendada por `task_sync_rec_etl`) | `task_identidade_limpar_staging` | `celery` | `apps/controle_etl/tasks.py` |
| — Tratar erro do chord de extração | `task_identidade_tratar_erro_pipeline` | — (`link_error`) | `apps/controle_etl/tasks.py` |

---

## Retry e tolerância a falha

Todas as tasks de carga usam:

- `max_retries=5`
- backoff exponencial: `min(60 * 2^(tentativa-1), 600)` segundos
- cada tentativa registra um `RastreioTentativa` em `sync_rec_db`
- em caso de falha numa etapa da chain, `LogEtapaETL` marca a etapa
  como `FALHA` e a task reagenda via `self.retry(...)` até esgotar as
  tentativas
- se **todas** as tasks de extração de um chord esgotarem os retries,
  `task_identidade_tratar_erro_pipeline` marca a `ExecucaoETL` como
  `falha` (só se ainda estiver `executando`) — sem esse handler, a
  execução ficaria presa indefinidamente
- retries do Keycloak (`_com_reintento`) e do token-ms (`_e_retriavel`)
  checam o status HTTP antes de retentar — erros de validação (400)
  propagam na 1ª tentativa, sem esperar backoff
