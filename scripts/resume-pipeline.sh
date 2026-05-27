#!/usr/bin/env bash
# =============================================================================
# resume-pipeline.sh — Retoma um pipeline ETL a partir do step onde parou
#
# Identifica automaticamente o step travado/falho e re-executa a partir daí,
# continuando os steps seguintes até o audit final.
#
# Uso:
#   ./scripts/resume-pipeline.sh                       # retoma última execução parada
#   ./scripts/resume-pipeline.sh --id <execution-id>   # execução específica
#   ./scripts/resume-pipeline.sh --dry-run             # mostra o que faria sem executar
#   ./scripts/resume-pipeline.sh --timeout 7200        # timeout por step em segundos (padrão 3600)
#   ./scripts/resume-pipeline.sh --from crossref_dedup # força início a partir de step específico
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
API_BASE="http://localhost:8001"
INTERNAL_TOKEN="${ETL_INTERNAL_TOKEN:-dev-etl-token}"
EXECUTION_ID=""
DRY_RUN=false
STEP_TIMEOUT=3600
POLL_INTERVAL=10
FORCE_FROM=""

# ---------------------------------------------------------------------------
# Parse de argumentos
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base) API_BASE="$2";         shift 2 ;;
    --token)    INTERNAL_TOKEN="$2";   shift 2 ;;
    --id)       EXECUTION_ID="$2";     shift 2 ;;
    --timeout)  STEP_TIMEOUT="$2";     shift 2 ;;
    --from)     FORCE_FROM="$2";       shift 2 ;;
    --dry-run)  DRY_RUN=true;          shift ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# //'
      exit 0
      ;;
    *)
      echo "Opção desconhecida: $1  (use --help)" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Cores e helpers
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()      { echo -e "${GREEN}>>>${NC} $*"; }
warn()      { echo -e "${YELLOW}AVISO:${NC} $*"; }
error()     { echo -e "${RED}ERRO:${NC} $*" >&2; exit 1; }
detail()    { echo -e "    ${CYAN}$*${NC}"; }
step_ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
step_ko()   { echo -e "  ${RED}✗${NC} $*"; }
step_wait() { echo -e "  ${YELLOW}⏳${NC} $*"; }
step_skip() { echo -e "  ${CYAN}»${NC} $*"; }
step_none() { echo -e "  ○  $*"; }

# ---------------------------------------------------------------------------
# Verifica dependências
# ---------------------------------------------------------------------------
command -v curl >/dev/null 2>&1 || error "curl não encontrado"
command -v jq   >/dev/null 2>&1 || error "jq não encontrado (obrigatório para este script)"

# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------
api_get() {
  # Usa -s (silent) sem -f para não matar o script em 4xx/5xx;
  # chama curl com --max-time para evitar hang indefinido
  curl -s --max-time 30 \
    -H "X-Internal-Token: $INTERNAL_TOKEN" \
    -H "Content-Type: application/json" \
    "$@"
}

api_get_checked() {
  # Versão que verifica HTTP code e sai com erro em 4xx/5xx
  local url="$1"
  local resp http_code body
  resp=$(curl -s --max-time 30 -w "\n%{http_code}" \
    -H "X-Internal-Token: $INTERNAL_TOKEN" \
    -H "Content-Type: application/json" \
    "$url")
  http_code=$(echo "$resp" | tail -1)
  body=$(echo "$resp" | head -n -1)
  if [[ "$http_code" != "200" ]]; then
    echo -e "${RED}ERRO:${NC} GET $url retornou HTTP $http_code" >&2
    echo "    Resposta: $body" >&2
    exit 1
  fi
  echo "$body"
}

api_post_raw() {
  local url="$1"; shift
  curl -s --max-time 30 -w "\n%{http_code}" -X POST \
    -H "X-Internal-Token: $INTERNAL_TOKEN" \
    -H "Content-Type: application/json" \
    "$@" "$url"
}

# ---------------------------------------------------------------------------
# Verifica se a API está respondendo
# ---------------------------------------------------------------------------
info "Verificando API em $API_BASE ..."
if ! curl -sf "${API_BASE}/api/health/" -o /dev/null; then
  error "API não está respondendo em ${API_BASE}/api/health/ — suba o container primeiro."
fi

# ---------------------------------------------------------------------------
# Resolve execução alvo
# ---------------------------------------------------------------------------
if [[ -z "$EXECUTION_ID" ]]; then
  info "Buscando última execução não finalizada com sucesso..."
  # Tenta encontrar a mais recente que não seja 'success'
  EXECUTION_ID=$(api_get "${API_BASE}/api/etl/executions/?ordering=-created_at&limit=10" \
    | jq -r '[.results[]? | select(.status != "success")] | first | .id // empty')

  if [[ -z "$EXECUTION_ID" ]]; then
    # Todas concluídas com sucesso — pega a última para informar
    EXECUTION_ID=$(api_get "${API_BASE}/api/etl/executions/?ordering=-created_at&limit=1" \
      | jq -r '.results[0].id // empty')
    [[ -z "$EXECUTION_ID" ]] && error "Nenhuma execução encontrada."
    EXEC_STATUS=$(api_get "${API_BASE}/api/etl/executions/${EXECUTION_ID}/" | jq -r '.status')
    info "Última execução ($EXECUTION_ID) está com status '$EXEC_STATUS'. Nada a retomar."
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Carrega estado completo da execução
# ---------------------------------------------------------------------------
EXEC_DATA=$(api_get_checked "${API_BASE}/api/etl/executions/${EXECUTION_ID}/")
EXEC_STATUS=$(echo "$EXEC_DATA"      | jq -r '.status')
EXEC_EXTRACTED=$(echo "$EXEC_DATA"   | jq -r '.total_extracted   // 0')
EXEC_TRANSFORMED=$(echo "$EXEC_DATA" | jq -r '.total_transformed // 0')
EXEC_LOADED=$(echo "$EXEC_DATA"      | jq -r '.total_loaded      // 0')
EXEC_STARTED=$(echo "$EXEC_DATA"     | jq -r '.started_at        // "—"')
EXEC_UPDATED=$(echo "$EXEC_DATA"     | jq -r '.updated_at        // "—"')

echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD} Execução alvo${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
detail "ID:          $EXECUTION_ID"
detail "Status:      $EXEC_STATUS"
detail "Iniciada:    $EXEC_STARTED"
detail "Atualizada:  $EXEC_UPDATED"
detail "Extraídos:   $EXEC_EXTRACTED"
detail "Transform:   $EXEC_TRANSFORMED"
detail "Carregados:  $EXEC_LOADED"
echo ""

if [[ "$EXEC_STATUS" == "success" ]]; then
  info "Execução já finalizada com sucesso. Nada a retomar."
  exit 0
fi

# ---------------------------------------------------------------------------
# Analisa status de cada step
# ---------------------------------------------------------------------------
get_step_status() {
  # Retorna o status do step mais recente com aquele nome
  echo "$EXEC_DATA" | jq -r --arg s "$1" \
    '[.steps[]? | select(.step_name == $s)] | sort_by(.started_at) | last | .status // "absent"'
}

extract_status() {
  # "success" só se TODOS os extract_* forem success; "failed" se algum falhou; "running"/"absent" caso contrário
  local count
  count=$(echo "$EXEC_DATA" | jq '[.steps[]? | select(.step_name | test("^extract_"))] | length')
  if [[ "$count" -eq 0 ]]; then
    echo "absent"; return
  fi
  echo "$EXEC_DATA" | jq -r \
    '[.steps[]? | select(.step_name | test("^extract_"))] |
     if   all(.[]; .status == "success") then "success"
     elif any(.[]; .status == "failed")  then "failed"
     elif any(.[]; .status == "running") then "running"
     else "partial" end'
}

S_SYNC=$(get_step_status "sync_catalogo")
S_EXTRACT=$(extract_status)
S_STAGING=$(get_step_status "staging")
S_CROSSREF=$(get_step_status "crossref_dedup")
S_DECISION=$(get_step_status "decision")
S_LOAD_TOKEN=$(get_step_status "load_token_ms")
S_AUDIT=$(get_step_status "audit")

echo -e "${BOLD}Estado dos steps:${NC}"
print_step_status() {
  local num="$1" name="$2" sts="$3"
  case "$sts" in
    success) step_ok  "Step $num [$name]: success" ;;
    running) step_wait "Step $num [$name]: running (possivelmente travado)" ;;
    failed)  step_ko  "Step $num [$name]: failed" ;;
    partial) step_wait "Step $num [$name]: parcialmente concluído" ;;
    absent)  step_none "Step $num [$name]: não executado" ;;
    *)       echo -e "  ?  Step $num [$name]: $sts" ;;
  esac
}

print_step_status "0"   "sync_catalogo"  "$S_SYNC"
print_step_status "1-2" "extract_*"      "$S_EXTRACT"
print_step_status "3"   "staging"        "$S_STAGING"
print_step_status "4"   "crossref_dedup" "$S_CROSSREF"
print_step_status "5"   "decision"       "$S_DECISION"
print_step_status "7"   "load_token_ms"  "$S_LOAD_TOKEN"
print_step_status "8"   "audit"          "$S_AUDIT"
echo ""

# ---------------------------------------------------------------------------
# Determina a partir de qual step retomar
# ---------------------------------------------------------------------------
declare -a STEP_ORDER=( "sync_catalogo" "extract" "staging" "crossref_dedup" "decision" "load_token_ms" "audit" )
declare -A STEP_STATUS=(
  [sync_catalogo]="$S_SYNC"
  [extract]="$S_EXTRACT"
  [staging]="$S_STAGING"
  [crossref_dedup]="$S_CROSSREF"
  [decision]="$S_DECISION"
  [load_token_ms]="$S_LOAD_TOKEN"
  [audit]="$S_AUDIT"
)

RESUME_FROM=""

if [[ -n "$FORCE_FROM" ]]; then
  # Usuário especificou --from explicitamente
  # Valida se o step existe na ordem
  for s in "${STEP_ORDER[@]}"; do
    [[ "$s" == "$FORCE_FROM" ]] && RESUME_FROM="$FORCE_FROM" && break
  done
  [[ -z "$RESUME_FROM" ]] && error "Step '$FORCE_FROM' inválido. Válidos: ${STEP_ORDER[*]}"
else
  # Detecta automaticamente o primeiro step que não está success
  for step in "${STEP_ORDER[@]}"; do
    sts="${STEP_STATUS[$step]}"
    if [[ "$sts" != "success" ]]; then
      RESUME_FROM="$step"
      break
    fi
  done
fi

if [[ -z "$RESUME_FROM" ]]; then
  info "Todos os steps já concluíram com sucesso. Nada a retomar."
  exit 0
fi

echo -e "  ${BOLD}Retomando a partir do step: ${CYAN}$RESUME_FROM${NC}"
[[ "$DRY_RUN" == true ]] && echo -e "  ${YELLOW}[DRY-RUN ativado — nenhum endpoint será chamado]${NC}"
echo ""

# ---------------------------------------------------------------------------
# Cancela execução se estiver RUNNING ou PENDING (libera o _guard_step)
# ---------------------------------------------------------------------------
if [[ "$EXEC_STATUS" == "running" || "$EXEC_STATUS" == "pending" ]]; then
  warn "Execução está '$EXEC_STATUS' — cancelando para liberar re-execução dos steps..."
  if [[ "$DRY_RUN" == false ]]; then
    CANCEL_RESP=$(api_post_raw "${API_BASE}/api/etl/executions/${EXECUTION_ID}/cancel/")
    HTTP_CODE=$(echo "$CANCEL_RESP" | tail -1)
    CANCEL_BODY=$(echo "$CANCEL_RESP" | head -n -1)
    if [[ "$HTTP_CODE" == "200" ]]; then
      NEW_STATUS=$(echo "$CANCEL_BODY" | jq -r '.status')
      detail "Status após cancel: $NEW_STATUS"
    else
      warn "Cancel retornou HTTP $HTTP_CODE — continuando mesmo assim..."
      detail "Resposta: $CANCEL_BODY"
    fi
  else
    detail "[DRY-RUN] POST /api/etl/executions/${EXECUTION_ID}/cancel/"
  fi
  echo ""
fi

# ---------------------------------------------------------------------------
# Aguarda step atingir status final (success ou failed)
# ---------------------------------------------------------------------------
wait_for_step() {
  local step_name="$1"
  local elapsed=0

  printf "    Monitorando step [%s]" "$step_name"

  while true; do
    sleep "$POLL_INTERVAL"
    elapsed=$(( elapsed + POLL_INTERVAL ))

    local fresh_data exec_st step_st
    fresh_data=$(api_get "${API_BASE}/api/etl/executions/${EXECUTION_ID}/")
    exec_st=$(echo "$fresh_data" | jq -r '.status')

    if [[ "$step_name" == "extract" ]]; then
      step_st=$(echo "$fresh_data" | jq -r \
        '[.steps[]? | select(.step_name | test("^extract_"))] |
         if   length == 0                           then "absent"
         elif all(.[]; .status == "success")        then "success"
         elif any(.[]; .status == "failed")         then "failed"
         elif any(.[]; .status == "running")        then "running"
         else "partial" end')
    else
      step_st=$(echo "$fresh_data" | jq -r --arg s "$step_name" \
        '[.steps[]? | select(.step_name == $s)] | sort_by(.started_at) | last | .status // "absent"')
    fi

    printf "\r    [%3ds] step=%-16s step_status=%-10s exec_status=%s     " \
      "$elapsed" "$step_name" "$step_st" "$exec_st"

    case "$step_st" in
      success)
        echo ""
        local recs_out
        if [[ "$step_name" == "extract" ]]; then
          recs_out=$(echo "$fresh_data" | jq \
            '[.steps[]? | select(.step_name | test("^extract_")) | .records_out] | add // 0')
        else
          recs_out=$(echo "$fresh_data" | jq -r --arg s "$step_name" \
            '[.steps[]? | select(.step_name == $s)] | sort_by(.started_at) | last | .records_out // 0')
        fi
        step_ok "Step [$step_name] concluído — records_out: $recs_out"
        return 0
        ;;
      failed)
        echo ""
        local err_detail
        err_detail=$(echo "$fresh_data" | jq -r --arg s "$step_name" \
          '[.steps[]? | select(.step_name == $s)] | sort_by(.started_at) | last | .error_detail // "sem detalhe"')
        step_ko "Step [$step_name] falhou"
        detail "Detalhe: $err_detail"
        return 1
        ;;
    esac

    # Execução encerrada externamente antes do step terminar
    if [[ "$exec_st" == "failed" || "$exec_st" == "cancelled" ]]; then
      echo ""
      step_ko "Execução marcada como '$exec_st' enquanto aguardava [$step_name]"
      return 1
    fi

    if [[ $elapsed -ge $STEP_TIMEOUT ]]; then
      echo ""
      warn "Timeout de ${STEP_TIMEOUT}s atingido aguardando step [$step_name]"
      return 2
    fi
  done
}

# ---------------------------------------------------------------------------
# Dispara um step individual e aguarda conclusão
# ---------------------------------------------------------------------------
run_step() {
  local step_key="$1"   # chave interna (sync_catalogo, extract, etc.)
  local endpoint="$2"   # url_path do DRF action
  local body="$3"       # JSON body
  local label="$4"      # descrição amigável

  echo ""
  echo -e "  ${CYAN}▶${NC}  ${BOLD}${label}${NC}"

  if [[ "$DRY_RUN" == true ]]; then
    detail "[DRY-RUN] POST ${API_BASE}/api/etl/executions/${EXECUTION_ID}/${endpoint}/"
    detail "[DRY-RUN] body: $body"
    return 0
  fi

  local resp http_code body_resp task_id
  resp=$(api_post_raw "${API_BASE}/api/etl/executions/${EXECUTION_ID}/${endpoint}/" -d "$body")
  http_code=$(echo "$resp" | tail -1)
  body_resp=$(echo "$resp" | head -n -1)

  if [[ "$http_code" != "202" ]]; then
    step_ko "Falha ao disparar [$label] — HTTP $http_code"
    detail "Resposta: $body_resp"
    return 1
  fi

  task_id=$(echo "$body_resp" | jq -r '.celery_task_id // "n/a"')
  detail "Celery task_id: $task_id"

  wait_for_step "$step_key"
}

# ---------------------------------------------------------------------------
# Executa steps a partir do ponto de retomada
# ---------------------------------------------------------------------------
FOUND_START=false

for step in "${STEP_ORDER[@]}"; do
  [[ "$step" == "$RESUME_FROM" ]] && FOUND_START=true
  [[ "$FOUND_START" == false ]]   && continue

  # Primeiro step a retobar usa force=true para limpar estado travado.
  # Steps seguintes (ainda não executados) usam force=false.
  if [[ "$step" == "$RESUME_FROM" ]]; then
    FORCE="true"
  else
    FORCE="false"
  fi

  case "$step" in
    sync_catalogo)
      run_step "sync_catalogo" "run-sync-catalogo" \
        "{\"force\":$FORCE}" \
        "Step 0 — Sync Catálogo (Sistemas/Perfis KC)" || exit 1
      ;;
    extract)
      run_step "extract" "run-extract" \
        "{\"source\":\"all\",\"force\":$FORCE}" \
        "Steps 1-2 — Extract All (SE1426 + EOL + CoreSSO)" || exit 1
      ;;
    staging)
      run_step "staging" "run-transform" \
        "{\"force\":$FORCE}" \
        "Step 3 — Transform / Staging" || exit 1
      ;;
    crossref_dedup)
      run_step "crossref_dedup" "run-crossref" \
        "{\"force\":$FORCE}" \
        "Step 4 — Crossref / Dedup (3.5M registros — pode demorar ~1h)" || exit 1
      ;;
    decision)
      run_step "decision" "run-decide" \
        "{\"force\":$FORCE}" \
        "Step 5 — Decide Target (create vs update)" || exit 1
      ;;
    load_token_ms)
      run_step "load_token_ms" "run-load-token" \
        "{\"force\":$FORCE}" \
        "Step 7 — Load Token-MS" || exit 1
      ;;
    audit)
      run_step "audit" "run-audit" \
        "{\"force\":$FORCE}" \
        "Step 8 — Audit / Finalizar execução" || exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resumo final
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}══════════════════════════════════════════════${NC}"
echo -e "${BOLD} Resultado final${NC}"
echo -e "${BOLD}══════════════════════════════════════════════${NC}"

FINAL=$(api_get "${API_BASE}/api/etl/executions/${EXECUTION_ID}/")
FINAL_STATUS=$(echo "$FINAL" | jq -r '.status')

echo "$FINAL" | jq '{
  status,
  started_at,
  finished_at,
  total_extracted,
  total_transformed,
  total_loaded,
  total_errors,
  steps: [.steps[]? | {step_name, status, records_in, records_out, records_error}]
}'

echo ""
if [[ "$FINAL_STATUS" == "success" ]]; then
  echo -e "${GREEN}${BOLD}Pipeline concluído com sucesso!${NC}"
  exit 0
else
  warn "Pipeline terminou com status: $FINAL_STATUS"
  exit 1
fi
