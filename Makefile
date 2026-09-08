COMPOSE      = docker compose -f docker-compose-dev.yml
EXEC_API     = $(COMPOSE) exec etl_api
RUN_API      = $(COMPOSE) run --rm etl_api
PYTEST_ARGS ?= --cov=apps --cov-report=term-missing --cov-fail-under=90

.PHONY: help build up down logs shell migrate \
        test test-controle test-extracao \
        lint coverage schema docs docs-clean \
        carregar-perfis validar-e2e validar-login \
        sincronizar-usuario

help:
	@echo ""
	@echo "Uso: make <comando>"
	@echo ""
	@echo "  Ambiente:"
	@echo "    make build             — rebuild da imagem dev"
	@echo "    make up                — sobe postgres (SYNC_REC_DB) e keydb"
	@echo "    make down              — derruba todos os containers"
	@echo "    make logs              — acompanha logs do etl_api"
	@echo "    make shell             — abre shell Django interativo"
	@echo ""
	@echo "  Migrações:"
	@echo "    make migrate           — aplica migrations no SYNC_REC_DB"
	@echo ""
	@echo "  Testes:"
	@echo "    make test              — todos os apps com cobertura ≥80%"
	@echo "    make test-controle     — apenas apps.controle_etl"
	@echo "    make test-extracao     — apenas apps.extracao"
	@echo ""
	@echo "  Qualidade:"
	@echo "    make lint              — ruff + black + isort + mypy"
	@echo "    make coverage          — relatório HTML em docs/_cov/"
	@echo "    make schema            — gera schema OpenAPI em schema.yml"
	@echo "    make docs              — gera documentação Sphinx"
	@echo ""
	@echo "  Scripts operacionais:"
	@echo "    make carregar-perfis           — carrega todos os perfis CoreSSO"
	@echo "    make carregar-perfis SIS_ID=42 — apenas sistema id=42"
	@echo "    make carregar-perfis SIS_ID=42 REALM=sme-hom"
	@echo ""
	@echo "  Validação E2E:"
	@echo "    make validar-e2e                         — pipeline completo (15 reg/fonte)"
	@echo "    make validar-e2e LOTE_MAXIMO=5            — reduz o volume de teste"
	@echo "    make validar-e2e REALM=sme-hom            — outro realm Keycloak"
	@echo "    make validar-e2e SIS_ID=1008              — filtra por sistema (Auto Serviço)"
	@echo "    make validar-e2e SIS_ID=1008 GRU_ID=abc   — filtra por sistema e grupo"
	@echo ""
	@echo "  Sincronizar Usuário:"
	@echo "    make sincronizar-usuario USER=6913261      — por RF"
	@echo "    make sincronizar-usuario USER=11122233344  — por CPF"
	@echo "    make sincronizar-usuario USER=a@sme.sp     — por email"
	@echo ""
	@echo "  Validação de Login:"
	@echo "    make validar-login USER=11122233344        — testa login por CPF"
	@echo "    make validar-login USER=6913261            — testa login por RF"
	@echo "    make validar-login USER=6913261 SENHA=xyz  — senha customizada"
	@echo ""

# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------

build:
	$(COMPOSE) up -d --build

up:
	$(COMPOSE) up -d \
		postgres_sync_rec \
		keydb

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f etl_api

shell:
	$(EXEC_API) python manage.py shell

# ---------------------------------------------------------------------------
# Migrações
# ---------------------------------------------------------------------------

migrate:
	$(EXEC_API) python manage.py migrate --noinput

# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

test:
	$(RUN_API) python -m pytest $(PYTEST_ARGS) -v

test-controle:
	$(RUN_API) python -m pytest apps/controle_etl/tests/ \
		--cov=apps.controle_etl \
		--cov-report=term-missing \
		-v

test-extracao:
	$(RUN_API) python -m pytest apps/extracao/tests/ \
		--cov=apps.extracao \
		--cov-report=term-missing \
		-v

# ---------------------------------------------------------------------------
# Qualidade
# ---------------------------------------------------------------------------

lint:
	$(RUN_API) bash -c "\
		ruff check . && \
		black --check . && \
		isort --check-only . && \
		mypy apps config"

coverage:
	$(RUN_API) python -m pytest $(PYTEST_ARGS) \
		--cov-report=html:docs/_cov
	@echo "Relatório gerado em docs/_cov/index.html"

schema:
	$(EXEC_API) python manage.py spectacular --file schema.yml
	@echo "Schema gerado em schema.yml"

docs:
	$(RUN_API) sphinx-build -b html docs docs/_build/html
	@echo "Documentação gerada em docs/_build/html/index.html"

docs-clean:
	rm -rf docs/_build

# ---------------------------------------------------------------------------
# Scripts operacionais
# ---------------------------------------------------------------------------

# make carregar-perfis
# make carregar-perfis SIS_ID=42
# make carregar-perfis SIS_ID=42 REALM=sme-hom
SIS_ID ?= -
REALM  ?=

carregar-perfis:
	$(EXEC_API) python manage.py carregar_perfis \
		$(if $(filter-out -,$(SIS_ID)),--sis-id $(SIS_ID)) \
		$(if $(REALM),--realm $(REALM))

# make validar-e2e
# make validar-e2e LOTE_MAXIMO=5
# make validar-e2e FONTE=coresso               — só CoreSSO
# make validar-e2e FONTE=se1426                 — só SE1426
# make validar-e2e FONTE=eol_alunos             — só EOL_DB
# make validar-e2e REALM=sme-hom
# make validar-e2e SIS_ID=1008                  — modo sistema
# make validar-e2e SIS_ID=1008 FORCAR=true
LOTE_MAXIMO ?= 15
GRU_ID      ?=
FORCAR      ?=
FONTE       ?= todos

validar-e2e:
	$(EXEC_API) python manage.py validar_e2e \
		--lote-maximo $(LOTE_MAXIMO) \
		--fonte $(FONTE) \
		$(if $(REALM),--realm $(REALM)) \
		$(if $(filter-out -,$(SIS_ID)),--sis-id $(SIS_ID)) \
		$(if $(GRU_ID),--gru-id $(GRU_ID)) \
		$(if $(FORCAR),--forcar-atualizacao)
	@echo "Relatório salvo em validacao_e2e/"

# make validar-login USER=11122233344
# make validar-login USER=6913261 SENHA=minhasenha
# make validar-login USER=6913261 REALM=sme-hom
SENHA ?=

validar-login:
	$(EXEC_API) python manage.py validar_login $(USER) \
		$(if $(SENHA),--senha $(SENHA)) \
		$(if $(REALM),--realm $(REALM))
	@echo "Resultado salvo em validacao_login/"

# make sincronizar-usuario USER=6913261
# make sincronizar-usuario USER=11122233344
# make sincronizar-usuario USER=angela@sme.sp REALM=sme-hom
sincronizar-usuario:
	$(EXEC_API) python manage.py sincronizar_usuario $(USER) \
		$(if $(REALM),--realm $(REALM))
	@echo "Resultado salvo em validacao_e2e/"
