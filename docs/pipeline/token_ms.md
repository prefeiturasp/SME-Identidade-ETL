# Carga no token-ms

## Objetivo

Enviar atributos complementares de identidade ao microsserviço `token-ms`
após o provisionamento no Keycloak.

---

## Task

`task_carregar_atributos_token` em `apps/controle_etl/tasks.py`.

Processa todos os registros com `situacao__in=["pronto", "carregado"]`
do staging, construindo payloads via `construir_payload_token_ms` e enviando
em lotes via `enviar_todos`.

---

## Cliente HTTP

`apps/controle_etl/cliente_token_ms.py` — cliente `httpx` com retry interno.

| Função | Responsabilidade |
|---|---|
| `enviar_lote(payloads, id_execucao)` | POST de um lote de atributos para `TOKEN_MS_URL` |
| `enviar_todos(payloads, id_execucao)` | Itera o iterável de payloads em lotes de `TOKEN_MS_BATCH_SIZE` (padrão 200) |

---

## Payload

Construído por `construir_payload_token_ms(usuario)` em `orquestrador_kc.py`:

| Campo | Origem |
|---|---|
| `login` | username resolvido (RF / CPF / matrícula) |
| `nome` | `usuario.nome` |
| `email` | `usuario.email` |
| `cpf` | `usuario.cpf` |
| `rf` | `usuario.rf` (servidores) |
| `tipo` | tipo inferido pelo modelo staging |

---

## Variáveis de ambiente relevantes

| Variável | Descrição |
|---|---|
| `TOKEN_MS_URL` | URL base do token-ms |
| `TOKEN_MS_API_KEY` | Chave de autenticação |
| `TOKEN_MS_BATCH_SIZE` | Tamanho do lote (padrão 200) |
| `TOKEN_MS_TIMEOUT` | Timeout HTTP em segundos (padrão 30) |
