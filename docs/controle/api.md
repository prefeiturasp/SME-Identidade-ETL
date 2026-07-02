# API REST de Controle

Definida em `apps/controle_etl/views.py`. Documentação interativa disponível
em `/api/v1/docs/` (Swagger) e `/api/v1/redoc/` (ReDoc).

Autenticação via header `X-API-Key` (configurado em `API_KEY` e `API_KEY_HEADER`).

---

## Execuções ETL

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/execucoes/` | Lista execuções (filtra por `situacao`, `fonte`) |
| `POST` | `/api/v1/etl/execucoes/` | Dispara nova execução do pipeline |
| `GET` | `/api/v1/etl/execucoes/{id}/` | Detalhe de uma execução |
| `POST` | `/api/v1/etl/execucoes/{id}/cancelar/` | Cancela execução pendente/executando |

**Payload de criação:**

```json
{
  "fonte": "todos",
  "realm_destino": "sme-apps"
}
```

Fontes válidas: `todos`, `se1426`, `coresso`, `eol_alunos`.

---

## Watermark

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/watermark/` | Lista marcas d'água por fonte |
| `POST` | `/api/v1/etl/watermark/{fonte}/resetar/` | Zera watermark (força extração completa) |

---

## Sistemas e Perfis CoreSSO

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/sistemas/` | Lista `SistemaStaging` |
| `POST` | `/api/v1/etl/sistemas/extrair/` | Extrai sistemas do CoreSSO |
| `POST` | `/api/v1/etl/sistemas/provisionar/` | Provisiona como clients no Keycloak |
| `GET` | `/api/v1/etl/perfis/` | Lista `PerfilCoressoStaging` |
| `POST` | `/api/v1/etl/perfis/extrair/` | Extrai perfis/grupos do CoreSSO |
| `POST` | `/api/v1/etl/perfis/provisionar/` | Provisiona como client roles no Keycloak |

**Filtros disponíveis (body JSON):**

- `sistemas/provisionar/`: `coresso_sis_id`, `sigla`, `realm`
- `perfis/provisionar/`: `coresso_sis_id`, `coresso_gru_id`, `realm`

---

## Vínculos Usuário↔Grupo

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/vinculos/` | Resumo de perfis por sistema |
| `POST` | `/api/v1/etl/vinculos/extrair/` | Extrai vínculos do CoreSSO |
| `POST` | `/api/v1/etl/vinculos/provisionar/` | Atribui client roles no Keycloak |

**Filtros (body JSON):**

```json
{
  "coresso_sis_id": 1008,
  "coresso_gru_id": "81E1E074-...",
  "realm": "sme-apps"
}
```

---

## Sincronização Individual de Usuário

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/api/v1/etl/usuario/sincronizar/` | Sincroniza um usuário no Keycloak |

Busca o usuário no CoreSSO por RF, CPF ou email, cria/atualiza no Keycloak
e atribui todos os client roles dos sistemas associados.

```json
{
  "identificador": "6913261",
  "realm": "sme-apps"
}
```

**Retorno:**

```json
{
  "acao": "atualizado",
  "kc_user_id": "b431df2f-...",
  "kc_url": "https://kc/.../users/.../settings",
  "username": "6913261",
  "nome": "ANGELA REGINA SAMPAIO NUNES",
  "roles_atribuidos": 15,
  "roles_erros": 0,
  "sistemas": [
    {"sistema": "Auto Serviço", "client_id": "auto-servico-qa", "roles": ["ASCOM", "CODAE", "GIPE"]},
    {"sistema": "SGP", "client_id": "sgp-qa", "roles": ["Admin"]}
  ]
}
```

---

## Pipeline Completo por Sistema

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/api/v1/etl/pipeline-sistema/` | Pipeline completo para um sistema |

Executa: extrair sistemas → provisionar client → extrair perfis → provisionar
roles → atribuir vínculos — tudo para um sistema específico.

```json
{
  "coresso_sis_id": 1008,
  "coresso_gru_id": "...",
  "realm": "sme-apps",
  "forcar_atualizacao": true
}
```

---

## Monitoramento e Saúde

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/monitoramento/resumo/` | Última execução por fonte (público) |
| `GET` | `/api/v1/etl/health/` | Health check (público) |
| `GET` | `/api/v1/etl/estatisticas/` | Estatísticas agregadas |
| `GET` | `/api/v1/etl/provisionamento/` | Registros de idempotência |
| `GET` | `/api/v1/etl/identidades/consultar/` | Busca identidade por CPF/RF |
| `GET` | `/api/v1/etl/checkpoints/` | Checkpoints de retomada |
| `GET` | `/api/v1/etl/tentativas/` | Rastreio de tentativas |

---

## Dashboard e Kanban (HTML)

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/dashboard/` | Dashboard de execuções |
| `GET` | `/dashboard/kanban/` | Kanban de etapas |
