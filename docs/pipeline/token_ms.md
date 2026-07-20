# Carga no token-ms

## Objetivo

Enviar atributos complementares de identidade (cargo, lotação, DRE/UE,
vínculos) ao microsserviço `token-ms` após o provisionamento no Keycloak,
para compor claims de autorização usadas na geração do token JWT.

---

## Task

`task_carregar_atributos_token` em `apps/controle_etl/tasks.py`.

Processa todos os registros com `situacao__in=["pronto", "carregado"]`
do staging (`UsuarioServidorStaging`, `UsuarioAlunoStaging`,
`UsuarioTerceiroStaging`) filtrados pelo `id_execucao` corrente,
construindo um payload por usuário via `construir_payload_token_ms`
e enviando em lotes via `enviar_todos`.

---

## Cliente HTTP

`apps/controle_etl/cliente_token_ms.py` — cliente `httpx` com retry
exponencial (base 1s, máximo 60s, até 5 tentativas) para erros
transitórios (`408, 425, 429, 500, 502, 503, 504` e falhas de
transporte/timeout).

| Função | Responsabilidade |
|---|---|
| `enviar_lote(usuarios, id_execucao)` | `POST {TOKEN_MS_URL}/api/v1/etl/push-batch` com um lote de atributos |
| `enviar_todos(usuarios, id_execucao, tamanho_lote=None)` | Itera o iterável de payloads em lotes de `TOKEN_MS_TAMANHO_LOTE` (padrão 500) |

**`TOKEN_MS_URL` deve incluir o prefixo de serviço do token-ms**
(ex.: `https://qa-identidade.sme.prefeitura.sp.gov.br/identidade-token`)
— o cliente concatena apenas `/api/v1/etl/push-batch` a este valor.

---

## Endpoint no token-ms

`POST {TOKEN_MS_URL}/api/v1/etl/push-batch`, implementado no
repositório `SME-Identidade-Token-Microsservico`
(`apps/atributos_complementares/`). Faz upsert idempotente por
identificador natural (RF, senão CPF, senão matrícula) — reenvios do
mesmo `id_execucao` ou de execuções diferentes atualizam o registro
existente, sem duplicar. O vínculo com a projeção de identidade
(`ProjecaoUsuario`) é oportunista: resolvido por RF/CPF quando existe,
sem bloquear o upsert quando ainda não existe.

Resposta: `{"processados": int, "criados": int, "atualizados": int}`.

---

## Payload

Construído por `construir_payload_token_ms(usuario)` em
`apps/controle_etl/orquestrador_kc.py`, um objeto por usuário dentro
de `{"id_execucao": "<uuid>", "usuarios": [...]}`:

| Campo | Origem | Observação |
|---|---|---|
| `rf` | `usuario.rf` | só `UsuarioServidorStaging` |
| `cpf` | `usuario.cpf` | comum a todos os tipos |
| `nome` | `usuario.nome` | comum |
| `email` | `usuario.email` | comum |
| `tipo_usuario` | inferido (`servidor`/`aluno`/`tipo_acesso`/`outro`) | |
| `cargo`, `funcao` | staging de servidor | **sempre `null` hoje** — ver limitações |
| `unidade`, `unidade_codigo` | staging (lotação) | **sempre `null` para servidor hoje** |
| `dre`, `ue` | staging | populado para alunos (EOL_DB); **`null` para servidor hoje** |
| `matricula` | staging | servidor/aluno/terceiro |
| `cod_escola`, `turma` | staging | só aluno |
| `tipo_acesso` | staging | só terceiro (CoreSSO) |
| `situacao`, `fonte`, `id_execucao` | staging | comuns |

Pelo menos um de `rf`, `cpf` ou `matricula` é obrigatório — o
token-ms rejeita (400) usuários sem nenhum identificador natural.

---

## Autenticação

O token-ms autentica via header `X-API-Key` comparado contra a
variável `API_KEY` (mesmo mecanismo usado pelo próprio ETL para
autenticar requisições que recebe). O cliente ETL envia esse header
quando `TOKEN_MS_API_KEY` está configurado.

---

## Variáveis de ambiente relevantes

| Variável | Descrição | Padrão |
|---|---|---|
| `TOKEN_MS_URL` | URL base do token-ms, incluindo prefixo `/identidade-token` | `https://token-ms:8000/identidade-token` |
| `TOKEN_MS_API_KEY` | Valor enviado no header de autenticação | — |
| `TOKEN_MS_API_KEY_HEADER` | Nome do header de autenticação | `X-API-Key` |
| `TOKEN_MS_TIMEOUT` | Timeout HTTP em segundos | `60` |
| `TOKEN_MS_TAMANHO_LOTE` | Tamanho do lote por requisição | `500` |

Em ambiente local, `TOKEN_MS_API_KEY`/`TOKEN_MS_API_KEY_HEADER` devem
ter o mesmo valor de `API_KEY`/`API_KEY_HEADER` configurado no `.env`
do `SME-Identidade-Token-Microsservico`.

---

## Limitações conhecidas

Os campos `cargo`, `funcao`, `unidade`, `unidade_codigo`, `dre` e `ue`
de **servidores** (fonte SE1426) são sempre enviados como `null`
hoje: a extração de SE1426 (`apps/extracao/tasks.py`) só lê
`rf, nome, cpf, situacao, email` — nenhuma view atualmente consultada
expõe cargo ou lotação de servidor. No sistema legado esses dados
vinham de uma API de fachada (EOL/SGP) que o projeto decidiu
deliberadamente não reintegrar, por perpetuar a dependência que o
projeto existe para eliminar. Popular esses campos depende de uma
investigação de schema em SE1426 (ou fonte irmã) com o time de dados,
ainda em aberto.

---

## Sincronização de perfis (`PUT /perfis/{usuario_id}/`)

Além do push-batch de atributos complementares, a mesma
`task_carregar_atributos_token` também sincroniza, por usuário, a
projeção de perfis usada para compor o JWT (`apps/perfil` do
token-ms) — extraída dos grupos CoreSSO aos quais o usuário está
vinculado (mesma fonte já usada por `sincronizar_usuario_kc`).

### Fluxo

Para cada usuário do staging da execução, `_sincronizar_perfis_execucao`
(`apps/controle_etl/tasks.py`):

1. Resolve o `kc_user_id` no Keycloak via
   `resolver_kc_user_id_de_usuario` (busca por username exato — RF,
   CPF ou matrícula, mesma prioridade de `_resolver_username`).
2. Monta o payload de projeção via
   `construir_payload_perfil_token_ms` (`orquestrador_kc.py`), que
   busca os vínculos usuário↔grupo no CoreSSO
   (`buscar_dados_usuario_coresso`) e converte cada grupo em um
   `PerfilUsuario` (`{"id": <uuid>, "nome": <gru_nome>, "ativo": true}`
   — a query de origem já filtra apenas vínculos ativos).
3. Envia via `enviar_perfil(kc_user_id, payload)`
   (`cliente_token_ms.py`) — `PUT {TOKEN_MS_URL}/api/v1/perfis/{kc_user_id}/`,
   mesmo retry/autenticação do push-batch.

Falha ao sincronizar o perfil de um usuário é contada e logada, mas
**não** interrompe os demais usuários nem afeta o resultado do
push-batch (responsabilidade já concluída antes desta etapa).
Métricas (`perfis_sincronizados`, `perfis_erros`) ficam em
`LogEtapaETL.metadados` da mesma etapa `CARREGAR_TOKEN`.

### Usuário sem identificador ou sem vínculo CoreSSO

Se o `kc_user_id` não for resolvido (usuário ainda não provisionado
no Keycloak) ou o usuário não tiver registro no CoreSSO (ex.: aluno
puro, fonte EOL_DB), a sincronização de perfil desse usuário é
pulada silenciosamente — não conta como erro.

### Fora do pipeline agendado: `usuario/sincronizar/` e `usuario/conceder-acesso/`

As rotas HTTP avulsas `POST usuario/sincronizar/` e
`POST usuario/conceder-acesso/` (`apps/controle_etl/views.py`) também
mantêm o token-ms atualizado, na mesma chamada em que atualizam o
Keycloak — não é preciso esperar a próxima execução do pipeline
agendado para que uma sincronização/concessão avulsa se reflita em
`permissoes` no token-ms.

Diferente do fluxo de staging, essas rotas **já têm** `dados` (de
`buscar_dados_usuario_coresso`) e `kc_user_id` (retornado por
`sincronizar_usuario_kc`/`conceder_acesso_kc`) em mãos antes de tocar
o token-ms — por isso usam `montar_payload_perfil(dados, ...)`
diretamente (`orquestrador_kc.py`), em vez de
`construir_payload_perfil_token_ms`, evitando uma segunda consulta
redundante ao CoreSSO. `construir_payload_perfil_token_ms` (staging)
passou a ser um wrapper fino sobre `montar_payload_perfil`.

Mesma filosofia *best-effort* do pipeline: falha ao chamar o
token-ms é só logada (`logger.exception`), sem alterar o status HTTP
nem o corpo da resposta — o Keycloak já foi atualizado com sucesso
antes dessa chamada.

`conceder_acesso` só chama o token-ms quando `dados` (CoreSSO)
existe. No fallback `_conceder_acesso_sem_coresso` (usuário
materializado via `usuario/criar/`, sem vínculo prévio no CoreSSO)
não há grupos para montar `perfis` — o token-ms não é chamado nesse
caminho.

### `permissoes` — fonte real confirmada (`SYS_GrupoPermissao`/`SYS_Modulo`)

Diferente do que se supunha inicialmente, o CoreSSO **tem** uma fonte
real de permissões, confirmada por varredura de schema
(`INFORMATION_SCHEMA`) contra o banco: `SYS_GrupoPermissao` (matriz
grupo×sistema×módulo, ~10 mil linhas em produção) e `SYS_Modulo`
(nome do módulo por sistema). Cada linha traz quatro flags booleanas
independentes — `grp_consultar`, `grp_inserir`, `grp_alterar`,
`grp_excluir`.

**Importante**: `mod_id` sozinho **não é chave única** — o mesmo
`mod_id` é reaproveitado em módulos de sistemas diferentes (até 19
sistemas para um único `mod_id` observados na amostra). A chave
natural é sempre o par `(sis_id, mod_id)`, confirmada pela própria
PK composta das tabelas no banco.

`buscar_permissoes_usuario_coresso(gru_ids)`
(`apps/extracao/tasks.py`) busca essas permissões para os grupos do
usuário (já obtidos por `buscar_dados_usuario_coresso`), filtrando
apenas módulos com **ao menos uma flag concedida** (a maioria das
combinações grupo×módulo vem com as quatro flags falsas e é
descartada). `construir_payload_perfil_token_ms`
(`orquestrador_kc.py`) deduplica por `(sistema_id, modulo_id)` quando
o mesmo módulo é concedido por mais de um grupo do usuário,
combinando as flags por OR lógico.

Formato de cada item de `permissoes`:
```json
{
  "sistema_id": 1, "sistema_nome": "CoreSSO",
  "modulo_id": 3, "modulo_nome": "Usuários",
  "consultar": true, "inserir": true, "alterar": false, "excluir": false
}
```

Modelado no token-ms como `ModuloPermissaoUsuario`
(`apps/perfil/models.py`) — substituiu o antigo `PermissaoUsuario`
(`{codigo, descricao}`), que nunca teve dado real e não representava
a granularidade verdadeira da fonte (sistema+módulo, não um código
plano). O Gateway-MS (`DadosAcessoView`) foi atualizado no mesmo
momento para consumir o novo formato.

**Volume por usuário**: um usuário com poucos grupos pode ter
dezenas a centenas de módulos permissionados (ex.: 229 módulos
observados para um usuário de teste com 3 grupos). Acompanhar se
isso se torna um problema de tamanho de payload para usuários com
muitos grupos (ex.: perfis administrativos amplos).
