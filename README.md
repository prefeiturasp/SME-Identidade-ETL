# etl-ms

Serviço de ETL responsável por consolidar usuários das bases legadas (SE1426, EOL_DB, CoreSSO) no Keycloak e no token-ms.

Roda como worker Celery + API Django. O banco de staging fica no Postgres próprio do serviço; as fontes legadas (SQL Server) são lidas em modo somente-leitura.

---

## Pré-requisitos

- Docker e Docker Compose
- Acesso às bases SQL Server (SE1426 e CoreSSO) — ver variáveis abaixo
- Keycloak acessível

---

## Como rodar

```bash
docker compose up -d
docker compose exec etl-ms-api python manage.py migrate
```

Disparar o pipeline manualmente:

```bash
# Pipeline completo
curl -X POST http://localhost:8001/api/etl/executions/run/ \
     -H 'Content-Type: application/json' \
     -d '{"source": "all"}'

# Testar um usuário específico
curl -X POST http://localhost:8001/api/etl/test-kc-upsert/<CPF>/ \
     -H 'Content-Type: application/json' -d '{}'

# Por RF (quando não tem CPF)
curl -X POST http://localhost:8001/api/etl/test-kc-upsert/ \
     -H 'Content-Type: application/json' \
     -d '{"rf": "8465061"}'
```

Sistemas CoreSSO e Keycloak clients:

```bash
curl -X POST http://localhost:8001/api/etl/sistemas/extract/
curl -X POST http://localhost:8001/api/etl/sistemas/load-keycloak/ \
     -H 'Content-Type: application/json' -d '{}'
```

---

## Pipeline

O pipeline roda em 8 etapas orquestradas por Celery (ver `core/tasks.py`):

1. **Extract** — lê SE1426, EOL_DB e CoreSSO em paralelo (chord)
2. **Transform** — normaliza CPF/RF/nome, faz lookup de lotação
3. **Crossref / Dedup** — agrupa registros do mesmo servidor por CPF ↔ RF (Union-Find)
4. **Decide target** — separa o que vai pro Keycloak do que vai pro token-ms
5. **Load Keycloak** — upsert em massa (desabilitado por padrão, ver `ETL_LOAD_KEYCLOAK_BULK_ENABLED`)
6. **Load token-ms** — envia atributos complementares em lotes
7. **Audit** — fecha a execução e registra métricas

O load em massa do Keycloak fica desligado por padrão. Para testar um usuário pontualmente, use o endpoint `test-kc-upsert`.

---

## Identificadores

O `username` no Keycloak segue esta prioridade: RF e CPF e fallback `{source}-{id}`.

Registros sem RF **e** sem CPF são marcados como `ERROR` na etapa de dedup e não chegam ao Keycloak.

---

## Variáveis de ambiente

```env
# SE1426 / EOL_DB (mesmo servidor SQL Server)
SE1426_DB_SERVER=
SE1426_DB_NAME=
SE1426_DB_USER=
SE1426_DB_PASSWORD=

# CoreSSO (read-only)
CORESSO_DB_SERVER=
CORESSO_DB_NAME=
CORESSO_DB_USER=
CORESSO_DB_PASSWORD=
CORESSO_API_URL=          # fallback REST caso o SQL Server não esteja acessível

# Keycloak
KEYCLOAK_SERVER_URL=
KEYCLOAK_REALM=sme-apps
KEYCLOAK_ADMIN_USER=
KEYCLOAK_ADMIN_PAWD=
KEYCLOAK_CLIENT_SUFFIX=prod   # sufixo do clientId: {sigla}-prod

# Flags
ETL_LOAD_KEYCLOAK_BULK_ENABLED=false

# token-ms
TOKEN_MS_BASE_URL=
TOKEN_MS_INTERNAL_TOKEN=
```

---

## Testes

```bash
python -m pytest tests/ --cov=core --cov=staging --cov=extract -q
```

Cobertura mínima configurada em 80%.
