# Comandos de Gestão

Comandos Django definidos em `apps/controle_etl/management/commands/`.

---

## executar_etl

Disparo síncrono do pipeline completo, sem passar pelo Celery.

```bash
docker compose -f docker-compose-dev.yml run --rm etl_api \
  python manage.py executar_etl \
    [--fonte todos|se1426|coresso|eol_alunos] \
    [--realm sme-apps]
```

Útil para debug local e testes de ponta a ponta sem broker.

---

## carregar_perfis

Provisiona perfis CoreSSO (`PerfilCoressoStaging`) como client roles no
Keycloak. Processa todos os perfis ou filtra por sistema (`--sis-id`).

```bash
docker compose -f docker-compose-dev.yml run --rm etl_api \
  python manage.py carregar_perfis \
    [--sis-id 42] \
    [--realm sme-apps]
```

Exibe progresso a cada 50 perfis e lista os primeiros 10 erros ao final.

---

## validar_e2e

Valida o pipeline ponta a ponta com volume reduzido (padrão 15 registros
por fonte) contra as origens e o Keycloak reais. Gera um relatório Markdown
(`validacao.md`) com os resultados por fonte e confirmação no Keycloak.

```bash
docker compose -f docker-compose-dev.yml run --rm etl_api \
  python manage.py validar_e2e \
    [--lote-maximo 15] \
    [--realm sme-apps] \
    [--saida validacao.md] \
    [--chunk-size 100]
```

> Não dispara `task_carregar_atributos_token` nem `task_sync_rec_etl` —
> foco em extração, resolução e Keycloak.
