#!/bin/bash
set -euo pipefail

# Reconcile stuck book jobs through the running API server.
BASE_URL="${AGENT_API_BASE_URL:-http://127.0.0.1:11888}"
WEBUI_EVENTS="/home/daravenrk/dragonlair/book_project/webui_events.jsonl"

for _ in 1 2 3 4 5; do
	if curl -fsS "$BASE_URL/api/health" >/dev/null 2>&1; then
		break
	fi
	sleep 2
done

# Best-effort hung recovery first; ignore 409 when normal running work exists.
curl -fsS -X POST "$BASE_URL/api/recover-hung" \
	-H 'Content-Type: application/json' \
	-d '{"force":false}' >/dev/null || true

# Resume eligible book-flow tasks that are failed/queued and not on hold.
curl -fsS -X POST "$BASE_URL/api/book-jobs/reconcile" >/dev/null || true

# Check for running objectives (tasks)
RUNNING=$(curl -fsS "$BASE_URL/api/status" | grep -o '"running": *[0-9]*' | grep -o '[0-9]*')
if [[ "$RUNNING" == "0" ]]; then
	# Write a joke to the webui chat events
	JOKE="[AI] Why did the IT guy and the writer get along so well? They both spend hours debugging characters."
	TS=$(date +%s)
	printf '{"ts": %s, "event": "chat_message", "payload": {"role": "assistant", "text": "%s"}}\n' "$TS" "$JOKE" >> "$WEBUI_EVENTS"
fi
