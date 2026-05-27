#!/usr/bin/env bash
# =============================================================================
# start-etl-local.sh — Sobe o ETL-MS em ambiente local
# Dependências: Docker, Docker Compose v2, acesso à rede interna 10.49.x.x
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ETL_DIR="$REPO_ROOT/SME-Identidade-ETL"
INFRA_COMPOSE="$REPO_ROOT/local-dev/docker-compose.local-infra.yml"
ETL_COMPOSE="$ETL_DIR/docker-compose.local.yml"
ENV_LOCAL="$ETL_DIR/.env.local"
ENV_EXAMPLE="$ETL_DIR/.env.local.example"

# --- Cores ---
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}>>>${NC} $*"; }
warn()  { echo -e "${YELLOW}AVISO:${NC} $*"; }
error() { echo -e "${RED}ERRO:${NC} $*"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Criar .env.local automaticamente se não existir
# ---------------------------------------------------------------------------
info "[1/5] Verificando .env.local do ETL..."
if [ ! -f "$ENV_LOCAL" ]; then
  warn ".env.local não encontrado — criando a partir do template com credenciais padrão..."
  cp "$ENV_EXAMPLE" "$ENV_LOCAL"
  echo "      Criado: $ENV_LOCAL"
  echo "      Verifique se as credenciais estão corretas antes de continuar:"
  echo "      nano $ENV_LOCAL"
else
  echo "      .env.local encontrado."
fi

# ---------------------------------------------------------------------------
# 2. Criar rede Docker sme-identidade
# ---------------------------------------------------------------------------
info "[2/5] Criando rede Docker sme-identidade..."
docker network create sme-identidade 2>/dev/null \
  && echo "      Rede criada." \
  || echo "      Rede já existia (ok)."

# ---------------------------------------------------------------------------
# 3. Subir Keycloak local (pula se já houver Keycloak respondendo na 8080)
# ---------------------------------------------------------------------------
info "[3/5] Verificando Keycloak..."
if curl -sf http://localhost:8080/health/ready --max-time 5 > /dev/null 2>&1; then
  echo "      Keycloak já está rodando em http://localhost:8080 — pulando."
else
  echo "      Iniciando Keycloak local (pode levar ~90s na primeira vez)..."
  docker compose -f "$INFRA_COMPOSE" up -d --wait \
    || { warn "--wait não suportado, aguardando 90s..."; docker compose -f "$INFRA_COMPOSE" up -d; sleep 90; }
  echo "      Keycloak pronto → http://localhost:8080 (admin / admin)"
fi

# ---------------------------------------------------------------------------
# 3.5 Garantir rede identidade-net e PgBouncer (main compose)
# ---------------------------------------------------------------------------
info "[3.5/5] Verificando rede identidade-net e PgBouncer..."
MAIN_COMPOSE="$REPO_ROOT/docker-compose.yml"

# Se a rede api_identidade-net existir sem labels do Compose (criada manualmente), remover
NET_LABEL=$(docker network inspect api_identidade-net \
  --format "{{index .Labels \"com.docker.compose.network\"}}" 2>/dev/null || echo "absent")
if docker network ls --format "{{.Name}}" | grep -q "^api_identidade-net$" && [ -z "$NET_LABEL" ]; then
  echo "      Rede api_identidade-net sem labels — removendo para o Compose recriar corretamente..."
  docker network rm api_identidade-net 2>/dev/null || true
fi

# Garante PgBouncer + postgres-etl via main compose (cria api_identidade-net com labels corretas)
if ! docker inspect identidade-pgbouncer > /dev/null 2>&1; then
  echo "      PgBouncer não está rodando — iniciando via main compose..."
  docker compose -f "$MAIN_COMPOSE" up -d postgres-etl pgbouncer \
    || error "Falha ao iniciar PgBouncer. Verifique: $MAIN_COMPOSE"
  echo "      PgBouncer e postgres-etl iniciados."
else
  echo "      PgBouncer já está rodando."
fi
echo "      Rede api_identidade-net ok."

# ---------------------------------------------------------------------------
# 4. Subir ETL-MS (api + worker + beat)
# ---------------------------------------------------------------------------
info "[4/5] Buildando e subindo ETL-MS (api + worker + beat)..."
docker compose -f "$ETL_COMPOSE" up -d --build 2>&1

# Aguarda apenas etl-api ficar healthy (worker/beat têm healthcheck desabilitado)
# start_period=60s + até 5 checks × 15s = máx ~135s
echo "      Aguardando API ficar healthy (pode levar até 2min)..."
READY=false
for i in $(seq 1 30); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' local-etl-api 2>/dev/null || echo "missing")
  case "$STATUS" in
    healthy)
      echo "      API healthy após $((i*5))s"
      READY=true
      break
      ;;
    exited|dead)
      error "Container local-etl-api encerrou inesperadamente. Verifique: docker logs local-etl-api"
      ;;
  esac
  echo "      Aguardando... ($((i*5))s) status=$STATUS"
  sleep 5
done
[ "$READY" = false ] && warn "API ainda não está healthy após 150s — verifique: docker logs local-etl-api"

# ---------------------------------------------------------------------------
# 5. Smoke tests
# ---------------------------------------------------------------------------
info "[5/5] Executando smoke tests..."
PASS=0; FAIL=0

smoke() {
  local label="$1" url="$2" expected="$3"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
  if [ "$code" = "$expected" ]; then
    echo -e "      ${GREEN}[OK]${NC}   $label → $code"
    PASS=$((PASS+1))
  else
    echo -e "      ${RED}[FAIL]${NC} $label → esperado $expected, recebido $code"
    FAIL=$((FAIL+1))
  fi
}

smoke "ETL health"      "http://localhost:8001/api/health/"        "200"
smoke "ETL docs"        "http://localhost:8001/api/docs/"          "200"
smoke "Keycloak health" "http://localhost:8080/health/ready"       "200"

echo ""
echo "      Resultado: ${PASS} OK / ${FAIL} falha(s)"

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  ETL-MS LOCAL PRONTO${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "  API       →  http://localhost:8001"
echo "  Swagger   →  http://localhost:8001/api/docs/"
echo "  Health    →  http://localhost:8001/api/health/"
echo "  Keycloak  →  http://localhost:8080  (admin / admin)"
echo "  Postgres  →  localhost:5434  (etl / etl / etl_db)"
echo "  KeyDB     →  localhost:6382"
echo ""
echo "  Logs API:    docker logs -f local-etl-api"
echo "  Logs Worker: docker logs -f local-etl-worker"
echo "  Parar tudo:  docker compose -f $ETL_COMPOSE down"
echo ""
