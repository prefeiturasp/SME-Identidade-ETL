# Carga no token-ms

## Objetivo

Enviar atributos complementares de identidade (cargo, lotação, DRE/UE,
vínculos) ao microsserviço `token-ms` após o provisionamento no Keycloak,
para compor claims de autorização usadas na geração do token JWT.

---

## Carga individual (caminho principal)

`task_carregar_atributo_token_individual` em `apps/controle_etl/tasks.py`,
roteada para a fila `etl_carga_token_ms` — desacoplada da fila do
Keycloak (`etl_carga_keycloak`).

Disparada **fire-and-forget** (`apply_async`) de dentro de
`_provisionar_lote_kc` assim que **um único usuário** é confirmado no
Keycloak — não espera o restante do lote terminar. A `chain` do
pipeline principal (`task_identidade_executar_pipeline`) não inclui
essa carga: `ExecucaoETL` fecha (`sucesso`/`parcial`) ao final do
Keycloak, e o progresso do token-ms por usuário é rastreável via
`hash_token_ms` em `ControleProvisionamento` (`token_ms_confirmado`
no serializer, ver [API de Controle](../controle/api.md)), não pela
`situacao` da execução.

Antes de enviar, recalcula `hash_token_ms` do payload atual e compara
com o valor persistido — se já bate, é *no-op* idempotente. Descarta
(loga, não reenvia) registros sem identificador via
`payload_tem_identificador`. Em sucesso, sincroniza o perfil
(`enviar_perfil`) e envia o lote de 1 (`enviar_lote`), gravando o novo
`hash_token_ms`. Retry (`max_retries=5`, backoff igual às demais
tasks) usa `_registrar_tentativa`/`_calcular_atraso` compartilhados.

**Limitação conhecida**: se um retry tardio ocorrer depois que o
staging da execução original já foi limpo
(`task_identidade_limpar_staging`, disparada por `task_sync_rec_etl`
com `countdown=600` — alinhado à janela máxima de retry), a task falha
por não encontrar o registro. A janela de retry (~10min no pior caso)
é sempre menor que a retenção do staging, então isso só ocorre em
cenários anômalos; uma falha definitiva nesse ponto só se resolve numa
próxima execução completa do pipeline.

## Carga em lote (fallback / reprocessamento manual)

`task_carregar_atributos_token` — mesma fila `etl_carga_token_ms`, fora
do caminho síncrono do pipeline. Processa todos os registros com
`situacao__in=["pronto", "carregado"]` do staging
(`UsuarioServidorStaging`, `UsuarioAlunoStaging`,
`UsuarioTerceiroStaging`) filtrados pelo `id_execucao`, checando
`hash_token_ms` por registro antes de enviar (mesma idempotência da
carga individual, em vez de reenviar sempre) e agrupando via
`enviar_todos`. Útil para reprocessar uma execução específica
manualmente ou como malha de segurança adicional.

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
| `cargo`, `funcao` | staging de servidor | usado por outros tipos de usuário — para servidor, ver `vinculos` |
| `unidade`, `unidade_codigo` | staging (lotação) | usado por outros tipos de usuário — para servidor, ver `vinculos` |
| `dre`, `ue` | staging | populado para alunos (EOL_DB) — para servidor, ver `vinculos` |
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

## Vínculos funcionais de servidor (`vinculos`)

Um servidor pode ter múltiplos vínculos funcionais vigentes e
independentes ao mesmo tempo — cargo base, cargo sobreposto/comissionado
e função/atividade, cada um com seu próprio cargo e sua própria
unidade/DRE, inclusive mais de um cargo base simultâneo. Por isso esse
dado **não** é um conjunto de campos escalares no payload — é uma lista
aninhada:

```json
{
  "rf": "1234567",
  ...,
  "vinculos": [
    {
      "tipo_vinculo": "cargo_base",
      "codigo_vinculo_origem": "998877",
      "cargo_codigo": "101",
      "cargo_nome": "PROFESSOR",
      "unidade_codigo": "123456",
      "unidade_nome": "EMEF FULANO DE TAL",
      "dre_codigo": "11",
      "situacao": "ativo",
      "data_inicio": null,
      "vigente": true
    }
  ]
}
```

Extraído em `_extrair_vinculos_servidor_se1426` (`apps/extracao/tasks.py`)
a partir de `v_servidor_cotic` + `v_cargo_base_cotic` (cargo base do
servidor, join por `cd_servidor`) + `lotacao_servidor` (unidade do cargo
base), `cargo_sobreposto_servidor` (cargo sobreposto) e
`funcao_atividade_cargo_servidor` (função/atividade). Cada tipo é
extraído como vínculo independente, sem `COALESCE`/fallback sobre o
cargo base — nenhum tipo prevalece sobre outro. Todos resolvem a DRE
via `v_cadastro_unidade_educacao.cd_unidade_administrativa_referencia` a
partir da UE — a DRE nunca é coluna direta do vínculo. Só vínculos
vigentes (`dt_fim_nomeacao`/`dt_cancelamento`/`dt_fim_cargo_sobreposto`/
`dt_fim_funcao_atividade` nulos ou futuros, conforme cada tabela) são
extraídos neste momento; histórico completo fica fora de escopo.
Persistido em staging como `VinculoServidorStaging` (`apps.staging.models`,
FK para `UsuarioServidorStaging`), incluído no payload por
`_vinculos_payload`/`_payload_token_ms_hash`
(`apps/controle_etl/orquestrador_kc.py`) e coberto automaticamente pelo
hash de idempotência — mudança em qualquer vínculo já dispara reenvio.

Os campos escalares `cargo`, `funcao`, `unidade`, `unidade_codigo`,
`dre` e `ue` do payload permanecem sempre `null` para servidor (usados
hoje só por outros tipos de usuário, ex. aluno) — não confundir com
`vinculos`, que é a fonte real desse dado para servidor.

---

## Sincronização de perfis (`PUT /perfis/{usuario_id}/`)

Além do push-batch de atributos complementares, o mesmo fluxo também
sincroniza, por usuário, a projeção de perfis usada para compor o JWT
(`apps/perfil` do token-ms) — extraída dos grupos CoreSSO aos quais o
usuário está vinculado (mesma fonte já usada por
`sincronizar_usuario_kc`).

### Carga individual

`task_carregar_atributo_token_individual` já recebe o resultado do
Keycloak com `kc_user_id` resolvido (não precisa buscá-lo de novo) —
monta o payload via `construir_payload_perfil_token_ms` e envia com
`enviar_perfil(kc_user_id, payload)` antes do push-batch de atributos.

### Carga em lote (fallback)

Para cada usuário do staging da execução, `_sincronizar_perfis_execucao`
(`apps/controle_etl/tasks.py`), usada pela task de lote:

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
`LogEtapaETL.metadados` da etapa `CARREGAR_TOKEN` — só preenchida
quando a task de lote é executada (a carga individual não abre etapa
própria, seu progresso é rastreado via `ControleProvisionamento`).

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
só resolve `dados`/`kc_user_id` a partir do registro de staging e
repassa para `montar_payload_perfil`.

Mesma filosofia *best-effort* do pipeline: falha ao chamar o
token-ms é só logada (`logger.exception`), sem alterar o status HTTP
nem o corpo da resposta — o Keycloak já foi atualizado com sucesso
antes dessa chamada.

`conceder_acesso` só chama o token-ms quando `dados` (CoreSSO)
existe. No fallback `_conceder_acesso_sem_coresso` (usuário
materializado via `usuario/criar/`, sem vínculo prévio no CoreSSO)
não há grupos para montar `perfis` — o token-ms não é chamado nesse
caminho.

### `permissoes` — fonte no CoreSSO (`SYS_GrupoPermissao`/`SYS_Modulo`)

O CoreSSO tem uma fonte real de permissões: `SYS_GrupoPermissao`
(matriz grupo×sistema×módulo) e `SYS_Modulo` (nome do módulo por
sistema). Cada linha traz quatro flags booleanas independentes —
`grp_consultar`, `grp_inserir`, `grp_alterar`, `grp_excluir`.

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
(`apps/perfil/models.py`) — a granularidade do model é sistema+módulo,
refletindo a granularidade real da fonte. O Gateway-MS
(`DadosAcessoView`) consome esse formato.

**Volume por usuário**: um usuário com poucos grupos pode ter
dezenas a centenas de módulos permissionados. Acompanhar se isso se
torna um problema de tamanho de payload para usuários com muitos
grupos (ex.: perfis administrativos amplos).
