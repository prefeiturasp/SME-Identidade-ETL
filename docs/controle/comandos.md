# Comandos de Gestão

Comandos Django definidos em `apps/controle_etl/management/commands/`.

---

## executar_etl

Disparo síncrono do pipeline completo, sem passar pelo Celery.

```bash
make shell
python manage.py executar_etl \
  [--fonte todos|se1426|coresso|eol_alunos] \
  [--realm sme-apps]
```

---

## carregar_perfis

Provisiona perfis CoreSSO como client roles no Keycloak.

```bash
make carregar-perfis
make carregar-perfis SIS_ID=42
make carregar-perfis SIS_ID=42 REALM=sme-hom
```

---

## validar_e2e

Valida o pipeline ponta a ponta contra as bases e Keycloak reais.
Gera relatório em `validacao_e2e/{data_hora}.md`.

```bash
# Pipeline completo (15 registros/fonte)
make validar-e2e

# Só CoreSSO
make validar-e2e FONTE=coresso LOTE_MAXIMO=5

# Modo sistema — extrai apenas vínculos do sistema,
# cria usuários ausentes no KC e valida
make validar-e2e SIS_ID=1008

# Forçar atualização no Keycloak
make validar-e2e SIS_ID=1008 FORCAR=true
```

**Argumentos:**

| Argumento | Padrão | Descrição |
|---|---|---|
| `--lote-maximo` | 15 | Teto de registros por fonte (0 = sem limite) |
| `--fonte` | todos | `todos`, `se1426`, `coresso`, `eol_alunos` |
| `--sis-id` | — | Filtra por sistema CoreSSO |
| `--gru-id` | — | Filtra por grupo CoreSSO |
| `--realm` | sme-apps | Realm Keycloak |
| `--forcar-atualizacao` | false | Força update mesmo sem mudança |
| `--saida` | auto | Caminho do relatório |

**Modo `--sis-id`:**

Quando informado, o E2E opera no modo sistema:
1. Extrai sistemas e perfis do CoreSSO
2. Provisiona clients e client roles no Keycloak
3. Extrai vínculos do sistema filtrado
4. Cria no Keycloak os usuários que não existem
5. Atribui client roles (vínculos)
6. Valida todos os usuários do sistema no Keycloak
7. Gera relatório com nome, grupos e link direto

---

## sincronizar_usuario

Sincroniza um usuário individual no Keycloak com todos os seus
sistemas e roles do CoreSSO.

```bash
# Por RF
make sincronizar-usuario USER=6913261

# Por CPF
make sincronizar-usuario USER=11122233344

# Por email
make sincronizar-usuario USER=angela@sme.prefeitura.sp.gov.br
```

O comando:
1. Busca o usuário no CoreSSO (por RF, CPF ou email)
2. Lista todos os sistemas e grupos associados
3. Cria ou atualiza no Keycloak
4. Atribui todos os client roles
5. Salva resultado em `validacao_e2e/usuario_{identificador}.json`

---

## validar_login

Testa o login de um usuário no Keycloak via OpenID Connect.

```bash
# Login por RF (senha padrão do .env)
make validar-login USER=6913261

# Senha customizada
make validar-login USER=6913261 SENHA=minhasenha
```

O comando:
1. Busca o usuário no Keycloak (por username, atributo rf ou cpf)
2. Reseta a senha (padrão: `VALIDAR_LOGIN_SENHA_PADRAO` do `.env`)
3. Faz login via OpenID Connect (grant type password)
4. Obtém userinfo e roles do token
5. Salva resultado em `validacao_login/{username}.json`

**Variáveis de ambiente:**

| Variável | Descrição |
|---|---|
| `VALIDAR_LOGIN_SENHA_PADRAO` | Senha usada quando `--senha` não é informada |
