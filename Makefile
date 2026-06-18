DC           = docker compose -f docker-compose-dev.yml
RUN          = $(DC) run --rm etl_api
PYTEST_ARGS ?= --cov=apps --cov-report=term-missing --cov-fail-under=80

.PHONY: help build up down logs shell migrate \
        test test-controle test-extracao \
        lint coverage schema docs docs-clean \
        carregar-perfis validar-e2e

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
	@echo "    make validar-e2e               — extração→resolução→Keycloak"
	@echo "                                      (15 registros/fonte) + validacao.md"
	@echo "    make validar-e2e LOTE_MAXIMO=5  — reduz o volume de teste"
	@echo "    make validar-e2e REALM=sme-hom  — outro realm Keycloak"
	@echo ""

# ---------------------------------------------------------------------------
# Ambiente
# ---------------------------------------------------------------------------

build:
	$(DC) build etl_api

up:
	$(DC) up -d postgres_sync_rec postgres_staging keydb

down:
	$(DC) down

logs:
	$(DC) logs -f etl_api

shell:
	$(RUN) python manage.py shell

# ---------------------------------------------------------------------------
# Migrações
# ---------------------------------------------------------------------------

migrate:
	$(RUN) python manage.py migrate --noinput

# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

test:
	$(RUN) python -m pytest $(PYTEST_ARGS) -v

test-controle:
	$(RUN) python -m pytest apps/controle_etl/tests/ \
		--cov=apps.controle_etl --cov-report=term-missing -v

test-extracao:
	$(RUN) python -m pytest apps/extracao/tests/ \
		--cov=apps.extracao --cov-report=term-missing -v

# ---------------------------------------------------------------------------
# Qualidade
# ---------------------------------------------------------------------------

lint:
	$(RUN) bash -c "\
		ruff check . && \
		black --check . && \
		isort --check-only . && \
		mypy apps config"

coverage:
	$(RUN) python -m pytest $(PYTEST_ARGS) \
		--cov-report=html:docs/_cov
	@echo "Relatório gerado em docs/_cov/index.html"

schema:
	$(RUN) python manage.py spectacular --file schema.yml
	@echo "Schema gerado em schema.yml"

docs:
	$(RUN) sphinx-build -b html docs docs/_build/html
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
	$(RUN) python manage.py carregar_perfis \
		$(if $(filter-out -,$(SIS_ID)),--sis-id $(SIS_ID)) \
		$(if $(REALM),--realm $(REALM))

# make validar-e2e
# make validar-e2e LOTE_MAXIMO=5
# make validar-e2e REALM=sme-hom
LOTE_MAXIMO ?= 15

validar-e2e:
	$(RUN) python manage.py validar_e2e \
		--lote-maximo $(LOTE_MAXIMO) \
		$(if $(REALM),--realm $(REALM))
	@echo "Relatório gerado em validacao.md"
