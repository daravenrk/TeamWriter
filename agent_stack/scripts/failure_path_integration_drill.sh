#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-http://127.0.0.1:11888}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-900}"
POLL_SECONDS="${POLL_SECONDS:-3}"
RESTART_CMD="${RESTART_CMD:-docker compose -f /home/daravenrk/dragonlair/agent_stack/docker-compose.agent.yml restart}"
PYTHONPATH_ROOT="${PYTHONPATH_ROOT:-/home/daravenrk/dragonlair}"
INTERRUPTION_DRILL="/home/daravenrk/dragonlair/agent_stack/scripts/interruption_recovery_drill.sh"
FALLBACK_DRILL="/home/daravenrk/dragonlair/agent_stack/scripts/fallback_integrity_drill.py"

log() {
  printf '[failure-path] %s\n' "$*" >&2
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
  python3 -c '
import json
import sys

expr = sys.argv[1]
data = json.load(sys.stdin)
value = data
for part in expr.split("."):
  if not part:
    continue
  if isinstance(value, dict):
    value = value.get(part)
  else:
    value = None
    break
if isinstance(value, (dict, list)):
  print(json.dumps(value))
elif value is None:
  print("")
else:
  print(value)
' "$expr"
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

list_active_tasks() {
  curl -fsS "$API_BASE/api/status" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
for task in data.get("tasks", []):
    if task.get("status") in {"queued", "running", "paused"}:
        print("{}|{}|{}|{}".format(task.get("id"), task.get("status"), task.get("profile"), task.get("prompt")))
'
}

require_idle_stack() {
  local active
  active="$(list_active_tasks)"
  if [[ -z "$active" ]]; then
    return 0
  fi

  log "Refusing to run failure-path drill while other tasks are active; the drill restarts the API service."
  printf '%s\n' "$active" >&2
  return 1
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

    if [[ "$state" == "failed" || "$state" == "cancelled" || "$state" == "completed" ]]; then
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

wait_for_book_run_dir() {
  local task_id="$1"
  local deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))
  while true; do
    local payload
    payload="$(curl -fsS "$API_BASE/api/tasks/$task_id/production-status")"
    local run_dir
    run_dir="$(printf '%s' "$payload" | json_get run_dir)"
    if [[ -n "$run_dir" && "$run_dir" != "null" ]]; then
      printf '%s' "$run_dir"
      return 0
    fi
    if [[ $(date +%s) -ge $deadline ]]; then
      log "Timed out waiting for run_dir for task=$task_id"
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
}

post_json() {
  local url="$1"
  local payload="$2"
  curl -fsS -X POST "$url" -H 'Content-Type: application/json' -d "$payload"
}

run_interruption_subdrill() {
  log "Subdrill 1/4: interruption recovery"
  API_BASE="$API_BASE" \
  TIMEOUT_SECONDS="$TIMEOUT_SECONDS" \
  POLL_SECONDS="$POLL_SECONDS" \
  RESTART_CMD="$RESTART_CMD" \
  "$INTERRUPTION_DRILL"
}

run_pause_resume_subdrill() {
  log "Subdrill 2/4: pause -> restart -> continue -> reconcile"

  local uniq task_payload create_resp task_id run_dir feedback_resp paused_payload paused_state
  uniq="$(date +%s)"
  task_payload="$(cat <<JSON
{
  "title": "Pause Resume Drill ${uniq}",
  "premise": "Validate persisted review-gate resume after restart.",
  "chapter_number": 1,
  "chapter_title": "Pause Recovery Chapter",
  "section_title": "Pause Recovery Section",
  "section_goal": "Reach review gate and pause for operator action.",
  "max_retries": 1,
  "writer_words": 250,
  "output_dir": "/home/daravenrk/dragonlair/book_project"
}
JSON
)"
  create_resp="$(post_json "$API_BASE/api/book-flow" "$task_payload")"
  task_id="$(printf '%s' "$create_resp" | json_get task_id)"
  if [[ -z "$task_id" ]]; then
    log "Failed to create pause/resume drill task"
    printf '%s\n' "$create_resp"
    exit 1
  fi

  wait_for_task_state "$task_id" "running,paused,completed" >/dev/null
  run_dir="$(wait_for_book_run_dir "$task_id")"
  log "Submitting pause_before_continue feedback for task=$task_id run_dir=$run_dir"
  feedback_resp="$(post_json "$API_BASE/api/book-feedback" "$(cat <<JSON
{
  "task_id": "$task_id",
  "run_dir": "$run_dir",
  "approved": true,
  "needs_rewrite": false,
  "score": 8.5,
  "comment": "Pause for restart-resume validation.",
  "feedback_type": "thumb",
  "issue_tags": [],
  "rewrite_scope": "ask_each_time",
  "pause_before_continue": true,
  "assistant_rewrite_requested": false,
  "reviewer": "failure_path_drill"
}
JSON
)")"
  log "feedback: $feedback_resp"

  paused_payload="$(wait_for_task_state "$task_id" "paused,completed,failed")"
  paused_state="$(printf '%s' "$paused_payload" | json_get status)"
  if [[ "$paused_state" != "paused" ]]; then
    log "Expected paused state before restart, got: $paused_state"
    printf '%s\n' "$paused_payload"
    exit 1
  fi

  log "Restarting service while task remains paused"
  # shellcheck disable=SC2086
  eval "$RESTART_CMD"
  wait_for_api

  local after_restart_payload after_restart_state
  after_restart_payload="$(wait_for_task_state "$task_id" "paused,queued,running,completed")"
  after_restart_state="$(printf '%s' "$after_restart_payload" | json_get status)"
  if [[ "$after_restart_state" != "paused" ]]; then
    log "Expected task to remain paused after restart, got: $after_restart_state"
    printf '%s\n' "$after_restart_payload"
    exit 1
  fi

  log "Submitting continue review action"
  post_json "$API_BASE/api/tasks/$task_id/review-action" '{"action":"continue","note":"resume after restart","reviewer":"failure_path_drill"}' >/dev/null

  log "Triggering reconcile to requeue any orphaned paused record"
  post_json "$API_BASE/api/book-jobs/reconcile" '{}' >/dev/null

  local resumed_payload resumed_state
  resumed_payload="$(wait_for_task_state "$task_id" "queued,running,completed,failed")"
  resumed_state="$(printf '%s' "$resumed_payload" | json_get status)"
  if [[ "$resumed_state" == "failed" ]]; then
    log "Pause/resume subdrill failed after resume request"
    printf '%s\n' "$resumed_payload"
    exit 1
  fi
}

run_cancel_subdrill() {
  log "Subdrill 3/4: queued/running task cancellation"

  local prompt payload task_id final_payload final_state
  prompt="Return 300 short numbered lines labelled cancel drill to keep the route busy."
  payload="$(post_json "$API_BASE/api/tasks" '{"prompt":"'"$prompt"'","profile":"nvidia-fast"}')"
  task_id="$(printf '%s' "$payload" | json_get task_id)"
  if [[ -z "$task_id" ]]; then
    log "Failed to create cancellable task"
    printf '%s\n' "$payload"
    exit 1
  fi

  post_json "$API_BASE/api/tasks/$task_id/cancel" '{}' >/dev/null
  final_payload="$(wait_for_task_state "$task_id" "cancelled,completed,failed")"
  final_state="$(printf '%s' "$final_payload" | json_get status)"
  if [[ "$final_state" != "cancelled" ]]; then
    log "Expected cancelled status, got: $final_state"
    printf '%s\n' "$final_payload"
    exit 1
  fi
}

run_fallback_subdrill() {
  log "Subdrill 4/4: fallback integrity and graceful degradation"
  PYTHONPATH="$PYTHONPATH_ROOT" python3 "$FALLBACK_DRILL"
}

main() {
  wait_for_api
  require_idle_stack
  run_interruption_subdrill
  run_pause_resume_subdrill
  run_cancel_subdrill
  run_fallback_subdrill
  log "failure_path_integration_drill: PASS"
}

main "$@"