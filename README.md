# SME-Identidade-ETL

Microsserviço ETL responsável por ingerir, reconciliar e provisionar
identidades da SME-SP (servidores, alunos e terceiros) a partir das bases
 (SE1426, CoreSSO, EOL_DB) no Keycloak e no token-ms.

Roda como worker Celery (`etl_worker`) + API Django (`etl_api`), com KeyDB
como broker. O `SYNC_REC_DB` (Postgres próprio do serviço) guarda apenas
controle técnico do pipeline — execuções, watermark, checkpoints, retries e
idempotência de provisionamento —, nunca dados de negócio ou PII. As fontes
 (SQL Server) são lidas em modo somente leitura.

## Versão de Python

- Python alvo do projeto: `3.12`

## Regra de Execução

- O ETL não executa sozinho na inicialização.
- Toda execução roda via pipeline Celery (`task_identidade_executar_pipeline`),
  disparada pela API, pelo dashboard HTML ou por comando de management.
- O serviço `etl_worker` atua como worker Celery; o `etl_api` expõe a API/dashboard.

## Compose

- `docker-compose.yml` (base/produção):
  - `keydb`, `etl_worker`, `etl_api`
- `docker-compose-dev.yml` (desenvolvimento):
  - `postgres_sync_rec`, `keydb`, `etl_worker`, `etl_api` (com debugpy)

## Subir Ambiente

Desenvolvimento:

```bash
docker compose -f docker-compose-dev.yml up --build -d
docker compose -f docker-compose-dev.yml exec etl_api python manage.py migrate --noinput
```

Base/produção:

```bash
docker compose up --build -d
docker compose exec etl_api python manage.py migrate --noinput
```

> As URLs no `.env` devem usar o nome do serviço Docker (`postgres_sync_rec`,
> `keydb`), não `localhost`. Os mapeamentos `5440:5432` e `6380:6379` no
> `docker-compose-dev.yml` são apenas para acesso externo do host.

## Dashboard e Kanban

Monitoramento visual das execuções, sem necessidade de API Key:

- Dashboard: `http://localhost:8001/dashboard/` — última execução por fonte,
  histórico filtrável (fonte/situação/período) e formulário de disparo manual.
- Kanban: `http://localhost:8001/dashboard/kanban/` — etapas do pipeline
  (`LogEtapaETL`) por execução, com totais de entrada/saída/erro.

## API e Swagger

- Swagger UI: `http://localhost:8001/api/v1/docs/`
- Schema: `http://localhost:8001/api/v1/schema/`

Autenticação via API key:

- Header: `X-API-Key`
- Valor: variável `API_KEY` no `.env`

Exemplo:

```bash
curl -H "X-API-Key: sua_chave" http://localhost:8001/api/v1/etl/execucoes/
```

## Pipeline

O pipeline roda em 6 etapas orquestradas por Celery (ver
`apps/controle_etl/tasks.py`), iniciadas por `task_identidade_executar_pipeline`:

1. **Extração** — `task_identidade_extrair_se1426`, `task_identidade_extrair_coresso`
   e `task_identidade_extrair_eol_alunos` em paralelo (chord), persistindo em staging
2. **Resolução de identidade** — `task_identidade_resolver_identidade`: transforma,
   reconcilia e deduplica os registros de staging
3. **Provisionamento Keycloak** — `task_provisionar_identidade_keycloak`: upsert em
   lote, idempotente (`ControleProvisionamento`); desabilitado por padrão
   (ver `ETL_CARGA_KEYCLOAK_BULK_HABILITADO`)
4. **Carga token-ms** — `task_carregar_atributos_token`: envia atributos
   complementares em lotes
5. **Registro operacional** — `task_sync_rec_etl`: fecha a execução, registra
   métricas finais e agenda a limpeza de staging (`task_identidade_limpar_staging`)

A carga em massa do Keycloak fica desligada por padrão. Use `validar_e2e` para
testar o pipeline ponta a ponta com volume reduzido sem afetar produção.

### Sistemas e perfis CoreSSO → Keycloak

Fluxo independente do pipeline principal, para sincronizar clients e roles:

```bash
# Sistemas (clients Keycloak)
curl -X POST http://localhost:8001/api/v1/etl/sistemas/extrair/
curl -X POST http://localhost:8001/api/v1/etl/sistemas/provisionar/ \
     -H 'Content-Type: application/json' -d '{}'
curl http://localhost:8001/api/v1/etl/sistemas/

# Perfis (client roles Keycloak)
curl -X POST http://localhost:8001/api/v1/etl/perfis/extrair/
curl -X POST http://localhost:8001/api/v1/etl/perfis/provisionar/ \
     -H 'Content-Type: application/json' -d '{}'
curl http://localhost:8001/api/v1/etl/perfis/
```

## Disparar o Pipeline

### Via API

```bash
curl -X POST http://localhost:8001/api/v1/etl/execucoes/ \
     -H "X-API-Key: sua_chave" \
     -H 'Content-Type: application/json' \
     -d '{"fonte": "todos", "realm_destino": "sme-apps"}'
```

`fonte` aceita `todos | se1426 | coresso | eol_alunos`.

Acompanhar e cancelar:

```bash
curl -H "X-API-Key: sua_chave" http://localhost:8001/api/v1/etl/execucoes/<pk>/
curl -X POST -H "X-API-Key: sua_chave" http://localhost:8001/api/v1/etl/execucoes/<pk>/cancelar/
```

Consultar histórico de provisionamento de uma identidade (público):

```bash
curl "http://localhost:8001/api/v1/etl/identidades/consultar/?cpf=<CPF>"
curl "http://localhost:8001/api/v1/etl/identidades/consultar/?rf=<RF>"
```

### Via management command

Execução direta (síncrona se `CELERY_TASK_ALWAYS_EAGER=1`, caso contrário
requer worker Celery ativo):

```bash
docker compose -f docker-compose-dev.yml exec etl_api \
  python manage.py executar_etl --fonte todos --realm sme-apps

# Volume reduzido para teste local
docker compose -f docker-compose-dev.yml exec etl_api \
  python manage.py executar_etl --fonte se1426 --lote-maximo 50
```

## Identificadores

O `username` no Keycloak segue esta prioridade: RF e CPF e fallback
`{fonte}-{id}`.

Registros sem RF **e** sem CPF são marcados como `erro` na etapa de resolução
de identidade e não chegam ao Keycloak.

## Variáveis de Ambiente

Ver `.env.example` para a lista completa e comentada. Principais grupos:

```env
# Django / API
DJANGO_SECRET_KEY=
API_KEY=
API_KEY_HEADER=X-API-Key

# SYNC_REC_DB — único banco do ETL (controle técnico, sem PII)
SYNC_REC_DB_URL=postgresql://postgres:postgres@postgres_sync_rec:5432/identidade_sync_rec

# Celery / KeyDB
URL_KEYDB=redis://keydb:6379/0

# Keycloak
KEYCLOAK_URL_SERVIDOR=
KEYCLOAK_REALM=sme-apps
KEYCLOAK_CLIENT_ID=
KEYCLOAK_CLIENT_SECRET=
KEYCLOAK_SUFIXO_CLIENT=dev   # sufixo do clientId: {sigla}-{sufixo}

# token-ms
TOKEN_MS_URL=
TOKEN_MS_TOKEN_INTERNO=

# Flags e controle de volume
ETL_CARGA_KEYCLOAK_BULK_HABILITADO=false
ETL_CHUNK_SIZE=500
ETL_LOTE_MAXIMO=0   # 0 = sem limite; use baixo (ex. 200) para testes

# SE1426 / EOL_DB / CoreSSO — SQL Server (read-only) + fallback REST
SE1426_DB_SERVIDOR=
EOL_DB_STRING_CONEXAO=
CORESSO_DB_SERVIDOR=
```

## Atalhos Make

Use `make help` para listar todos os comandos disponíveis. Os principais:

**Infraestrutura**

| Comando | Descrição |
|---|---|
| `make build` | Build da imagem `etl_api` |
| `make up` | Sobe `postgres_sync_rec` e `keydb` em background |
| `make down` | Derruba todos os containers |
| `make logs` | Acompanha logs do `etl_api` em tempo real |
| `make shell` | Abre shell Django interativo |

**Migrações**

| Comando | Descrição |
|---|---|
| `make migrate` | Aplica migrations no `SYNC_REC_DB` |

**Testes**

| Comando | Descrição |
|---|---|
| `make test` | Todos os apps com cobertura ≥80% |
| `make test-controle` | Apenas `apps.controle_etl` |
| `make test-extracao` | Apenas `apps.extracao` |

**Qualidade**

| Comando | Descrição |
|---|---|
| `make lint` | `ruff` + `black` + `isort` + `mypy` |
| `make coverage` | Relatório HTML em `docs/_cov/` |
| `make schema` | Gera schema OpenAPI em `schema.yml` |
| `make docs` | Gera documentação Sphinx em `docs/_build/html/` |

**Scripts operacionais**

| Comando | Descrição |
|---|---|
| `make carregar-perfis` | Carrega todos os perfis CoreSSO como client roles |
| `make carregar-perfis SIS_ID=42` | Apenas o sistema CoreSSO `id=42` |
| `make carregar-perfis SIS_ID=42 REALM=sme-hom` | Idem, em outro realm |

**Validação E2E**

| Comando | Descrição |
|---|---|
| `make validar-e2e` | Extração → resolução → Keycloak (15 registros/fonte) + `validacao.md` |
| `make validar-e2e LOTE_MAXIMO=5` | Reduz o volume de teste |
| `make validar-e2e REALM=sme-hom` | Outro realm Keycloak |

## Testes

Executados via container, nunca direto no host:

```bash
make test
```

Equivalente direto:

```bash
docker compose -f docker-compose-dev.yml run --rm etl_api \
  python -m pytest --cov=apps --cov-report=term-missing --cov-fail-under=80 -v
```

Cobertura mínima configurada em 80%.
