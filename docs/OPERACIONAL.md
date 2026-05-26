# Guia Operacional — ETL SME-Identidade

> **Swagger UI (local):** `http://localhost:8001/docs/`  
> Todos os cenários deste guia são executados diretamente pelo Swagger. Nenhum terminal é necessário.

---

## � 0. Autenticar no Swagger antes de qualquer operação

Todos os endpoints exigem o header `X-Internal-Token`.

1. Abra `http://localhost:8001/docs/`
2. Clique no botão **Authorize** 🔒 (canto superior direito)
3. No campo **InternalToken (apiKey)**, informe o valor do token:
   - Local: `dev-etl-token`
   - QA/Produção: valor do `.env` → `ETL_INTERNAL_TOKEN`
4. Clique em **Authorize** → **Close**

> A partir daqui o Swagger envia `X-Internal-Token` automaticamente em todas as requisições.

---

## 📊 Diagramas e Documentação do Fluxo

| Documento | O que contém | Como acessar |
|---|---|---|
| **etl-swimlane.html** | Diagrama visual completo do pipeline (swimlane por raia) para apresentação| Abrir no navegador: `docs/etl-swimlane.html` |
| **PIPELINE_FLOW.md** | Descrição técnica de cada step (0–8), regras de transform, dedup, idempotência | `docs/PIPELINE_FLOW.md` |
| **STEP_ENDPOINTS.md** | Referência completa de cada endpoint individual | `docs/STEP_ENDPOINTS.md` |

---

## 🔍 1. Verificar se o Worker Está Trabalhando

### 1a. Health check — serviço está respondendo?

No Swagger (`http://localhost:8001/docs/`):

1. Expanda **`GET /api/health/`**
2. Clique em **Try it out** → **Execute**
3. Resposta `200` com os componentes `database`, `cache`, `celery` — todos devem estar `"working"`

> O health check não exige autenticação.

### 1b. Listar execuções e ver o status geral

1. Expanda **`GET /api/etl/executions/`**
2. Clique em **Try it out** → **Execute**
3. A resposta lista as execuções com `id`, `status` e `created_at`
4. Copie o `id` da execução que deseja monitorar

### 1c. Ver o estado atual de uma execução (steps + contadores)

1. Expanda **`GET /api/etl/executions/{id}/`**
2. Clique em **Try it out**
3. Preencha o campo `id` com o UUID da execução
4. Clique em **Execute**
5. Na resposta, observe o array `steps` com `step_name`, `status`, `records_out` e `records_error`

### Interpretação do status

| `status` da execução | Step com `status=running` | `records_out` | Conclusão |
|---|---|---|---|
| `running` | Sim | > 0 | ✅ Trabalhando normalmente |
| `running` | Sim | `0` e `updated_at` parado | ❌ **Travado** — provável SIGKILL (OOM) |
| `success` | Nenhum | — | ✅ Concluído com sucesso |
| `failed` | Nenhum | — | ❌ Falhou — ver step com `status=failed` |

**Como confirmar se está travado:** execute o `GET /api/etl/executions/{id}/` duas vezes com ~1 minuto de intervalo. Se o campo `updated_at` não mudou e o step ainda aparece como `running` com `records_out=0` → a task foi morta.

---

## 🔁 2. Retomar uma Etapa Travada (SIGKILL / OOM)

Quando um step aparece com `status=running` mas `updated_at` está parado, o processo foi morto pelo OOM Killer. Use o endpoint do step correspondente com `"force": true`.

### Identificar qual step travou

1. Execute **`GET /api/etl/executions/{id}/`** no Swagger
2. No array `steps`, encontre o item com `"status": "running"` — esse é o step travado

### Reexecutar o step travado

| Step travado | Endpoint no Swagger | Body |
|---|---|---|
| `sync_catalogo` (Step 0) | `POST /api/etl/executions/{id}/run-sync-catalogo/` | `{"force": true}` |
| `extract_*` (Steps 1–2) | `POST /api/etl/executions/{id}/run-extract/` | `{"source": "all", "force": true}` |
| `staging` / `transform` (Step 3) | `POST /api/etl/executions/{id}/run-transform/` | `{"force": true}` |
| `crossref_dedup` (Step 4) | `POST /api/etl/executions/{id}/run-crossref/` | `{"force": true}` |
| `decide_target` (Step 5) | `POST /api/etl/executions/{id}/run-decide/` | `{"force": true}` |
| `load_keycloak` (Step 6) | `POST /api/etl/executions/{id}/reload_keycloak/` | `{"reset_loaded": true}` |
| `load_token_ms` (Step 7) | `POST /api/etl/executions/{id}/run-load-token/` | `{"force": true}` |
| `audit_etl` (Step 8) | `POST /api/etl/executions/{id}/run-audit/` | `{"force": true}` |

**Como usar no Swagger:**
1. Expanda o endpoint correspondente ao step travado
2. Clique em **Try it out**
3. Preencha `id` com o UUID da execução
4. Cole o body indicado na tabela acima
5. Clique em **Execute**

> O `"force": true` apaga o log do step antes de reexecutar, garantindo que ele não seja pulado por já estar marcado como `running`.

---

## ▶️ 3. Executar Cada Step Individualmente

> Use quando quiser rodar uma etapa específica sem disparar o pipeline completo.  
> Todos os endpoints ficam na seção **ETL Executions** do Swagger.

**Passos gerais para qualquer step:**
1. Expanda o endpoint desejado em `POST /api/etl/executions/{id}/<ação>/`
2. Clique em **Try it out**
3. Preencha o `id` com o UUID da execução
4. Ajuste o body conforme a tabela abaixo
5. Clique em **Execute**

### Opções por step

#### Step 0 — `run-sync-catalogo` — Sincronizar Sistemas e Perfis do CoreSSO
```json
{ "force": true }
```

#### Step 1+2 — `run-extract` — Extração das Fontes Legadas
```json
{ "source": "all", "force": true }
```
Opções do campo `source`:

| Valor | O que extrai |
|---|---|
| `all` | SE1426 + EOL_DB + EOL Alunos + CoreSSO |
| `se1426` | Somente servidores PMSP |
| `eol_db` | Somente lotações e UEs do EOL |
| `eol_alunos` | Somente alunos ativos com e-mail |
| `coresso` | Somente usuários do SSO legado |

#### Step 3 — `run-transform` — Validação CPF, Normalização RF e Nome
```json
{ "force": true }
```
> Registros com CPF inválido ficam com `status=error` — não chegam ao Keycloak.

#### Step 4 — `run-crossref` — Deduplicação por CPF
```json
{ "force": true }
```
> Cruza SE1426 + EOL + CoreSSO pelo CPF. Prioridade: SE1426 > EOL > CoreSSO.

#### Step 5 — `run-decide` — Hash / Idempotência
```json
{ "force": true }
```
> Compara hash SHA-256 com `UpsertControl`. Dados inalterados são marcados como `skip`.

#### Step 6 — `reload_keycloak` — Carga no Keycloak

Carga completa:
```json
{ "reset_loaded": true }
```

Piloto de 100 usuários:
```json
{ "max_records": 100, "reset_loaded": true }
```

Homologação com 1.000 usuários:
```json
{ "max_records": 1000, "reset_loaded": true }
```

#### Step 7 — `run-load-token` — Carga no Token-MS
```json
{ "force": true }
```

#### Step 8 — `run-audit` — Fechar e contabilizar a execução
```json
{ "force": true }
```

---

## 🎯 4. Carga Seletiva (por CPF, RF ou Lote)

Endpoint: **`POST /api/etl/sync-selective/`**

**Como usar no Swagger:**
1. Expanda **`POST /api/etl/sync-selective/`**
2. Clique em **Try it out**
3. Cole um dos bodies abaixo conforme o cenário
4. Clique em **Execute**

#### Verificar usuário por CPF (sem enviar ao Keycloak)
```json
{ "cpfs": ["123.456.789-09"], "load_keycloak": false }
```

#### Enviar 1 usuário por CPF ao Keycloak
```json
{ "cpfs": ["123.456.789-09"], "load_keycloak": true }
```

#### Enviar usuário por RF ao Keycloak
```json
{ "rfs": ["1234567"], "load_keycloak": true }
```

#### Validar os primeiros N do staging (sem enviar)
```json
{ "limit": 10, "load_keycloak": false }
```

---

## 📋 5. Consultar Métricas de Qualidade da Execução

Endpoint: **`GET /api/etl/executions/{id}/`**

**Como usar no Swagger:**
1. Expanda **`GET /api/etl/executions/{id}/`**
2. Clique em **Try it out** → preencha `id` → **Execute**
3. Na resposta, observe os campos:

| Campo na resposta | Significado |
|---|---|
| `status` | Estado geral: `running` / `success` / `failed` |
| `total_extracted` | Total extraído das fontes legadas |
| `total_ready` | Total após dedup e validação (pronto para KC) |
| `total_loaded` | Total efetivamente enviado ao Keycloak |
| `total_errors` | Registros com CPF inválido ou erro de carga |
| `total_skipped` | Registros ignorados por hash inalterado (idempotência) |
| `steps[].step_name` | Nome do step |
| `steps[].status` | `success` / `running` / `failed` |
| `steps[].records_out` | Registros gerados pelo step |
| `steps[].records_error` | Registros com erro neste step |

---

## ⚡ Referência Rápida — Endpoints no Swagger

```
GET  /api/etl/executions/                          → listar execuções
GET  /api/etl/executions/{id}/                     → detalhe + steps + contadores
POST /api/etl/executions/                          → disparar novo pipeline completo

POST /api/etl/executions/{id}/run-sync-catalogo/   → Step 0
POST /api/etl/executions/{id}/run-extract/         → Steps 1-2  (source: all|se1426|eol_db|eol_alunos|coresso)
POST /api/etl/executions/{id}/run-transform/       → Step 3
POST /api/etl/executions/{id}/run-crossref/        → Step 4
POST /api/etl/executions/{id}/run-decide/          → Step 5
POST /api/etl/executions/{id}/reload_keycloak/     → Step 6  (max_records, reset_loaded)
POST /api/etl/executions/{id}/run-load-token/      → Step 7
POST /api/etl/executions/{id}/run-audit/           → Step 8

POST /api/etl/sync-selective/                      → carga/consulta por CPF ou RF
GET  /api/etl/stats/                               → métricas globais do ETL
GET  /api/etl/sistemas/                            → sistemas CoreSSO sincronizados
GET  /api/etl/perfis/                              → perfis CoreSSO sincronizados
GET  /api/health/                                  → health check (sem autenticação)
```

---

## 🗺️ Ver o Diagrama Visual do Fluxo

Abra o arquivo `docs/etl-swimlane.html` diretamente no navegador.

O diagrama mostra:
- **6 raias**: Gatilho → ETL Worker → Fontes Legadas (SE1426/EOL/CoreSSO) → Staging → Keycloak → Token-MS
- **Cada step** com os dados que busca, o tratamento aplicado e o que produz
- **Garantias de qualidade**: validação CPF mod11, dedup por CPF, idempotência por hash, rastreabilidade
- **Exemplos de controle granular**: como carregar 100 ou 1.000 usuários em piloto

