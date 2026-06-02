# SME-Identidade-ETL

Microsserviço de ETL responsável por consolidar usuários das bases legadas (SE1426, EOL_DB, CoreSSO) no Keycloak e no Token-MS.

Roda como **worker Celery + API Django REST**. O banco de staging fica no PostgreSQL próprio do serviço; as fontes legadas (SQL Server) são acessadas em modo somente-leitura.

---

## 📚 Documentação Adicional

- **[README_INICIAR_PROJETO.md](README_INICIAR_PROJETO.md)** — Guia completo: scripts, testes, cobertura e resumo dos PRs
- **[scripts/README.md](scripts/README.md)** — Documentação detalhada dos scripts operacionais

---

## Visão Geral da Arquitetura

```
SE1426 (SQL Server) ──┐
EOL_DB (SQL Server) ──┤                      ┌──→ Keycloak (realm COTIC)
CoreSSO (SQL Server) ─┼──→ ETL (etl_db) ────┤
SME Integração (API) ─┘                      └──→ Token-MS
```

O pipeline é orquestrado pelo **Celery** em etapas encadeadas (chord + chain). Cada execução gera um registro `ETLExecution` com log detalhado de cada etapa em `ETLStepLog`.

Para a documentação visual completa, consulte [Docs/diagramas/README.md](../Docs/diagramas/README.md).

---

## Stack

| Componente | Tecnologia |
|---|---|
| API | Django 5.1 + Django REST Framework |
| Worker | Celery 5 |
| Broker | KeyDB (Redis-compatible) |
| Banco próprio | PostgreSQL (via PgBouncer) |
| Fontes legadas | SQL Server (pyodbc / mssql) |
| Destino identidades | Keycloak Admin SDK |
| Destino claims | Token-MS (HTTP interno) |

---

## Pré-requisitos

- Docker e Docker Compose
- Acesso às bases SQL Server (SE1426, EOL e CoreSSO) — ver variáveis abaixo
- Keycloak acessível com usuário admin configurado
- Token-MS rodando e acessível

---

## Como rodar

```bash
docker compose up -d
docker compose exec etl-ms-api python manage.py migrate
```

Verificar saúde:

```bash
curl http://localhost:8001/api/health/
curl http://localhost:8001/api/health/ready/
```

---

## Endpoints principais

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/health/` | liveness |
| GET | `/api/health/ready/` | readiness |
| POST | `/api/etl/executions/run/` | dispara pipeline completo |
| GET | `/api/etl/executions/` | lista execuções |
| GET | `/api/etl/executions/{id}/` | detalhe + steps |
| POST | `/api/etl/test-kc-upsert/` | testa upsert de um usuário por CPF ou RF |
| POST | `/api/etl/sistemas/extract/` | extrai sistemas do CoreSSO |
| POST | `/api/etl/sistemas/load-keycloak/` | cria/atualiza clients no Keycloak |
| GET | `/api/docs/` | Swagger UI |

Disparar o pipeline manualmente:

```bash
# Pipeline completo
curl -X POST http://localhost:8001/api/etl/executions/run/ \
     -H 'Content-Type: application/json' \
     -d '{"source": "all"}'

# Testar um usuário específico (por CPF)
curl -X POST http://localhost:8001/api/etl/test-kc-upsert/ \
     -H 'Content-Type: application/json' \
     -d '{"cpf": "12345678901"}'

# Por RF (quando não tem CPF)
curl -X POST http://localhost:8001/api/etl/test-kc-upsert/ \
     -H 'Content-Type: application/json' \
     -d '{"rf": "8465061"}'
```

---

## Pipeline

O pipeline roda em 7 etapas orquestradas pelo Celery (ver `core/tasks.py`):

| Etapa | Task | Descrição |
|---|---|---|
| 1 | `extract_*` | Lê SE1426, EOL_DB e CoreSSO em paralelo (chord) |
| 2 | `transform_staging` | Normaliza CPF/RF/nome, enriquece lotações |
| 3 | `crossref_dedup` | Agrupa o mesmo servidor em múltiplas fontes via Union-Find |
| 4 | `decide_target` | Monta payloads para Keycloak e Token-MS |
| 5 | `load_keycloak` | Upsert de usuários, grupos e roles no Keycloak |
| 6 | `load_token_ms` | Envia atributos complementares em lotes ao Token-MS |
| 7 | `audit_etl` | Fecha a execução, registra métricas e retroalimenta o CoreSSO |

O load em massa do Keycloak fica **desligado por padrão** (`ETL_LOAD_KEYCLOAK_BULK_ENABLED=false`). Habilite em produção após validar os dados com `test-kc-upsert`.

---

## Identificadores

O `username` no Keycloak segue esta prioridade:

1. RF (registro funcional)
2. CPF
3. Fallback: `{source}-{id}`

Registros sem RF **e** sem CPF são marcados como `ERROR` na etapa de dedup e não chegam ao Keycloak.

---

## Variáveis de ambiente

```env
# App
DJANGO_SECRET_KEY=          # obrigatório em produção
DEBUG=false
ALLOWED_HOSTS=*
LOG_LEVEL=INFO

# Banco próprio do ETL (PostgreSQL)
DATABASE_URL=postgres://etl:etl@pgbouncer:6432/etl_db

# Celery / Broker
CELERY_BROKER_URL=redis://keydb:6379/2
CELERY_RESULT_BACKEND=redis://keydb:6379/3

# Keycloak
KEYCLOAK_SERVER_URL=
KEYCLOAK_REALM=sme-apps
KEYCLOAK_ADMIN_USER=
KEYCLOAK_ADMIN_PAWD=
KEYCLOAK_CLIENT_SUFFIX=prod   # sufixo do clientId: {sigla}-prod
KEYCLOAK_VERIFY_SSL=true

# Flags
ETL_LOAD_KEYCLOAK_BULK_ENABLED=false   # true para habilitar carga em lote

# Token-MS
TOKEN_MS_URL=http://token-ms:8000
TOKEN_MS_INTERNAL_TOKEN=
TOKEN_MS_TIMEOUT=60

# SE1426 (SQL Server)
SE1426_DB_SERVER=
SE1426_DB_NAME=se1426
SE1426_DB_USER=
SE1426_DB_PASSWORD=

# EOL DB (SQL Server — connection string completa)
EOL_DB_CONNECTION_STRING=

# CoreSSO (SQL Server — somente-leitura)
CORESSO_DB_SERVER=
CORESSO_DB_NAME=CoreSSO
CORESSO_DB_USER=
CORESSO_DB_PASSWORD=

# SME Integração API (fallback para alunos)
SME_INTEGRACAO_BASE_URL=https://hom-smeintegracaoapi.sme.prefeitura.sp.gov.br
SME_INTEGRACAO_API_KEY=
SME_INTEGRACAO_LOGIN=
SME_INTEGRACAO_PASSWORD=
```

---

## Testes

```bash
python -m pytest tests/ --cov=core --cov=staging --cov=extract -q
```

280 testes · cobertura mínima configurada em 80% (`pytest.ini`).

```bash
# Cobertura detalhada por módulo
python -m pytest tests/ --cov=core --cov=staging --cov=extract --cov-report=term-missing

# Cobertura em HTML (abre no browser se xdg-utils disponível, senão sobe servidor em :9000)
./scripts/coverage.sh 
```

---

## Estrutura de pastas

```
etl_ms/          → configurações Django (settings, urls, wsgi, celery)
core/            → orquestração do pipeline (tasks, models, keycloak_client, token_ms_client)
staging/         → modelos de staging, transform e dedup
extract/         → tasks de extração por fonte (SE1426, EOL, CoreSSO)
tests/           → 280 testes unitários e de integração
Docs/diagramas/  → 11 diagramas PNG do fluxo e schema
```
