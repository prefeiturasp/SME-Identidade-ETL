#!/usr/bin/env bash
# =============================================================================
# clean-keycloak-realms.sh — Zera os realms do Keycloak deixando apenas o master
#
# Deleta os realms COTIC, sme-apps e sme-devops via Admin REST API.
# O realm master permanece intacto com o usuário admin.
# Keycloak permanece rodando. Nenhum volume é removido.
#
# Pré-requisito: Keycloak rodando em http://localhost:8080
#
# Uso:
#   ./scripts/clean-keycloak-realms.sh          → pede confirmação
#   ./scripts/clean-keycloak-realms.sh --yes    → pula confirmação
#   ./scripts/clean-keycloak-realms.sh --help   → mostra este menu
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
KC_DIR="$REPO_ROOT/keycloak"

KC_URL="http://localhost:8080"
KC_ADMIN="admin"
KC_PASS="admin"

REALM_NAMES=("COTIC" "sme-apps" "sme-devops")

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
  --yes)  SKIP_CONFIRM=true ;;
  --help) sed -n '3,14p' "$0" | sed 's/^# \?//'; exit 0 ;;
  "")     ;;
  *)      error "Argumento desconhecido: '$ARG'. Use --help." ;;
esac

# =============================================================================
# Banner
# =============================================================================
echo ""
hr
echo -e "  ${RED}${BOLD}⚠  LIMPEZA DE REALMS — KEYCLOAK LOCAL${NC}"
hr
echo ""
echo "  Realms que serão DELETADOS:"
echo "    • COTIC"
echo "    • sme-apps"
echo "    • sme-devops"
echo ""
echo "  Realm preservado (sem alteração):"
echo "    • master  (usuário admin intacto)"
echo ""
echo "  Keycloak permanece rodando. Nenhum volume é removido."
echo "  Os realms NÃO serão reimportados."
hr
echo ""

if [ "$SKIP_CONFIRM" = false ]; then
  echo -e "  ${YELLOW}${BOLD}Todos os usuários dos realms acima serão apagados.${NC}"
  echo ""
  read -r -p "  Digite 'sim' para confirmar: " CONFIRM
  echo ""
  [ "$CONFIRM" = "sim" ] || { echo "  Operação cancelada."; exit 0; }
fi

# =============================================================================
# Step 1 — Verificar Keycloak disponível
# =============================================================================
step "1" "Verificando Keycloak em $KC_URL..."
if ! curl -sf "$KC_URL/health/ready" --max-time 10 > /dev/null 2>&1; then
  error "Keycloak não está respondendo. Inicie com: ./scripts/start-etl-local.sh"
fi
info "Keycloak online."

# =============================================================================
# Step 2 — Obter token de admin
# =============================================================================
step "2" "Autenticando no master realm..."
TOKEN_RESPONSE=$(curl -sf -X POST \
  "$KC_URL/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=$KC_ADMIN" \
  -d "password=$KC_PASS" \
  -d "grant_type=password" 2>/dev/null) \
  || error "Falha ao obter token admin. Verifique credenciais (admin/admin)."

TOKEN=$(python3 -c "import sys,json; print(json.loads('''$TOKEN_RESPONSE''')['access_token'])" 2>/dev/null) \
  || error "Falha ao parsear token. Resposta: $TOKEN_RESPONSE"

info "Token obtido."

# =============================================================================
# Step 3 — Deletar realms existentes
# =============================================================================
step "3" "Deletando realms..."
for REALM in "${REALM_NAMES[@]}"; do
  HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
    -H "Authorization: Bearer $TOKEN" \
    "$KC_URL/admin/realms/$REALM")
  case "$HTTP" in
    204) info "Realm '$REALM' deletado." ;;
    404) warn "Realm '$REALM' não encontrado (já estava ausente)." ;;
    401) error "Token expirado ou sem permissão. Execute novamente." ;;
    *)   warn "DELETE $REALM → HTTP $HTTP (pode já ter sido removido)." ;;
  esac
done

# =============================================================================
# Step 4 — Verificar realms restantes
# =============================================================================
step "4" "Verificando realms ativos..."

REALMS_ATIVOS=$(curl -sf \
  -H "Authorization: Bearer $TOKEN" \
  "$KC_URL/admin/realms" 2>/dev/null \
  | python3 -c "import sys,json; [print('      •', r['realm']) for r in json.load(sys.stdin)]" 2>/dev/null \
  || echo "      (não foi possível listar)")
echo "$REALMS_ATIVOS"

# =============================================================================
# Resumo
# =============================================================================
echo ""
hr
echo -e "  ${GREEN}${BOLD}REALMS DELETADOS COM SUCESSO${NC}"
hr
echo ""
echo "  Keycloak  →  http://localhost:8080  (admin / admin)"
echo "  Apenas o realm master permanece ativo."
echo ""
echo "  Para reimportar os realms de dev na próxima execução:"
echo "    ./scripts/start-etl-local.sh"
echo ""
echo "  Para disparar o pipeline após reimport:"
echo "    ./scripts/trigger-pipeline.sh"
echo ""
