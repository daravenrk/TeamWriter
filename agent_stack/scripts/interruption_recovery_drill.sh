#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-http://127.0.0.1:11888}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-420}"
POLL_SECONDS="${POLL_SECONDS:-3}"
RESTART_CMD="${RESTART_CMD:-docker compose -f /home/daravenrk/dragonlair/agent_stack/docker-compose.agent.yml restart}"
SKIP_INTERRUPT="${SKIP_INTERRUPT:-0}"

BOOK_TITLE="${BOOK_TITLE:-Interruption Drill Book}"
PREMISE="${PREMISE:-Validate recovery after service interruption.}"
CHAPTER_TITLE="${CHAPTER_TITLE:-Recovery Drill Chapter}"
SECTION_TITLE="${SECTION_TITLE:-Interruption Path}"
SECTION_GOAL="${SECTION_GOAL:-Exercise interruption detection and reconcile resume behavior.}"

log() {
  printf '[drill] %s\n' "$*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  }
}

require_cmd curl
require_cmd python3

json_get() {
  local expr="$1"
  python3 - "$expr" <<'PY'
import json,sys
expr=sys.argv[1]
data=json.load(sys.stdin)
value=data
for part in expr.split('.'):
    if not part:
        continue
    if isinstance(value, dict):
        value=value.get(part)
    else:
        value=None
        break
if isinstance(value,(dict,list)):
    import json as _json
    print(_json.dumps(value))
elif value is None:
    print("")
else:
    print(value)
PY
}

wait_for_api() {
  local deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while true; do
    if curl -fsS "$API_BASE/api/health" >/dev/null 2>&1; then
      return 0
    fi
    if [[ $(date +%s) -ge $deadline ]]; then
      log "Timed out waiting for API at $API_BASE"
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
}

wait_for_task_state() {
  local task_id="$1"
  local wanted_csv="$2"
  local deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while true; do
    local payload
    payload="$(curl -fsS "$API_BASE/api/tasks/$task_id")"
    local state
    state="$(printf '%s' "$payload" | json_get status)"
    log "task=$task_id state=$state"

    IFS=',' read -r -a wanted <<<"$wanted_csv"
    for target in "${wanted[@]}"; do
      if [[ "$state" == "$target" ]]; then
        printf '%s' "$payload"
        return 0
      fi
    done

    if [[ "$state" == "failed" || "$state" == "cancelled" ]]; then
      log "Task entered terminal non-success state: $state"
      printf '%s' "$payload"
      return 0
    fi

    if [[ $(date +%s) -ge $deadline ]]; then
      log "Timed out waiting for task state in [$wanted_csv]"
      printf '%s' "$payload"
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
}

main() {
  log "Waiting for API readiness"
  wait_for_api

  local create_payload
  create_payload="$(cat <<JSON
{
  "title": "$BOOK_TITLE",
  "premise": "$PREMISE",
  "chapter_number": 1,
  "chapter_title": "$CHAPTER_TITLE",
  "section_title": "$SECTION_TITLE",
  "section_goal": "$SECTION_GOAL",
  "max_retries": 1,
  "writer_words": 350,
  "output_dir": "/home/daravenrk/dragonlair/book_project"
}
JSON
)"

  log "Creating book-flow task"
  local create_resp
  create_resp="$(curl -fsS -X POST "$API_BASE/api/book-flow" -H 'Content-Type: application/json' -d "$create_payload")"
  local task_id
  task_id="$(printf '%s' "$create_resp" | json_get task_id)"
  if [[ -z "$task_id" ]]; then
    log "Failed to parse task_id from response"
    printf '%s\n' "$create_resp"
    exit 1
  fi
  log "Created task_id=$task_id"

  log "Waiting for task to enter running state"
  wait_for_task_state "$task_id" "running,completed"

  if [[ "$SKIP_INTERRUPT" != "1" ]]; then
    log "Interrupting service with restart command"
    # shellcheck disable=SC2086
    eval "$RESTART_CMD"
    log "Waiting for API after interruption"
    wait_for_api
  else
    log "SKIP_INTERRUPT=1, skipping service restart"
  fi

  log "Triggering reconcile"
  local reconcile_resp
  reconcile_resp="$(curl -fsS -X POST "$API_BASE/api/book-jobs/reconcile")"
  log "reconcile: $reconcile_resp"

  log "Waiting for task to resume or finish"
  local final_payload
  final_payload="$(wait_for_task_state "$task_id" "queued,running,completed")"

  local final_state final_error
  final_state="$(printf '%s' "$final_payload" | json_get status)"
  final_error="$(printf '%s' "$final_payload" | json_get error)"

  log "Final task state: $final_state"
  if [[ -n "$final_error" ]]; then
    log "Final task error: $final_error"
  fi

  log "Drill complete"
}

main "$@"
