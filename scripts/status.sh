#!/usr/bin/env bash
# remolt status — quick overview of the cluster state
set -uo pipefail

NS="${REMOLT_NAMESPACE:-remolt}"
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
RESET='\033[0m'

echo -e "${BOLD}remolt status${RESET}"
echo

# Server
echo -e "${BOLD}Server${RESET}"
kubectl -n "$NS" get pods -l app=remolt-server --no-headers 2>/dev/null | while read -r name ready status restarts age; do
  color=$GREEN
  [[ "$ready" != "1/1" ]] && color=$YELLOW
  [[ "$status" == *"Error"* || "$status" == *"Crash"* ]] && color=$RED
  echo -e "  ${color}${status}${RESET}  ${name}  ${DIM}${ready} ready, ${age} old, ${restarts} restarts${RESET}"
done
echo

# Health
SERVER_POD=$(kubectl -n "$NS" get pods -l app=remolt-server -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [[ -n "$SERVER_POD" ]]; then
  HEALTH=$(kubectl -n "$NS" exec "$SERVER_POD" -- curl -s http://localhost:8080/health 2>/dev/null || echo '{}')
  SESSIONS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sessions',0))" 2>/dev/null || echo "?")
  echo -e "${BOLD}Sessions${RESET}  ${SESSIONS} active"
  echo
fi

# Sandbox pods
echo -e "${BOLD}Sandbox Pods${RESET}"
SANDBOX_PODS=$(kubectl -n "$NS" get pods -l remolt.managed=true --no-headers 2>/dev/null)
if [[ -z "$SANDBOX_PODS" ]]; then
  echo -e "  ${DIM}(none)${RESET}"
else
  WARM=0
  SESSION=0
  OTHER=0
  while read -r name ready status restarts age; do
    if [[ "$name" == remolt-warm-* ]]; then
      ((WARM++))
      echo -e "  ${DIM}warm${RESET}    ${name}  ${DIM}${status}, ${age}${RESET}"
    else
      ((SESSION++))
      SID=$(kubectl -n "$NS" get pod "$name" -o jsonpath='{.metadata.labels.remolt\.session-id}' 2>/dev/null)
      echo -e "  ${GREEN}session${RESET} ${name}  ${DIM}${status}, ${age}, sid=${SID:0:12}...${RESET}"
    fi
  done <<< "$SANDBOX_PODS"
  echo
  echo -e "  ${SESSION} session pod(s), ${WARM} warm pod(s)"
fi
echo

# Tunnel
echo -e "${BOLD}Tunnel${RESET}"
kubectl -n "$NS" get pods -l app=cloudflared --no-headers 2>/dev/null | while read -r name ready status restarts age; do
  color=$GREEN
  [[ "$ready" != "1/1" ]] && color=$YELLOW
  echo -e "  ${color}${status}${RESET}  ${name}  ${DIM}${age}${RESET}"
done
echo

# Recent events
echo -e "${BOLD}Recent Events${RESET} ${DIM}(last 5)${RESET}"
if [[ -n "$SERVER_POD" ]]; then
  kubectl -n "$NS" logs "$SERVER_POD" --tail=200 2>/dev/null \
    | grep '"event"' 2>/dev/null \
    | tail -5 \
    | while read -r line; do
        EVENT=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('event','?'))" 2>/dev/null || echo "?")
        echo -e "  ${DIM}${line}${RESET}"
      done
fi
echo
