# Caminhos de Execução

## 1. Via API REST (assíncrono — produção)

```bash
POST /api/v1/etl/execucoes/
X-API-Key: <chave>
Content-Type: application/json

{"fonte": "todos", "realm_destino": "sme-apps"}
```

Cria uma `ExecucaoETL` com `situacao="pendente"` e dispara
`task_identidade_executar_pipeline.apply_async`.

---

## 2. Via comando Django (síncrono — debug)

```bash
docker compose -f docker-compose-dev.yml run --rm etl_api \
  python manage.py executar_etl --fonte todos --realm sme-apps
```

Executa o pipeline diretamente, sem Celery. Útil para depuração local.

---

## 3. Via workers Celery com hot-reload

```bash
docker compose -f docker-compose-dev.yml up etl_worker_celery etl_worker_keycloak etl_worker_token_ms
```

Em dev, os workers são divididos por fila (`etl_worker_celery`,
`etl_worker_keycloak`, `etl_worker_token_ms` — ver
[Visão Geral](../arquitetura/visao_geral.md)), cada um rodando
`scripts/watch_celery.py` com `ETL_WORKER_FILAS` próprio. O script inicia o
worker com `watchdog.PollingObserver` e reinicia automaticamente ao
detectar mudanças em `/app/apps` ou `/app/config`. Substitui `watchmedo
auto-restart` em ambientes sem inotify (Docker Desktop no macOS/Windows).

Monitoramento das filas/tasks via Celery Flower: `http://localhost:5555`
(serviço `etl_flower`).

---

## 4. Dashboard HTML

```
GET /etl/dashboard/
```

Exibe execuções em andamento e permite disparar novas execuções via formulário.

---

## Fluxo de chamadas

```{graphviz}
digraph G {
    rankdir=LR;
    node [shape=box, style="rounded"];

    API  [label="POST /api/v1/\netl/execucoes/"];
    CMD  [label="manage.py\nexecutar_etl"];
    TASK [label="task_identidade_\nexecutar_pipeline\n(Celery)"];
    PIPE [label="Pipeline\n(chord + chain)"];
    CTL  [label="ExecucaoETL"];

    API -> TASK -> PIPE;
    CMD -> PIPE;
    PIPE -> CTL [style=dashed, label="atualiza"];
}
```
