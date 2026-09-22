# Modelos de Controle

Definidos em `apps/controle_etl/models.py`.

---

## ExecucaoETL

Registro central de cada disparo do pipeline.

| Campo | Tipo | Descrição |
|---|---|---|
| `id_execucao` | UUID (PK) | Identificador único da execução |
| `fonte` | CharField | `"se1426"`, `"coresso"`, `"eol_alunos"` ou `"todos"` |
| `realm_destino` | CharField | Realm Keycloak de destino |
| `situacao` | CharField | `pendente` / `executando` / `sucesso` / `parcial` / `falha` / `cancelado` |
| `tipo_disparo` | CharField | `"agendado"` (Beat) / `"manual"` (API) |
| `iniciado_em` | DateTimeField | Timestamp de início |
| `finalizado_em` | DateTimeField | Timestamp de conclusão |
| `total_extraido` | IntegerField | Total de registros extraídos |
| `total_transformado` | IntegerField | Total após transformação |
| `total_carregado` | IntegerField | Total provisionado no Keycloak |
| `total_erros` | IntegerField | Total de erros de provisionamento |
| `id_tarefa_celery` | CharField | ID da task Celery associada |

---

## LogEtapaETL

Rastreio de cada etapa dentro de uma execução.

| Campo | Tipo | Descrição |
|---|---|---|
| `execucao` | FK → ExecucaoETL | Execução pai |
| `nome_etapa` | CharField | Nome da task Celery (ver `LogEtapaETL.NomeEtapa`, 1 a 6) |
| `ordem_etapa` | PositiveSmallIntegerField | Ordem de execução |
| `situacao` | CharField | `executando` / `sucesso` / `falha` / `ignorado` |
| `iniciado_em` / `finalizado_em` | DateTimeField | Timestamps |
| `registros_entrada` | IntegerField | Registros recebidos pela etapa |
| `registros_saida` | IntegerField | Registros processados com sucesso |
| `registros_erro` | IntegerField | Registros com erro |
| `metadados` | JSONField | Dados extras (ex.: `{"ignorados": 12}`) |

---

## ControleProvisionamento

Histórico de provisionamento por entidade — chave
`(tipo_entidade, sistema_origem, id_origem, realm_destino)`, persistente
entre execuções (sobrevive à limpeza de staging). Guarda **3 hashes
independentes**, um por estágio do pipeline — cada estágio decide, sem
depender dos outros, se precisa reexecutar. Os 3 hashes precisam bater
para o registro inteiro ser ignorado; onde um não bate, aquele estágio
(e os que dependem dele) reexecuta, mesmo que os outros já estejam
confirmados. Ver [Provisionamento Keycloak](../pipeline/keycloak.md) e
[Carga no token-ms](../pipeline/token_ms.md) para o fluxo de decisão.

| Campo | Tipo | Descrição |
|---|---|---|
| `tipo_entidade` | CharField | `"usuario"`, `"grupo"`, `"role"` ou `"client"` |
| `sistema_origem` | CharField | Fonte de origem |
| `id_origem` | CharField | CPF (prioridade), senão RF, senão PK do staging |
| `id_destino` | CharField | ID da entidade no Keycloak |
| `realm_destino` | CharField | Realm Keycloak de destino |
| `hash_extracao` | CharField | SHA-256 dos campos do staging pós-extração/resolução — muda quando o dado da fonte muda |
| `hash_keycloak` | CharField | SHA-256 do payload enviado ao Keycloak (antigo `hash_conteudo`) |
| `hash_token_ms` | CharField | SHA-256 do payload enviado ao token-ms |
| `token_ms_pendente` | BooleanField | `True` quando `hash_token_ms` está desatualizado — localiza pendências de envio sem depender do staging (limpo periodicamente) |
| `versao` | PositiveIntegerField | Incrementada a cada atualização real no Keycloak |
| `ativo` | BooleanField | Situação ativa/inativa do registro |
| `erro_sincronizacao` | TextField | Último erro de sincronização, se houver |
| `ultima_execucao` | FK → ExecucaoETL | Última execução que processou esta entidade |

---

## CheckpointEtl

Permite retomada da execução por etapa após falha.

| Campo | Tipo | Descrição |
|---|---|---|
| `id_execucao` | UUID | Execução associada |
| `etapa` | CharField | Nome da task interrompida |
| `pagina_atual` | IntegerField | Último lote processado |
| `ultimo_id_processado` | CharField | Último ID de registro processado |
| `estado_json` | JSONField | Estado adicional para retomada |

---

## MarcaDaguaExtracao

Watermark incremental por fonte — evita re-extração completa.

| Campo | Tipo | Descrição |
|---|---|---|
| `fonte` | CharField (PK) | `"se1426"`, `"coresso"` ou `"eol_alunos"` |
| `ultimo_processado_em` | DateTimeField | Timestamp do último registro processado com sucesso |
| `ultima_pagina` | IntegerField | Última página paginada |
| `total_processado` | IntegerField | Total acumulado de registros processados |
| `atualizado_em` | DateTimeField | Timestamp da última atualização |

---

## RastreioTentativa

Registrado em `sync_rec_db` a cada tentativa de execução de uma task.

| Campo | Tipo | Descrição |
|---|---|---|
| `id_execucao` | UUID | Execução associada |
| `nome_tarefa` | CharField | Nome da task Celery |
| `numero_tentativa` | IntegerField | Número da tentativa (base 1) |
| `erro` | TextField | Mensagem de erro (se houver) |
| `duracao_segundos` | FloatField | Duração da tentativa |
| `registrado_em` | DateTimeField | Timestamp |
