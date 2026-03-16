#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:11888}"
MODEL="${2:-openclaw-fast}"

echo "[1/3] GET /v1/models"
curl -sS "$BASE_URL/v1/models" | head -c 600 | cat
printf "\n\n"

echo "[2/3] POST /v1/chat/completions (non-stream)"
curl -sS "$BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":\"reply with: ok\"}]}" | head -c 800 | cat
printf "\n\n"

echo "[3/3] POST /v1/chat/completions (stream)"
curl -sSN "$BASE_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"count 1 to 3\"}]}" | head -n 20 | cat
printf "\n"

echo "Smoke checks finished."
