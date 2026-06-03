#!/usr/bin/env bash
# =============================================================================
# trigger-pipeline.sh — Dispara o pipeline ETL manualmente para o realm sme-apps
#
# Uso:
#   ./scripts/trigger-pipeline.sh                          # defaults: servidor, 100k, step6=on, step7=off
#   ./scripts/trigger-pipeline.sh --source se1426           # apenas fonte SE1426
#   ./scripts/trigger-pipeline.sh --realm sme-devops        # outro realm
#   ./scripts/trigger-pipeline.sh --all-users               # processa todos os tipos de usuário
#   ./scripts/trigger-pipeline.sh --no-limit                # sem limite de registros
#   ./scripts/trigger-pipeline.sh --max-records 50000       # limite customizado
#   ./scripts/trigger-pipeline.sh --user-types aluno        # apenas alunos
#   ./scripts/trigger-pipeline.sh --load-token-ms           # habilita step 7 (Token-MS)
#   ./scripts/trigger-pipeline.sh --no-load-keycloak        # desabilita step 6
#   ./scripts/trigger-pipeline.sh --watch                   # aguarda conclusão e exibe status
#   ./scripts/trigger-pipeline.sh --no-logs                 # não abre os logs após disparar
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
API_BASE="http://localhost:8001"
SOURCE="all"
REALM="sme-apps"
LOAD_KEYCLOAK="true"       # step 6 habilitado por padrão
LOAD_TOKEN_MS="false"      # step 7 pulado por padrão
MAX_RECORDS_EXTRACT=100000  # limita extração a 100k registros
USER_TYPES="servidor"       # processa apenas servidores por padrão
WATCH=false
FOLLOW_LOGS=true
NOTE="Trigger manual via trigger-pipeline.sh"

# ---------------------------------------------------------------------------
# Parse de argumentos
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base)          API_BASE="$2";                shift 2 ;;
    --source)            SOURCE="$2";                  shift 2 ;;
    --realm)             REALM="$2";                   shift 2 ;;
    --load-keycloak)     LOAD_KEYCLOAK="true";          shift ;;
    --no-load-keycloak)  LOAD_KEYCLOAK="false";         shift ;;
    --load-token-ms)     LOAD_TOKEN_MS="true";          shift ;;
    --no-token-ms)       LOAD_TOKEN_MS="false";         shift ;;
    --max-records)       MAX_RECORDS_EXTRACT="$2";      shift 2 ;;
    --user-types)        USER_TYPES="$2";               shift 2 ;;
    --all-users)         USER_TYPES="all";              shift ;;
    --no-limit)          MAX_RECORDS_EXTRACT="";        shift ;;
    --watch)             WATCH=true;                   shift ;;
    --no-logs)           FOLLOW_LOGS=false;             shift ;;
    --note)              NOTE="$2";                    shift 2 ;;
    -h|--help)
      sed -n '2,9p' "$0" | sed 's/^# //'
      exit 0
      ;;
    *)
      echo "Opção desconhecida: $1  (use --help)" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Cores
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${GREEN}>>>${NC} $*"; }
warn()    { echo -e "${YELLOW}AVISO:${NC} $*"; }
error()   { echo -e "${RED}ERRO:${NC} $*"; exit 1; }
detail()  { echo -e "    ${CYAN}$*${NC}"; }

# ---------------------------------------------------------------------------
# Verifica dependências
# ---------------------------------------------------------------------------
command -v curl  >/dev/null 2>&1 || error "curl não encontrado"
command -v jq    >/dev/null 2>&1 || { warn "jq não instalado — saída sem formatação"; JQ_CMD="cat"; }
JQ_CMD="${JQ_CMD:-jq .}"

# ---------------------------------------------------------------------------
# Verifica se a API está respondendo
# ---------------------------------------------------------------------------
TRIGGER_TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
info "Verificando API em $API_BASE ..."
if ! curl -sf "${API_BASE}/api/health/" -o /dev/null; then
  error "API não está respondendo em ${API_BASE}/api/health/ — suba o container primeiro."
fi

# ---------------------------------------------------------------------------
# Dispara o pipeline
# ---------------------------------------------------------------------------
# Monta payload — max_records_extract é omitido quando vazio (sem limite)
if [[ -n "${MAX_RECORDS_EXTRACT:-}" ]]; then
  PAYLOAD=$(printf '{"source":"%s","target_realm":"%s","load_keycloak":%s,"load_token_ms":%s,"user_types":"%s","max_records_extract":%s,"note":"%s"}' \
    "$SOURCE" "$REALM" "$LOAD_KEYCLOAK" "$LOAD_TOKEN_MS" "$USER_TYPES" "$MAX_RECORDS_EXTRACT" "$NOTE")
else
  PAYLOAD=$(printf '{"source":"%s","target_realm":"%s","load_keycloak":%s,"load_token_ms":%s,"user_types":"%s","note":"%s"}' \
    "$SOURCE" "$REALM" "$LOAD_KEYCLOAK" "$LOAD_TOKEN_MS" "$USER_TYPES" "$NOTE")
fi

info "Disparando pipeline ETL..."
detail "Realm:               $REALM"
detail "Source:              $SOURCE"
detail "User types:          $USER_TYPES"
detail "Max records extract: ${MAX_RECORDS_EXTRACT:-sem limite}"
detail "Step 6 Keycloak:     $LOAD_KEYCLOAK"
detail "Step 7 Token-MS:     $LOAD_TOKEN_MS"
echo ""

RESPONSE=$(curl -sf -X POST \
  "${API_BASE}/api/etl/executions/" \
  -H "Content-Type: application/json" \
  -H "X-Forwarded-User: trigger-pipeline.sh" \
  -H "X-Internal-Token: dev-etl-token" \
  -d "$PAYLOAD") || error "Falha ao chamar a API. Verifique se o container está rodando."

EXECUTION_ID=$(echo "$RESPONSE" | jq -r '.id // empty')
STATUS=$(echo "$RESPONSE" | jq -r '.status // empty')

if [[ -z "$EXECUTION_ID" ]]; then
  echo "Resposta da API:"
  echo "$RESPONSE" | ${JQ_CMD}
  error "Não foi possível extrair o execution_id da resposta."
fi

echo -e "${GREEN}Pipeline disparado com sucesso!${NC}"
detail "Execution ID: $EXECUTION_ID"
detail "Status:       $STATUS"
echo ""
echo "  Acompanhe o log do worker:"
echo "    docker logs -f local-etl-worker"
echo ""
echo "  Ou consulte via API:"
echo "    curl -s ${API_BASE}/api/etl/executions/${EXECUTION_ID}/ | jq ."
echo ""

# ---------------------------------------------------------------------------
# Abre logs do worker (padrão) ou polling via API (--watch)
# ---------------------------------------------------------------------------
if [[ "$WATCH" == true ]]; then
  info "Aguardando conclusão via API (Ctrl+C para cancelar)..."
  POLL_INTERVAL=5
  TIMEOUT_SECS=600
  ELAPSED=0

  while true; do
    sleep "$POLL_INTERVAL"
    ELAPSED=$(( ELAPSED + POLL_INTERVAL ))

    CURRENT=$(curl -sf "${API_BASE}/api/etl/executions/${EXECUTION_ID}/" 2>/dev/null || echo '{}')
    CURRENT_STATUS=$(echo "$CURRENT" | jq -r '.status // "unknown"')

    printf "\r    [%3ds] status: %-12s" "$ELAPSED" "$CURRENT_STATUS"

    case "$CURRENT_STATUS" in
      success)
        echo ""
        echo -e "\n${GREEN}Pipeline concluído com sucesso!${NC}"
        echo "$CURRENT" | jq '{status, started_at, finished_at, steps: [.steps[]? | {step_name, status, records_in, records_out, records_error}]}'
        exit 0
        ;;
      failed|cancelled)
        echo ""
        echo -e "\n${RED}Pipeline terminou com status: $CURRENT_STATUS${NC}"
        echo "$CURRENT" | jq '{status, started_at, finished_at, steps: [.steps[]? | {step_name, status, records_error, metadata}]}'
        exit 1
        ;;
    esac

    if [[ $ELAPSED -ge $TIMEOUT_SECS ]]; then
      echo ""
      warn "Timeout de ${TIMEOUT_SECS}s atingido. Última resposta:"
      echo "$CURRENT" | jq '{status, steps: [.steps[]? | {step_name, status}]}'
      exit 2
    fi
  done
elif [[ "$FOLLOW_LOGS" == true ]]; then
  WORKER_CONTAINER="local-etl-worker"
  if docker ps --format '{{.Names}}' | grep -q "^${WORKER_CONTAINER}$"; then
    info "Abrindo logs do worker (Ctrl+C para sair)..."
    echo ""
    exec docker logs -f --since "$TRIGGER_TS" "$WORKER_CONTAINER"
  else
    warn "Container '$WORKER_CONTAINER' não está rodando — logs não disponíveis."
  fi
fi
