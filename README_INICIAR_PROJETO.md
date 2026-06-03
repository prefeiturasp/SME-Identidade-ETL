# Guia Completo — SME-Identidade-ETL

> Documentação completa: setup, scripts, testes, cobertura e resumo dos PRs #10-#14

---

## 🚀 Início Rápido (5 minutos)

```bash
# 1. Entre no diretório
cd SME-Identidade-ETL

# 2. Inicie a stack completa
./scripts/start-etl-local.sh

# 3. Aguarde ~2 minutos
# Acesse: http://localhost:8001/api/docs/
```

---

## Índice

1. [Pré-requisitos](#pré-requisitos)
2. [Scripts Disponíveis](#scripts-disponíveis)
3. [Iniciar o Projeto Localmente](#iniciar-o-projeto-localmente)
4. [Executar Testes e Ver Cobertura](#executar-testes-e-ver-cobertura)
5. [Operações Comuns](#operações-comuns)
6. [Resumo dos PRs](#resumo-dos-prs)
7. [Troubleshooting](#troubleshooting)

---

## Pré-requisitos

Antes de iniciar, certifique-se de ter instalado:

- **Docker** e **Docker Compose v2**
- **Python 3.11+** (para testes locais fora do container)
- **Git**
- Acesso à rede interna `10.49.x.x` (necessário para acessar bases SQL Server legadas)

### Variáveis de Ambiente

O projeto usa um arquivo `.env.local` com as credenciais e configurações. Este arquivo é criado automaticamente a partir do `.env.local.example` na primeira execução do script `start-etl-local.sh`.

**Importante**: Revise e ajuste as credenciais antes de rodar pela primeira vez:

```bash
nano SME-Identidade-ETL/.env.local
```

---

## Scripts Disponíveis

Todos os scripts estão em `SME-Identidade-ETL/scripts/`:

| Script | Descrição |
|--------|-----------|
| `start-etl-local.sh` | **Inicia todo o ambiente local** (PostgreSQL + KeyDB + Keycloak + ETL API/Worker/Beat) |
| `reset-etl-local.sh` | **Limpa completamente** o ambiente (remove containers e volumes) |
| `trigger-pipeline.sh` | **Dispara o pipeline ETL** manualmente via API |
| `resume-pipeline.sh` | **Retoma um pipeline** que parou ou falhou em algum step |
| `coverage.sh` | **Gera relatório HTML de cobertura** de testes |
| `clean-keycloak-realms.sh` | **Limpa realms do Keycloak** (útil para resetar estado de testes) |

---

## Iniciar o Projeto Localmente

### 1. Primeira Execução (Setup Completo)

```bash
cd /home/cristhian/api/SME-Identidade-ETL

# Executa o script de inicialização
./scripts/start-etl-local.sh
```

**O que o script faz:**

1. ✅ Verifica e cria `.env.local` a partir do template (se não existir)
2. ✅ Cria as redes Docker necessárias (`sme-identidade`, `api_identidade-net`)
3. ✅ Sobe o Keycloak local (porta 8080) com PostgreSQL próprio
4. ✅ Aguarda Keycloak ficar disponível (~90s na primeira vez)
5. ✅ Sobe os containers do ETL:
   - `local-etl-postgres` (PostgreSQL 16 na porta 5437)
   - `local-etl-keydb` (KeyDB/Redis na porta 6382)
   - `local-etl-api` (Django + Gunicorn na porta 8001)
   - `local-etl-worker` (Celery Worker)
   - `local-etl-beat` (Celery Beat)
6. ✅ Aguarda API ficar healthy (até 2 minutos)
7. ✅ Executa smoke tests (verifica endpoints `/api/health/` e `/api/health/ready/`)
8. ✅ Exibe resumo de acesso

**Tempo estimado**: 2-3 minutos na primeira execução (download de imagens Docker).

### 2. Verificar se Está Funcionando

Após a execução bem-sucedida do script, você verá:

```
─────────────────────────────────────────────────────────────────────────────
  ✅  ETL-MS rodando localmente
─────────────────────────────────────────────────────────────────────────────

  ETL API         → http://localhost:8001
  Swagger/Docs    → http://localhost:8001/api/docs/
  Django Admin    → http://localhost:8001/admin/
  Keycloak Admin  → http://localhost:8080 (admin / admin)

  PostgreSQL ETL  → localhost:5437 (user: etl / db: etl_db)
  KeyDB/Redis     → localhost:6382

─────────────────────────────────────────────────────────────────────────────
  📋  Próximos passos

  • Verificar execuções:     http://localhost:8001/api/etl/executions/
  • Disparar pipeline:       ./scripts/trigger-pipeline.sh
  • Ver logs da API:         docker logs -f local-etl-api
  • Ver logs do worker:      docker logs -f local-etl-worker
─────────────────────────────────────────────────────────────────────────────
```

### 3. Acessar os Serviços

| Serviço | URL | Credenciais |
|---------|-----|-------------|
| **API ETL** | http://localhost:8001 | - |
| **Swagger UI** | http://localhost:8001/api/docs/ | - |
| **Django Admin** | http://localhost:8001/admin/ | (criar superuser) |
| **Keycloak Admin** | http://localhost:8080 | admin / admin |

Para criar um superuser do Django:

```bash
docker exec -it local-etl-api python manage.py createsuperuser
```

---

## Executar Testes e Ver Cobertura

### 1. Executar Todos os Testes

```bash
cd /home/cristhian/api/SME-Identidade-ETL

# Testes rápidos (sem relatório de cobertura)
python -m pytest tests/ -q

# Testes com cobertura (resumo no terminal)
python -m pytest tests/ --cov=core --cov=staging --cov=extract -q

# Testes com cobertura detalhada (mostra linhas não cobertas)
python -m pytest tests/ --cov=core --cov=staging --cov=extract --cov-report=term-missing
```

**Cobertura mínima**: 80% (configurado em `pytest.ini`)

**Total de testes**: ~445 testes (dependendo da branch)

### 2. Gerar Relatório HTML de Cobertura

```bash
# Executa testes, gera HTML e tenta abrir no navegador
./scripts/coverage.sh

# Apenas gera o relatório sem abrir
./scripts/coverage.sh --no-open

# Sobe servidor HTTP para visualizar (http://localhost:9000)
./scripts/coverage.sh --serve
```

O relatório HTML é gerado em `htmlcov/` e mostra:
- ✅ Cobertura por módulo/arquivo
- ✅ Linhas cobertas vs não cobertas
- ✅ Navegação interativa pelo código

**Acesso**: http://localhost:9000 (quando usando `--serve`)

### 3. Testes Específicos

```bash
# Apenas testes de core
python -m pytest tests/test_core*.py -v

# Apenas testes de staging
python -m pytest tests/test_staging*.py -v

# Apenas testes de extract
python -m pytest tests/test_extract*.py -v

# Um arquivo específico
python -m pytest tests/test_core_tasks.py -v

# Um teste específico
python -m pytest tests/test_core_tasks.py::TestLoadKeycloak::test_load_disabled -v
```

---

## Operações Comuns

### Disparar o Pipeline ETL

O script `trigger-pipeline.sh` permite disparar o pipeline manualmente com várias opções:

```bash
# Pipeline padrão (servidores, 100k registros, sem load no Keycloak)
./scripts/trigger-pipeline.sh

# Processar apenas fonte SE1426
./scripts/trigger-pipeline.sh --source se1426

# Processar todos os tipos de usuário (servidor + aluno)
./scripts/trigger-pipeline.sh --all-users

# Sem limite de registros
./scripts/trigger-pipeline.sh --no-limit

# Habilitar carga no Keycloak (step 6)
./scripts/trigger-pipeline.sh --load-keycloak

# Processar apenas alunos, com limite de 50k
./scripts/trigger-pipeline.sh --user-types aluno --max-records 50000

# Disparar e acompanhar execução
./scripts/trigger-pipeline.sh --watch

# Ver todas as opções
./scripts/trigger-pipeline.sh --help
```

### Retomar Pipeline que Falhou

```bash
# Retoma última execução que parou
./scripts/resume-pipeline.sh

# Retoma execução específica
./scripts/resume-pipeline.sh --id <execution-id>

# Mostra o que seria feito sem executar
./scripts/resume-pipeline.sh --dry-run

# Força início a partir de step específico
./scripts/resume-pipeline.sh --from crossref_dedup

# Ver todas as opções
./scripts/resume-pipeline.sh --help
```

### Ver Logs

```bash
# Logs da API (Django)
docker logs -f local-etl-api

# Logs do Worker (Celery)
docker logs -f local-etl-worker

# Logs do Beat (Scheduler)
docker logs -f local-etl-beat

# Logs do PostgreSQL
docker logs -f local-etl-postgres

# Logs do Keycloak
docker logs -f local-keycloak
```

### Resetar Ambiente Completo

```bash
# Remove TODOS os containers e volumes do ETL + Keycloak
./scripts/reset-etl-local.sh

# Sem confirmação (útil para CI/automação)
./scripts/reset-etl-local.sh --yes
```

**⚠️ ATENÇÃO**: Esta operação é **irreversível** e apaga todos os dados locais!

### Limpar Apenas Realms do Keycloak

```bash
# Remove realms criados durante testes
./scripts/clean-keycloak-realms.sh
```

---

## Resumo dos PRs

Abaixo está um resumo dos PRs que foram abertos no repositório `prefeiturasp/SME-Identidade-ETL`:

### PR #10 — Configuração do Ambiente Local
**Branch**: `pr-76737f6-5227a10-ac6c087`  
**Resumo**: Setup completo da stack Docker Compose, README reescrito, correção de issues Sonar

**Principais mudanças**:
- ✅ Criação do `docker-compose.local.yml` completo
- ✅ README.md reescrito com visão geral da arquitetura
- ✅ Extração da camada de serviço `KeycloakUpsertService` para `core/service.py` (Sonar S1188)
- ✅ Correção de complexidade cognitiva em `health.py` (Sonar S3776)
- ✅ Ampliação da cobertura de testes em `core/tasks` e `core/views`
- ✅ Scripts operacionais criados

**Arquivos impactados**: 13 arquivos · +1.507 / -268 linhas  
**Testes**: 300 passed · cobertura 88%

---

### PR #11 — Otimização da Pipeline
**Branch**: `pr-9a91607-d32c12d`  
**Resumo**: Extração de helpers, refatoração do Keycloak client, novos campos de controle, endpoint sync-selective

**Principais mudanças**:
- ✅ Helpers extraídos em `core/tasks.py` (`_step_done`, `_get_or_create_step`)
- ✅ Novo método `upsert_kc_client()` para criar/atualizar clients KC via Registration API
- ✅ Novos campos `load_keycloak` e `load_token_ms` em `ETLExecution` (controle por execução)
- ✅ Novo endpoint `/api/etl/sync-selective/` para upsert pontual por CPF/RF
- ✅ Correção de query SE1426 (campo `funcao`)
- ✅ Scripts operacionais: `reset-etl-local.sh`, `start-etl-local.sh`, `trigger-pipeline.sh`

**Arquivos impactados**: 21 arquivos · +2.193 / -282 linhas  
**Testes**: 364 passed · cobertura 88%

---

### PR #12 — Refactor de Orquestração/Infra
**Branch**: `pr-1ada40a-f62dc59`  
**Resumo**: Ajustes no fluxo de pipeline (pre-check de realm), melhorias em scripts operacionais, tuning de worker

**Principais mudanças**:
- ✅ Ajuste no fluxo de dispatch da pipeline (pre-check de realm antes do chord)
- ✅ Suporte ao fluxo de garantia de realm antes do despacho completo
- ✅ Tuning de limite de consumo do worker/Celery
- ✅ Novo script `clean-keycloak-realms.sh` para limpeza de realms
- ✅ Ajustes em `reset-etl-local.sh` e `start-etl-local.sh`

**Arquivos impactados**: 8 arquivos · +303 / -42 linhas  
**Testes**: 364 passed · 432 warnings

---

### PR #13 — Evolução Estrutural da Pipeline
**Branch**: `pr-ae75a4d-776e733`  
**Resumo**: Controles finos de execução, autenticação interna, execução isolada por step, documentação operacional

**Principais mudanças**:
- ✅ Autenticação interna via `X-Internal-Token` (`core/authentication.py`)
- ✅ Novos campos em `ETLExecution`: `max_records`, `max_records_extract`, `user_types`, `skip_steps`
- ✅ Pipeline com filtros por tipo de usuário e skip de etapas
- ✅ Endpoints por step: `run-sync-catalogo`, `run-extract`, `run-transform`, `run-crossref`, etc.
- ✅ Documentação operacional completa (`docs/OPERACIONAL.md`)
- ✅ Scripts `resume-pipeline.sh` e `trigger-pipeline.sh` ampliados
- ✅ Expansão massiva de testes (4 novos arquivos de cobertura dedicada)

**Arquivos impactados**: 35 arquivos · +4.850 / -643 linhas  
**Testes**: 445 passed · 513 warnings

---

### PR #14 — Melhorias de Qualidade de Código
**Branch**: `pr-b9e5ad1-086f9c9`  
**Resumo**: Aderência a PEPs (docstrings, type hints, imports explícitos), enriquecimento do payload Keycloak, redução de complexidade cognitiva

**Principais mudanças**:
- ✅ Docstrings e type hints adicionados em todos os módulos
- ✅ Enriquecimento do payload Keycloak com `cod_escola` e `cod_dre`
- ✅ +6 LEFT JOINs na extração SE1426 (cargo, funcao, lotacao_servidor, cod_dre)
- ✅ Redução de Complexidade Cognitiva em `upsert_user_to_keycloak` (S3776)
- ✅ `_build_email()` por tipo de usuário
- ✅ Extração de `_create_or_update_kc_user()` para reduzir complexidade
- ✅ Fix: `MatchType` retorna `str()` antes de persistir

**Arquivos impactados**: 35 arquivos · +398 / -114 linhas  
**Testes**: 280 passed · cobertura 88%

---

## Troubleshooting

### Problema: API não inicia ou fica unhealthy

**Solução**:
```bash
# Verificar logs
docker logs local-etl-api

# Verificar banco de dados
docker exec -it local-etl-postgres psql -U etl -d etl_db -c '\dt'

# Resetar ambiente
./scripts/reset-etl-local.sh --yes
./scripts/start-etl-local.sh
```

### Problema: Worker não processa tasks

**Solução**:
```bash
# Verificar logs do worker
docker logs local-etl-worker

# Verificar se KeyDB está respondendo
docker exec -it local-etl-keydb redis-cli ping

# Reiniciar worker
docker restart local-etl-worker
```

### Problema: Testes falhando

**Solução**:
```bash
# Limpar cache do pytest
rm -rf .pytest_cache

# Limpar banco de teste
rm -f db.sqlite3

# Executar testes com verbose
python -m pytest tests/ -vv

# Executar apenas um teste que está falhando
python -m pytest tests/test_core_tasks.py::TestExtractSE1426::test_extract_success -vv
```

### Problema: Cobertura abaixo de 80%

**Solução**:
```bash
# Ver relatório detalhado com linhas não cobertas
python -m pytest tests/ --cov=core --cov=staging --cov=extract --cov-report=term-missing

# Ver relatório HTML para análise visual
./scripts/coverage.sh
```

### Problema: Porta 8001 já está em uso

**Solução**:
```bash
# Verificar o que está usando a porta
sudo lsof -i :8001

# Parar o processo ou mudar a porta no docker-compose.local.yml
# Ou parar outro ETL rodando
docker stop local-etl-api
```

### Problema: Erro de conexão com SQL Server legado

**Solução**:
```bash
# Verificar se está na rede interna 10.49.x.x
ping <SE1426_DB_SERVER>

# Verificar credenciais no .env.local
nano SME-Identidade-ETL/.env.local

# Testar conexão manualmente
docker exec -it local-etl-api python manage.py shell
>>> from extract.tasks import extract_se1426
>>> # teste de conexão aqui
```

---

## Estrutura do Projeto

```
SME-Identidade-ETL/
├── core/                   # Pipeline, orchestração, Keycloak, Token-MS
│   ├── authentication.py   # Autenticação interna (X-Internal-Token)
│   ├── keycloak_client.py  # Cliente Keycloak Admin
│   ├── token_ms_client.py  # Cliente Token-MS
│   ├── models.py           # ETLExecution, ETLStepLog, UpsertControl
│   ├── tasks.py            # Tasks Celery (7 steps do pipeline)
│   ├── service.py          # KeycloakUpsertService
│   └── views.py            # API endpoints DRF
├── extract/                # Extração de fontes legadas
│   └── tasks.py            # extract_se1426, extract_eol_db, extract_coresso
├── staging/                # Transform, dedup, staging models
│   ├── models.py           # Servidor, Aluno, Sistema, Perfil staging
│   ├── tasks.py            # transform, crossref_dedup
│   └── utils.py            # normalize_cpf, validate_cpf, build_dedup_key
├── etl_ms/                 # Configurações Django
│   ├── settings.py         # Django settings
│   ├── celery.py           # Configuração Celery
│   └── urls.py             # URL routing
├── tests/                  # 445 testes unitários + integração
├── scripts/                # Scripts operacionais
│   ├── start-etl-local.sh
│   ├── reset-etl-local.sh
│   ├── trigger-pipeline.sh
│   ├── resume-pipeline.sh
│   ├── coverage.sh
│   └── clean-keycloak-realms.sh
├── docs/                   # Documentação
│   └── OPERACIONAL.md
├── docker-compose.local.yml
├── pytest.ini
├── requirements.txt
└── README.md
```

---

## 📚 Documentação Relacionada

- **[README.md](README.md)** — Documentação principal do projeto
- **[scripts/README.md](scripts/README.md)** — Documentação detalhada dos scripts operacionais
- **[Swagger UI Local](http://localhost:8001/api/docs/)** — API interativa

**Repositório**: [github.com/prefeiturasp/SME-Identidade-ETL](https://github.com/prefeiturasp/SME-Identidade-ETL)

---

**Última atualização**: Junho 2026  
**Mantido por**: Equipe SME-Identidade
