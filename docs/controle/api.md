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

## Criação Manual de Usuário

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/api/v1/etl/usuario/criar/` | Cria/atualiza um usuário a partir de dados diretos |

Diferente de `usuario/sincronizar/` e `usuario/conceder-acesso/`, **não
depende do usuário já existir no CoreSSO** — os dados vêm da própria
requisição. Usado para usuários que ainda não estão em nenhuma fonte
legada (ex.: parceiro externo, cadastro manual via API).

O registro é materializado em staging com `fonte="api_manual"` (rastreável
em `ControleProvisionamento.sistema_origem`) e provisionado pelo mesmo
caminho idempotente do pipeline (`provisionar_usuario_kc`) — upsert por
CPF/RF, hash de conteúdo, sem duplicação de lógica.

`sistema`/`roles` são **opcionais**: quando informados juntos, o acesso é
concedido na mesma chamada, reaproveitando o núcleo de
`usuario/conceder-acesso/` (`_conceder_roles_sistema_kc`) — evita uma
segunda requisição só para permissionar um usuário recém-criado.

```json
{
  "nome": "Fulano de Tal",
  "cpf": "12345678900",
  "email": "fulano@externo.com",
  "tipo_usuario": "terceiro",
  "sistema": 1008,
  "roles": ["COTIC"],
  "realm": "sme-apps"
}
```

Campos:

| Campo | Obrigatório | Descrição |
|---|---|---|
| `nome` | Sim | Nome do usuário |
| `cpf` / `rf` | Ao menos um | Identificador — `rf` só é persistido para `tipo_usuario="servidor"` |
| `email` | Não | E-mail do usuário |
| `tipo_usuario` | Não (`terceiro`) | `servidor`, `aluno` ou `terceiro` — define o model de staging usado |
| `sistema` | Só com `roles` | `coresso_sis_id` do sistema a conceder acesso |
| `roles` | Só com `sistema` | Nomes dos roles/perfis a conceder no sistema |
| `realm` | Não | Realm Keycloak de destino |

**Retorno (sem `sistema`/`roles`):**

```json
{
  "acao": "criado",
  "kc_user_id": "307506e4-...",
  "hash_conteudo": "cf931569..."
}
```

**Retorno (com `sistema`/`roles`):**

```json
{
  "acao": "criado",
  "kc_user_id": "6dbda0a5-...",
  "hash_conteudo": "42e9e153...",
  "sistema": "Auto Serviço",
  "client_id": "auto-servico-qa",
  "roles_atribuidos": ["COTIC"],
  "roles_nao_encontrados": [],
  "erros": 0
}
```

Se `sistema`/`roles` vierem desalinhados (um informado sem o outro), ou se o
`sistema` informado não existir/não tiver client no Keycloak, a requisição é
rejeitada com `400` **antes de qualquer efeito colateral** — nada é
materializado em staging nem provisionado no Keycloak. Se o usuário for
criado mas a concessão de acesso falhar por erro de comunicação com o
Keycloak (não por sistema inválido, já descartado antes), a resposta é `502`
incluindo o `kc_user_id` já criado (o usuário não é desfeito).

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

Se o `identificador` não for encontrado no CoreSSO, a resposta é `204 No
Content` (sem corpo) — não `404`, para não ser mascarado por proxies/WAF que
interceptam respostas 404 com uma página de erro genérica.

**Token-MS**: além do Keycloak, esta rota também atualiza a projeção de
perfis e permissões no token-ms (`PUT /perfis/{kc_user_id}/`), a partir dos
mesmos dados já buscados no CoreSSO — ver
[Carga de atributos complementares](../pipeline/token_ms.md). A sincronização
é *best-effort*: uma falha de comunicação com o token-ms é registrada em log,
mas não altera o status HTTP nem o corpo da resposta, já que o Keycloak
(etapa crítica) já foi atualizado com sucesso.

---

## Concessão de Acesso a Sistema

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/api/v1/etl/usuario/conceder-acesso/` | Concede acesso a um sistema e roles específicos |

Busca o usuário no CoreSSO, cria/atualiza no Keycloak e atribui os client
roles informados — independentemente dos vínculos reais no CoreSSO
(concessão manual, fora do fluxo de reconciliação automática).

```json
{
  "identificador": "6913261",
  "sistema": 1008,
  "roles": ["COTIC"],
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
  "sistema": "Auto Serviço",
  "client_id": "auto-servico-qa",
  "roles_atribuidos": ["COTIC"],
  "roles_nao_encontrados": [],
  "erros": 0
}
```

O núcleo de resolução/atribuição de roles (`_conceder_roles_sistema_kc`)
é compartilhado com `usuario/criar/` — ver
[Provisionamento Keycloak](../pipeline/keycloak.md).

Se o `identificador` não for encontrado no CoreSSO, ou o `sistema` informado
não existir/não tiver client no Keycloak, a resposta é `204 No Content`
(sem corpo) — mesmo motivo do endpoint de sincronização (evitar
interceptação de proxies/WAF em respostas de erro).

**Token-MS**: quando o usuário existe no CoreSSO, esta rota também atualiza
perfis/permissões no token-ms na mesma chamada, mesma lógica *best-effort*
de `usuario/sincronizar/`. No fallback sem CoreSSO
(`_conceder_acesso_sem_coresso`, usuário criado via `usuario/criar/` sem
vínculo prévio) não há grupos para montar um payload de perfil — o token-ms
não é chamado nesse caminho.

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
| `GET` | `/api/v1/etl/identidades/consultar/` | Busca identidade direto no Keycloak por CPF/RF/e-mail |
| `GET` | `/api/v1/etl/checkpoints/` | Checkpoints de retomada |
| `GET` | `/api/v1/etl/tentativas/` | Rastreio de tentativas |

### Consulta de identidade

`identidades/consultar/` busca a conta **diretamente no Keycloak** (não
mais no histórico local `ControleProvisionamento`) — reflete o estado
real, independentemente de qual caminho de upsert criou o usuário
(pipeline, `usuario/criar/`, `usuario/sincronizar/` ou
`usuario/conceder-acesso/`). Por gerar uma chamada real ao Keycloak a cada
consulta, **exige `AutenticacaoApiKey`** (deixou de ser público).

```
GET /api/v1/etl/identidades/consultar/?rf=7376065
GET /api/v1/etl/identidades/consultar/?cpf=123.456.789-01
GET /api/v1/etl/identidades/consultar/?email=fulano@sme.sp.gov.br&realm=COTIC
```

**Retorno:**

```json
[
  {
    "kc_user_id": "5c29cc47-...",
    "username": "7376065",
    "nome": "MONICA CARVALHO TANG",
    "email": "monica.tang@sme.prefeitura.sp.gov.br",
    "ativo": true,
    "cpf": "26930618810",
    "rf": "7376065",
    "kc_url": "https://kc/.../users/.../settings"
  }
]
```

Retorna uma lista (pode haver mais de uma conta se existirem duplicatas
ainda não mescladas no Keycloak) ou `[]` se não encontrar. `400` se
nenhum identificador for informado; `502` se o Keycloak estiver
inacessível.

---

## Dashboard e Kanban (HTML)

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/dashboard/` | Dashboard de execuções |
| `GET` | `/dashboard/kanban/` | Kanban de etapas |
