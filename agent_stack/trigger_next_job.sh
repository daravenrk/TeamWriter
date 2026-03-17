#!/bin/bash
set -euo pipefail

# Reconcile stuck book jobs through the running API server.
BASE_URL="${AGENT_API_BASE_URL:-http://127.0.0.1:11888}"
NVIDIA_URL="${OLLAMA_NVIDIA_URL:-http://127.0.0.1:11434}"
AMD_URL="${OLLAMA_AMD_URL:-http://127.0.0.1:11435}"
LAST_MODE_FILE="${DRAGONLAIR_LAST_MODE_FILE:-/opt/ai-stack/active_mode.env}"
APPLY_LAST_MODE_SCRIPT="${DRAGONLAIR_APPLY_LAST_MODE_SCRIPT:-/opt/ai-stack/modes/apply_last_mode.sh}"
STARTUP_GRACE_SECONDS="${DRAGONLAIR_CRON_STARTUP_GRACE_SECONDS:-600}"
FORCE_STARTUP_BYPASS="${DRAGONLAIR_CRON_BYPASS_STARTUP_GRACE:-0}"

log() {
	echo "[trigger_next_job] $*"
}

is_api_healthy() {
	curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1
}

is_ollama_healthy() {
	local url="$1"
	curl -fsS "$url/api/tags" >/dev/null 2>&1
}

host_uptime_seconds() {
	awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 999999
}

in_startup_grace_window() {
	if [[ "$FORCE_STARTUP_BYPASS" == "1" ]]; then
		return 1
	fi

	local up
	up="$(host_uptime_seconds)"
	[[ "$up" -lt "$STARTUP_GRACE_SECONDS" ]]
}

running_tasks_count() {
	local status_json
	if ! is_api_healthy; then
		echo 0
		return 0
	fi

	status_json="$(curl -fsS "$BASE_URL/api/status" 2>/dev/null || true)"
STATUS_JSON="$status_json" python3 - <<'PY'
import json, os
try:
    data = json.loads(os.environ.get("STATUS_JSON") or "{}")
    counts = data.get("task_counts") or {}
    print(int(counts.get("running", 0) or 0))
except Exception:
    print(0)
PY
}

recover_stack_if_idle() {
	if in_startup_grace_window; then
		log "Startup grace active (uptime=$(host_uptime_seconds)s < ${STARTUP_GRACE_SECONDS}s); skipping cron remediation this pass."
		return 0
	fi

	local running
	running="$(running_tasks_count)"
	if [[ "$running" -gt 0 ]]; then
		log "Active processing detected (running=$running); skipping restart/remediation."
		return 0
	fi

	if is_api_healthy && is_ollama_healthy "$NVIDIA_URL" && is_ollama_healthy "$AMD_URL"; then
		return 0
	fi

	log "Service health check failed while idle; attempting non-disruptive recovery."
	docker start ollama_nvidia ollama_amd dragonlair_agent_stack fetcher >/dev/null 2>&1 || true
	docker compose -f /opt/ai-stack/docker-compose.yml up -d >/dev/null 2>&1 || true
	docker compose -f /home/daravenrk/dragonlair/agent_stack/docker-compose.agent.yml up -d >/dev/null 2>&1 || true

	if [[ -x "$APPLY_LAST_MODE_SCRIPT" && -f "$LAST_MODE_FILE" ]]; then
		if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
			log "Re-applying persisted mode from $(basename "$LAST_MODE_FILE") after recovery."
			sudo -n "$APPLY_LAST_MODE_SCRIPT" >/dev/null 2>&1 || true
		else
			log "Persisted mode file found but sudo is non-interactive unavailable; boot service remains source of truth."
		fi
	fi
}

recover_stack_if_idle

if in_startup_grace_window; then
	log "Startup grace active; skipping reconcile this pass."
	exit 0
fi

for _ in 1 2 3 4 5; do
	if is_api_healthy; then
		break
	fi
	sleep 2
done

if ! is_api_healthy; then
	log "API is still unreachable; skipping reconcile pass."
	exit 0
fi

# Best-effort hung recovery first; ignore 409 when normal running work exists.
running="$(running_tasks_count)"
if [[ "$running" -eq 0 ]]; then
	curl -fsS -X POST "$BASE_URL/api/recover-hung" \
		-H 'Content-Type: application/json' \
		-d '{"force":false}' >/dev/null || true
else
	log "Skipping recover-hung because running tasks exist (running=$running)."
fi

# Resume eligible book-flow tasks that are failed/queued and not on hold.
curl -fsS -X POST "$BASE_URL/api/book-jobs/reconcile" >/dev/null || true
