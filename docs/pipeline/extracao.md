# Extração

## Objetivo

Extrair usuários das três fontes SQL Server para o staging PostgreSQL e
publicar as configurações de recuperação de senha do EOL DB APOIO no MS-Token.

---

## Fontes e tasks

| Fonte | Task | Módulo de extração |
|---|---|---|
| SE1426 | `task_identidade_extrair_se1426` | `apps.extracao.services.usuarios.Se1426Service` |
| CoreSSO | `task_identidade_extrair_coresso` | `apps.extracao.services.usuarios.CoreSSOService` |
| EOL_DB (alunos) | `task_identidade_extrair_eol_alunos` | `apps.extracao.services.alunos.AlunosService` |

As três tasks disparam em paralelo via `chord` do Celery. O callback de
resolução só executa após todas concluírem.

Os serviços ficam em `services/`, separados em `usuarios`, `alunos`,
`sistemas` e `grupos_e_permissoes`. Cada domínio tem um arquivo
`*_queries.py` correspondente em `sql/`; execução, parâmetros e mapeamento
permanecem nos serviços. As conexões ficam em `config/conexoes_db.py`.

`grupos_e_permissoes` concentra perfis, grupos, associações de usuários a
grupos e permissões CRUD. Vínculos funcionais permanecem em `usuarios`.
Usuários e alunos usam `BaseETLService` para leitura, limites e confirmação
do watermark após a persistência no staging.
As entradas de `apps.extracao.tasks` continuam disponíveis.

---

## RegistroIdentidade

Dataclass intermediária definida em `apps/extracao/dtos.py`:

| Campo | Descrição |
|---|---|
| `fonte` | `"se1426"`, `"coresso"` ou `"eol_alunos"` |
| `tipo` | `"servidor"`, `"aluno"` ou `"terceiro"` |
| `id_origem` | ID primário na fonte |
| `cpf` | CPF normalizado (somente dígitos) |
| `rf` | Registro Funcional (servidores) |
| `nome` | Nome completo |
| `email` | E-mail institucional |
| `situacao` | Situação na fonte |
| `matricula` | Matrícula (alunos/terceiros) |
| `cod_escola` | Código da escola (alunos) |

---

## Watermark incremental

O `BaseETLService` consulta `MarcaDaguaExtracao` (app `controle_etl`) antes
da leitura. As fontes com filtro temporal usam essa marca como data inicial;
`data_referencia` permite substituir a data nessa execução.

O método `executar()` confirma o watermark após a gravação de todos os lotes
no staging e o commit. A marca corresponde ao início da leitura. Falhas na
origem, na persistência ou interrupções por limite mantêm a marca anterior.
O método `extrair()` apenas fornece registros, sem avançar o watermark.

Para forçar extração completa, resete a marca via
`POST /api/v1/etl/watermark/{fonte}/resetar/`.

---

## Paginação

As consultas SQL Server usam `fetchmany(chunk_size)`, controlado por
`ETL_CHUNK_SIZE` (padrão 500). A leitura das APIs é paginada; falhas são
propagadas para as retentativas do Celery. `ETL_LOTE_MAXIMO` limita o total
de registros entregues, sem avançar o watermark de uma leitura parcial.

---

## Sistemas para recuperação de senha

A função `apps.extracao.services.sistemas.extrair_sistemas_recuperacao` lê a tabela
`public.sistemarecuperacaosenha` no PostgreSQL do EOL DB APOIO, em modo somente
leitura. A conexão usa `ApiEolConnection` ou `API_EOL_DB_URL`, nessa ordem
de prioridade.

| Campo | Descrição |
|---|---|
| `codigo_sistema` | Código do sistema na recuperação de senha |
| `nome_sistema` | Nome do sistema |
| `pagina_recuperacao_senha` | URL à qual o MS-Token acrescenta o token |

A função `apps.controle_etl.tasks.sincronizar_sistemas_recuperacao` publica
os dados na seção `sistemas_recuperacao_senha` do `push-batch`, usando a
configuração `TOKEN_MS_*`. O MS-Token cria ou atualiza os cadastros pelo código.
Essa carga é manual, independente do pipeline de usuários, sem staging ou
watermark.

Para executar, use o endpoint abaixo, sem corpo e com a API Key do ETL:

```text
POST /identidade-etl/api/v1/etl/sistemas-recuperacao-senha/sincronizar/
```

Ou o comando Django:

```bash
python manage.py sincronizar_sistemas_recuperacao
```

O retorno informa os totais `processados`, `criados` e `atualizados`
confirmados pelo MS-Token.

A sincronização registra uma `ExecucaoETL` com a fonte `eol_db_apoio`.
O dashboard soma os registros lidos e os envios confirmados ao MS-Token;
a contagem de Keycloak permanece zero nessa carga.
