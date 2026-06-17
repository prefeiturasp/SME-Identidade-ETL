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
| `situacao` | CharField | `pendente` / `executando` / `sucesso` / `falha` / `cancelado` |
| `tipo_disparo` | CharField | `"api"` / `"manual"` / `"agendado"` |
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
| `nome_etapa` | CharField | Nome da task Celery |
| `ordem_etapa` | IntegerField | Ordem de execução (1–8) |
| `situacao` | CharField | `pendente` / `executando` / `sucesso` / `falha` |
| `iniciado_em` / `finalizado_em` | DateTimeField | Timestamps |
| `registros_entrada` | IntegerField | Registros recebidos pela etapa |
| `registros_saida` | IntegerField | Registros processados com sucesso |
| `registros_erro` | IntegerField | Registros com erro |
| `metadados` | JSONField | Dados extras (ex.: `{"ignorados": 12}`) |

---

## ControleProvisionamento

Histórico de provisionamento por entidade — permite detecção de mudança
via hash de conteúdo.

| Campo | Tipo | Descrição |
|---|---|---|
| `tipo_entidade` | CharField | `"usuario"`, `"sistema"` ou `"perfil"` |
| `sistema_origem` | CharField | Fonte de origem |
| `id_origem` | CharField | ID na fonte |
| `situacao` | CharField | `"provisionado"` / `"erro"` / `"ignorado"` |
| `hash_conteudo` | CharField | SHA-256 do payload para detecção de mudança |
| `id_execucao` | UUID | Última execução que processou esta entidade |
| `kc_user_id` | CharField | ID do usuário no Keycloak |

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
| `fonte` | CharField (unique) | `"se1426"`, `"coresso"` ou `"eol_alunos"` |
| `ultima_data_referencia` | DateTimeField | Data do último registro processado |
| `ultima_pagina` | IntegerField | Última página paginada |
| `total_processado` | IntegerField | Total acumulado de registros processados |
| `ultimo_processado_em` | DateTimeField | Timestamp da última atualização |

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
