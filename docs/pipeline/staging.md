# Staging

## Objetivo

Persistir os `RegistroIdentidade` extraídos em tabelas PostgreSQL intermediárias
antes da resolução de identidade. O staging isola a extração da carga e
permite retomada sem re-extrair das fontes.

---

## Modelos de staging

Definidos em `apps/staging/models.py`:

| Modelo | Tipo de usuário | Tabela |
|---|---|---|
| `UsuarioServidorStaging` | Servidores (SE1426 / CoreSSO) | `staging_usuario_servidor` |
| `UsuarioAlunoStaging` | Alunos (EOL_DB) | `staging_usuario_aluno` |
| `UsuarioTerceiroStaging` | Terceiros / externos (CoreSSO) | `staging_usuario_terceiro` |
| `SistemaStaging` | Sistemas CoreSSO | `staging_sistema` |
| `PerfilCoressoStaging` | Grupos/perfis CoreSSO | `staging_perfil_coresso` |

Todos os modelos de usuário herdam de `_UsuarioStagingBase`, que define:
`id_execucao`, `fonte`, `id_origem`, `cpf`, `rf`, `nome`, `email`,
`situacao`, `situacao` (ETL: `extraido` → `pronto` / `erro`), `detalhe_erro`.

`fonte` normalmente reflete a origem da extração (`se1426`, `coresso`,
`eol_alunos`), mas o endpoint `POST /api/v1/etl/usuario/criar/` também
materializa registros diretamente com `fonte="api_manual"` — usuários sem
vínculo prévio no CoreSSO, criados a partir de dados informados na
requisição em vez de um pipeline de extração. Ver
[API REST de Controle](../controle/api.md).

---

## Tasks de staging

Definidas em `apps/staging/tasks.py`:

| Task | Função |
|---|---|
| `persistir_extracao_staging` | Salva `RegistroIdentidade` nos modelos corretos via `bulk_create` |
| `transformar_staging` | Valida e normaliza os registros; muda `situacao` para `pronto` ou `erro` |
| `deduplicar_identidades` | Elege um registro vencedor por CPF e por RF; marca os demais como `duplicado` |

---

## Ciclo de vida de um registro

```{graphviz}
digraph G {
    rankdir=LR;
    node [shape=box, style="rounded"];

    EXT  [label="extraido"];
    PRNT [label="pronto"];
    ERR  [label="erro"];
    DUP  [label="duplicado"];
    CARR [label="carregado"];

    EXT  -> PRNT [label="transformar_staging\n(validacao OK)"];
    EXT  -> ERR  [label="transformar_staging\n(CPF e nome ausentes)"];
    PRNT -> DUP  [label="deduplicar_identidades\n(perdedor)"];
    PRNT -> CARR [label="provisionar_usuario_kc\n(sucesso)"];
}
```

---

## Limpeza de staging

Após a execução, `task_identidade_limpar_staging` remove registros de
execuções antigas, mantendo as `ETL_MANTER_ULTIMAS_EXECUCOES` (padrão 3)
mais recentes com sucesso.
