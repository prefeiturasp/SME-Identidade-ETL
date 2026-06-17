# Provisionamento Keycloak

## Objetivo

Criar ou atualizar usuários, roles e grupos no Keycloak a partir dos registros
com `situacao="pronto"` no staging.

---

## Task

`task_provisionar_identidade_keycloak` em `apps/controle_etl/tasks.py`.

Processa em lotes de `ETL_TAMANHO_LOTE_PROVISIONAMENTO` (padrão 200) usando
`ThreadPoolExecutor` para paralelismo controlado.

---

## Orquestrador

`apps/controle_etl/orquestrador_kc.py` — encapsula toda a comunicação com a
Admin API do Keycloak via `python-keycloak`.

### Funções principais

| Função | Responsabilidade |
|---|---|
| `obter_admin_keycloak(realm)` | Autentica e retorna cliente `KeycloakAdmin` |
| `construir_payload_kc(usuario)` | Monta atributos do usuário para o Keycloak |
| `provisionar_usuario_kc(admin, usuario, ...)` | Upsert de um usuário; retorna `acao` (`criado`/`atualizado`/`ignorado`/`erro`) |
| `provisionar_usuarios_kc_em_paralelo(admin, usuarios, ...)` | Executa lotes em `ThreadPoolExecutor` |
| `provisionar_client_kc(admin, sistema)` | Cria ou atualiza um client Keycloak a partir de `SistemaStaging` |
| `provisionar_role_client_kc(admin, perfil)` | Cria ou atualiza uma client role a partir de `PerfilCoressoStaging` |
| `calcular_hash_conteudo(payload)` | SHA-256 do payload para detecção de mudança |

---

## Fluxo de upsert por usuário

```{graphviz}
digraph G {
    rankdir=TB;
    node [shape=box, style="rounded"];

    HASH  [label="Calcular hash\ndo payload"];
    BUSCA [label="Buscar usuario\nno Keycloak\n(por email / RF / CPF)"];
    EQ    [label="Hash igual?"];
    IGN   [label="ignorado"];
    UPD   [label="Atualizar usuario\n+ roles + grupos"];
    CRT   [label="Criar usuario\n+ roles + grupos"];
    CTL   [label="Atualizar\nControleProvisionamento"];

    HASH -> BUSCA;
    BUSCA -> EQ   [label="encontrado"];
    BUSCA -> CRT  [label="nao encontrado"];
    EQ    -> IGN  [label="sim"];
    EQ    -> UPD  [label="nao"];
    UPD   -> CTL;
    CRT   -> CTL;
}
```

---

## Roles e grupos derivados

`_derivar_roles_realm(usuario)` — mapeia cargo e função para roles do realm:

| Cargo / Função (substring) | Role Keycloak |
|---|---|
| `PROFESSOR` | `Professor` |
| `DIRETOR DE ESCOLA` | `Diretor` |
| `COORDENADOR PEDAGOGICO` | `CoordenadorPedagogico` |
| `ASSISTENTE DE DIRETOR` | `AssistenteDiretor` |
| _(e outros mapeamentos)_ | |

`_derivar_grupos(usuario)` — determina grupos de DRE e UE.

---

## Reconexão entre lotes

A cada lote, `obter_admin_keycloak` é chamado novamente para renovar o token
de sessão do Keycloak antes que expire.
