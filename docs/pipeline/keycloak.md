# Provisionamento Keycloak

## Objetivo

Criar ou atualizar usuários, clients, client roles, grupos e vínculos no
Keycloak a partir dos dados do CoreSSO e das fontes SE1426/EOL_DB.

---

## Hierarquia no Keycloak

```
Realm (sme-apps)
├── Client (sistema CoreSSO → clientId: "{nome_slug}-{sufixo}")
│   └── Client Role (grupo CoreSSO → name: slug de gru_nome)
├── User (username: CPF ou RF)
│   ├── Realm Roles (cargo/função: Professor, Diretor, etc.)
│   ├── Groups (hierarquia: /SME/DRE-{dre}/UE-{ue})
│   └── Client Role Mappings (vínculos grupo↔sistema)
└── Realm Role (cargo/função genérica)
```

---

## Orquestrador

`apps/controle_etl/orquestrador_kc.py` — encapsula toda a comunicação com a
Admin API do Keycloak via `python-keycloak`.

### Funções principais

| Função | Responsabilidade |
|---|---|
| `obter_admin_keycloak(realm)` | Autentica e retorna cliente `KeycloakAdmin` |
| `construir_payload_kc(usuario)` | Monta atributos do usuário para o Keycloak |
| `provisionar_usuario_kc(admin, usuario, ...)` | Upsert de um usuário; suporta `forcar_atualizacao` |
| `provisionar_usuarios_kc_em_paralelo(...)` | Lotes em `ThreadPoolExecutor` |
| `provisionar_client_kc(admin, sistema)` | Cria ou atualiza client a partir de `SistemaStaging` |
| `provisionar_role_client_kc(admin, perfil)` | Cria ou atualiza client role a partir de `PerfilCoressoStaging` |
| `atribuir_client_roles_usuario_kc(admin, vinculos)` | Atribui client roles em streaming com cache |
| `sincronizar_usuario_kc(admin, dados_coresso)` | Sincroniza um usuário com todos os seus roles |
| `conceder_acesso_kc(admin, dados_coresso, sis_id, roles)` | Upsert de usuário do CoreSSO + concessão de roles de um sistema |
| `_conceder_roles_sistema_kc(admin, kc_user_id, sis_id, roles)` | Núcleo de resolução/atribuição de roles — não faz upsert de usuário |
| `calcular_hash_conteudo(payload)` | SHA-256 para detecção de mudança |

---

## Mapeamento CoreSSO → Keycloak

| CoreSSO | Keycloak | Exemplo |
|---|---|---|
| `SYS_Sistema` | Client | `auto-servico-qa` |
| `SYS_Grupo` | Client Role | `COPED`, `ASCOM` |
| `SYS_Usuario` | User | username = CPF ou RF |
| `SYS_UsuarioGrupo` | Client Role Mapping | Usuário ← role COPED no client auto-servico-qa |

---

## Fluxo de upsert por usuário

```{graphviz}
digraph G {
    rankdir=TB;
    node [shape=box, style="rounded"];

    HASH  [label="Calcular hash\ndo payload"];
    BUSCA [label="Buscar usuario\nno Keycloak\n(por email / RF / CPF)"];
    EQ    [label="Hash igual?\n(forcar_atualizacao?)"];
    IGN   [label="ignorado"];
    UPD   [label="Atualizar usuario\n+ roles + grupos"];
    CRT   [label="Criar usuario\n+ roles + grupos"];
    CTL   [label="Atualizar\nControleProvisionamento"];

    HASH -> BUSCA;
    BUSCA -> EQ   [label="encontrado"];
    BUSCA -> CRT  [label="nao encontrado"];
    EQ    -> IGN  [label="sim e nao forcar"];
    EQ    -> UPD  [label="nao ou forcar"];
    UPD   -> CTL;
    CRT   -> CTL;
}
```

---

## Provisionamento de vínculos (client roles)

A função `atribuir_client_roles_usuario_kc` opera em streaming:

1. Recebe iterador de vínculos `{login, cpf, gru_id, gru_nome, sis_id}`
2. Para cada vínculo, resolve o role e o usuário via cache local
3. Chama `admin.assign_client_role(user_id, client_uuid, [role])`
4. Retorna contadores `{atribuidos, ignorados, erros}`

Cache evita lookups repetidos na API do Keycloak (mesmo usuário
em múltiplos grupos, mesmo grupo para múltiplos usuários).

---

## Sincronização individual

A função `sincronizar_usuario_kc` recebe os dados do CoreSSO
(resultado de `buscar_dados_usuario_coresso`) e:

1. Cria ou atualiza o usuário no Keycloak
2. Para cada sistema do usuário, atribui os client roles correspondentes
3. Retorna resumo com ação, roles atribuídos e link do admin console

---

## Concessão de acesso a sistema

`_conceder_roles_sistema_kc(admin, kc_user_id, sis_id, nomes_roles)` é o
núcleo reaproveitável de concessão de acesso — recebe um `kc_user_id` já
existente (upsert feito por quem chama) e:

1. Busca o `SistemaStaging` pelo `coresso_sis_id` informado — se não
   encontrado ou sem `kc_client_uuid`, retorna só `{"erro": ...}`
2. Para cada role solicitado, busca o `PerfilCoressoStaging` correspondente
   (por `nome` ou `kc_role_nome`); se não existir, cria via `_criar_role_kc`
3. Atribui o client role ao usuário (`admin.assign_client_role`)
4. Retorna `sistema`, `client_id`, `roles_atribuidos`, `roles_nao_encontrados`
   e `erros`

Duas rotas da API reaproveitam esse núcleo, cada uma resolvendo o
`kc_user_id` de um jeito diferente:

```{graphviz}
digraph G {
    rankdir=TB;
    node [shape=box, style="rounded"];

    subgraph cluster_coresso {
        label="usuario/conceder-acesso/";
        BUSCA_CS [label="buscar_dados_usuario_coresso"];
        UPSERT_CS [label="_upsert_coresso_kc"];
        BUSCA_CS -> UPSERT_CS;
    }

    subgraph cluster_manual {
        label="usuario/criar/";
        STAGING [label="materializa\nUsuarioXStaging\n(fonte=api_manual)"];
        UPSERT_MAN [label="provisionar_usuario_kc"];
        STAGING -> UPSERT_MAN;
    }

    NUCLEO [label="_conceder_roles_sistema_kc\n(kc_user_id, sis_id, roles)"];

    UPSERT_CS -> NUCLEO;
    UPSERT_MAN -> NUCLEO [label="se sistema/roles\nforem informados"];
}
```

Na criação manual (`usuario/criar/`), `sistema`/`roles` são opcionais —
sem eles, a chamada só cria a identidade; com eles, cria e concede acesso
na mesma requisição.

---

## Roles e grupos derivados

`_derivar_roles_realm(usuario)` — mapeia cargo e função para roles do realm:

| Cargo / Função | Role Keycloak |
|---|---|
| `PROFESSOR` | `Professor` |
| `DIRETOR DE ESCOLA` | `Diretor` |
| `COORDENADOR PEDAGOGICO` | `CoordenadorPedagogico` |
| `ASSISTENTE DE DIRETOR` | `AssistenteDiretor` |

`_derivar_grupos(usuario)` — determina grupos de DRE e UE:
`/SME/DRE-{dre}/UE-{ue}`.

---

## Reconexão entre lotes

A cada lote, `obter_admin_keycloak` é chamado novamente para renovar o token
de sessão do Keycloak antes que expire.
