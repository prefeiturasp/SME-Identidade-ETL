#!/usr/bin/env bash
# =============================================================================
# reset-etl-local.sh — Limpa o ambiente local do ETL-MS + Keycloak
#
# Remove containers e volumes do ETL e do Keycloak local (infra compartilhada).
# Use para fazer uma carga do zero completa.
#
# Uso:
#   ./scripts/reset-etl-local.sh          → pede confirmação
#   ./scripts/reset-etl-local.sh --yes    → pula confirmação (CI/automação)
#   ./scripts/reset-etl-local.sh --help   → mostra este menu
#
# Containers removidos:
#   local-etl-api, local-etl-worker, local-etl-beat
#   local-etl-postgres, local-etl-keydb
#   local-keycloak, local-postgres-kc
#
# Volumes removidos:
#   local_etl_pg    (PostgreSQL — etl_db: staging, logs, celery-results)
#   local_etl_keydb (KeyDB/Redis — broker Celery do ETL)
#   local_pg_kc     (PostgreSQL — banco do Keycloak local)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ETL_DIR/.." && pwd)"
ETL_COMPOSE="$ETL_DIR/docker-compose.local.yml"
INFRA_COMPOSE="$REPO_ROOT/local-dev/docker-compose.local-infra.yml"

# --- Cores ---
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${GREEN}  ✔${NC}  $*"; }
warn()  { echo -e "${YELLOW}  ⚠${NC}  $*"; }
error() { echo -e "${RED}  ✖${NC}  $*"; exit 1; }
step()  { echo -e "\n${CYAN}${BOLD}[$1]${NC} $2"; }
hr()    { echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"; }

SKIP_CONFIRM=false
ARG="${1:-}"

case "$ARG" in
  --yes)   SKIP_CONFIRM=true ;;
  --help)  sed -n '3,20p' "$0" | sed 's/^# \?//'; exit 0 ;;
  "")      ;;
  *)       error "Argumento desconhecido: '$ARG'. Use --help." ;;
esac

[ -f "$ETL_COMPOSE" ] || error "docker-compose.local.yml não encontrado em: $ETL_DIR"

# =============================================================================
# Banner
# =============================================================================

echo ""
hr
echo -e "  ${RED}${BOLD}⚠  RESET LOCAL — SME-Identidade-ETL${NC}"
hr
echo ""
echo "  Containers que serão removidos:"
echo "    • local-etl-api"
echo "    • local-etl-worker"
echo "    • local-etl-beat"
echo "    • local-etl-postgres"
echo "    • local-etl-keydb"
echo ""
echo "    • local-keycloak"
echo "    • local-postgres-kc"
echo ""
echo "  Volumes que serão removidos:"
echo "    • local_etl_pg     (PostgreSQL — banco etl_db)"
echo "    • local_etl_keydb  (KeyDB — broker Celery)"
echo "    • local_pg_kc      (PostgreSQL — banco do Keycloak)"
echo ""
hr
echo ""

if [ "$SKIP_CONFIRM" = false ]; then
  echo -e "  ${YELLOW}${BOLD}Esta operação é IRREVERSÍVEL. Todos os dados locais (ETL + Keycloak) serão apagados.${NC}"
  echo ""
  read -r -p "  Digite 'sim' para confirmar: " CONFIRM
  echo ""
  [ "$CONFIRM" = "sim" ] || { echo "  Operação cancelada."; exit 0; }
fi

# =============================================================================
# Reset
# =============================================================================

step "1" "Parando containers e removendo volumes do ETL..."

docker compose -f "$ETL_COMPOSE" down --volumes --remove-orphans 2>/dev/null || true

# Remoção explícita por nome como safety net
for vol in sme-identidade-etl_local_etl_pg sme-identidade-etl_local_etl_keydb; do
  if docker volume inspect "$vol" > /dev/null 2>&1; then
    docker volume rm "$vol" 2>/dev/null && info "Volume $vol removido." || warn "Não foi possível remover $vol (em uso?)."
  fi
done

info "Containers e volumes do ETL removidos."

step "2" "Parando containers e removendo volumes do Keycloak..."

if [ -f "$INFRA_COMPOSE" ]; then
  docker compose -f "$INFRA_COMPOSE" down --volumes --remove-orphans 2>/dev/null || true
  # Remoção explícita por nome como safety net
  if docker volume inspect local-dev_local_pg_kc > /dev/null 2>&1; then
    docker volume rm local-dev_local_pg_kc 2>/dev/null && info "Volume local-dev_local_pg_kc removido." || warn "Não foi possível remover local-dev_local_pg_kc (em uso?)."
  fi
  info "Containers e volumes do Keycloak removidos."
else
  warn "Compose da infra não encontrado: $INFRA_COMPOSE (Keycloak não foi resetado)"
fi

# =============================================================================
# Resumo
# =============================================================================

echo ""
hr
echo -e "  ${GREEN}${BOLD}✔  Reset do ETL concluído.${NC}"
hr
echo ""
echo "  Próximos passos:"
echo ""
echo -e "  ${CYAN}# Subir o ETL do zero (recria banco + aplica migrations):${NC}"
echo "    ./scripts/start-etl-local.sh"
echo ""
echo -e "  ${CYAN}# Ou subir manualmente:${NC}"
echo "    docker compose -f docker-compose.local.yml up -d --build"
echo "    docker compose -f docker-compose.local.yml exec etl-api python manage.py migrate"
echo ""
