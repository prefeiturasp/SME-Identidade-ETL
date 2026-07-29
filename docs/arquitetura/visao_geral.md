# Visão Geral da Arquitetura

## Bancos configurados

O projeto usa dois bancos PostgreSQL distintos:

| Alias Django | Variável de ambiente | Finalidade |
|---|---|---|
| `default` | `DATABASE_URL` | Controle ETL (`ExecucaoETL`, `LogEtapaETL`, checkpoints, watermarks) |
| `sync_rec_db` | `SYNC_REC_DB_URL` | Rastreio operacional (`RastreioTentativa`) |

O staging (tabelas `UsuarioServidorStaging`, `UsuarioAlunoStaging`, `UsuarioTerceiroStaging`,
`SistemaStaging`, `PerfilCoressoStaging`) também reside no `default`.

---

## Fontes de origem

Todos os bancos de origem são SQL Server, acessados via `pyodbc`, somente-leitura:

| Fonte | Variável de ambiente | Entidades extraídas |
|---|---|---|
| SE1426 | `SE1426_DB_*` | servidores ativos |
| CoreSSO | `CORESSO_DB_*` | usuários externos, sistemas, perfis/grupos |
| EOL_DB | `EOL_DB_*` | alunos matriculados |

---

## Destinos de carga

| Destino | Protocolo | Finalidade |
|---|---|---|
| Keycloak | Admin REST API (`python-keycloak`) | upsert de usuários, roles, grupos |
| token-ms | HTTP REST (`httpx`) | atributos complementares em lote |

---

## Fluxo lógico

```{graphviz}
digraph G {
    rankdir=TB;
    node [shape=box, style="rounded"];

    subgraph cluster_src {
        label="Origens (SQL Server, read-only)";
        SE1426; CORESSO [label="CoreSSO"]; EOL;
    }

    subgraph cluster_etl {
        label="SME-Identidade-ETL";
        EXT  [label="Extracao\n(chord paralelo)"];
        STG  [label="Staging DB\n(PostgreSQL)"];
        RES  [label="Resolucao /\nDeduplicacao"];
        KC   [label="Provisionamento\nKeycloak"];
        TMS  [label="Carga\ntoken-ms"];
        CTL  [label="Controle ETL\n(ExecucaoETL)"];
    }

    SE1426  -> EXT;
    CORESSO -> EXT;
    EOL     -> EXT;
    EXT     -> STG;
    STG     -> RES;
    RES     -> KC;
    RES     -> TMS;
    KC      -> CTL [style=dashed, label="metricas"];
    TMS     -> CTL [style=dashed, label="metricas"];
}
```

---

## Infraestrutura Celery

| Componente | Localização |
|---|---|
| App Celery | `config/celery.py` |
| Tasks do pipeline | `apps/controle_etl/tasks.py` |
| Tasks de staging | `apps/staging/tasks.py` |
| Tasks de extração | `apps/extracao/tasks.py` |
| Worker com hot-reload | `scripts/watch_celery.py` |

**Configuração:**
- Broker: KeyDB/Redis (`CELERY_BROKER_URL`)
- `max_retries=5`, backoff exponencial (base 60s, máx 600s)
- Extração em paralelo via `chord` — as 3 tasks de extração disparam
  simultaneamente e o callback de resolução só executa após todas concluírem

---

## Princípio de idempotência

Toda operação de carga é idempotente:

- **Staging** — `update_or_create` por `(id_execucao, fonte, id_origem)`
- **Keycloak** — busca o usuário pelo username antes de criar; atualiza se
  `hash_extracao` (dado da fonte) ou `hash_keycloak` (payload) mudaram
- **token-ms** — upsert por identificador externo; reenvia só se
  `hash_token_ms` mudou desde a última confirmação
- **ControleProvisionamento** — registra 3 hashes independentes por
  entidade em `(tipo_entidade, sistema_origem, id_origem, realm_destino)`,
  um por estágio do pipeline (extração, Keycloak, token-ms) — cada
  estágio decide, sem depender dos outros, se precisa reexecutar; ver
  [Modelos de Controle](../controle/modelos.md)
