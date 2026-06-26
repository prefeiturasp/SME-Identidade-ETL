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
