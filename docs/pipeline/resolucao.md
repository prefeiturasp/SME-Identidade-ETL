# Resolução de Identidade

## Objetivo

Consolidar os registros de staging em identidades únicas, eliminando
duplicatas entre fontes e dentro da mesma fonte, antes do provisionamento.

---

## Task

`task_identidade_resolver_identidade` em `apps/controle_etl/tasks.py`.

Chama em sequência:

1. `transformar_staging` — normaliza e valida cada registro
2. `deduplicar_identidades` — elege vencedores por CPF e por RF

---

## Regra de deduplicação

Definida em `apps/staging/tasks.py` (`deduplicar_identidades`):

**Prioridade de fonte (menor = mais confiável):**

| Fonte | Prioridade |
|---|---|
| `se1426` | 1 |
| `eol_db` | 2 |
| `coresso` | 3 |

Para registros com o mesmo CPF, vence o de menor prioridade (fonte mais
confiável). Em caso de empate, vence o de maior `id`.

O mesmo critério se aplica para deduplicação por RF.

Registros perdedores têm `situacao` alterada para `duplicado` e não são
enviados ao Keycloak.

---

## Regra de username no Keycloak

Definida em `apps/controle_etl/orquestrador_kc._resolver_username`:

```
RF  →  CPF  →  matricula  →  "{fonte}-{id}"
```

Servidores com RF preferem RF; alunos e terceiros preferem CPF.
Se nenhum identificador estiver disponível, o fallback é `"{fonte}-{id}"`.

---

## Crossref entre execuções

`ControleProvisionamento` em `apps/controle_etl/models.py` registra a última
situação de cada entidade por `(tipo_entidade, sistema_origem, id_origem)`.
Isso permite que o provisionamento detecte atualizações e ignore registros
sem alteração de conteúdo (via hash SHA-256).
