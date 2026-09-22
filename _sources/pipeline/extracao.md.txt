# Extração

## Objetivo

Ler dados brutos das três fontes SQL Server e persistir como registros
`RegistroIdentidade` no staging do PostgreSQL.

---

## Fontes e tasks

| Fonte | Task | Módulo de extração |
|---|---|---|
| SE1426 | `task_identidade_extrair_se1426` | `apps.extracao.tasks.extrair_se1426` |
| CoreSSO | `task_identidade_extrair_coresso` | `apps.extracao.tasks.extrair_coresso` |
| EOL_DB (alunos) | `task_identidade_extrair_eol_alunos` | `apps.extracao.tasks.extrair_eol_alunos` |

As três tasks disparam em paralelo via `chord` do Celery. O callback de
resolução só executa após todas concluírem.

---

## RegistroIdentidade

Dataclass intermediária definida em `apps/extracao/tasks.py`:

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

Cada extração consulta `_obter_watermark(fonte)` antes de executar e chama
`_atualizar_watermark(fonte, ultima_data, total_processado)` ao concluir.

O watermark é persistido em `MarcaDaguaExtracao` (app `controle_etl`) e
permite que extrações subsequentes busquem apenas registros alterados após a
última execução bem-sucedida.

Para forçar extração completa, passe `data_referencia=None` ou resete via
`POST /api/v1/etl/watermark/{fonte}/resetar/`.

---

## Paginação

As queries SQL Server usam `fetchmany(chunk_size)` em loop, controlado por
`ETL_CHUNK_SIZE` (padrão 500). O watermark salva `ultima_pagina` para
retomada em caso de interrupção.
