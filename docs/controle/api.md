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

---

## Etapas de uma execução

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/execucoes/{id}/etapas/` | Lista `LogEtapaETL` da execução |
| `GET` | `/api/v1/etl/tentativas/` | Lista `RastreioTentativa` (filtra por `id_execucao`) |

---

## Watermark

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/watermark/` | Lista marcas d'água por fonte |
| `POST` | `/api/v1/etl/watermark/{fonte}/resetar/` | Zera watermark da fonte (força extração completa) |

---

## Controle de provisionamento

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/controle/` | Lista `ControleProvisionamento` |
| `GET` | `/api/v1/etl/consulta-identidade/` | Busca identidade por CPF/RF/matrícula |

---

## Checkpoints

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/checkpoints/` | Lista checkpoints de retomada |

---

## Sistemas e Perfis CoreSSO

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/api/v1/etl/sistemas/` | Lista `SistemaStaging` |
| `POST` | `/api/v1/etl/sistemas/extrair/` | Dispara extração de sistemas CoreSSO |
| `POST` | `/api/v1/etl/sistemas/provisionar/` | Provisiona sistemas como clients Keycloak |
| `GET` | `/api/v1/etl/perfis/` | Lista `PerfilCoressoStaging` |
| `POST` | `/api/v1/etl/perfis/extrair/` | Dispara extração de perfis CoreSSO |
| `POST` | `/api/v1/etl/perfis/provisionar/` | Provisiona perfis como client roles Keycloak |

---

## Dashboard e Kanban (HTML)

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/etl/dashboard/` | Dashboard de execuções em andamento |
| `GET` | `/etl/kanban/` | Kanban de etapas por execução |
| `POST` | `/etl/dashboard/disparar/` | Disparo rápido via formulário HTML |
