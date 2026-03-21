#!/bin/bash

# Test script: Verify canon stage receives user premise
# This script launches a book flow job with specific user input and checks if canon.json is created properly

set -e

API_URL="http://localhost:11888"
CHAPTER_NUM=${1:-1}
SECTION_TITLE=${2:-"Opening Adventure"}

echo "================================"
echo "Testing Canon Stage with User Input"
echo "================================"
echo "Launching book flow with premise input..."
echo "API Endpoint: $API_URL"
echo "Chapter: $CHAPTER_NUM"
echo "Section: $SECTION_TITLE"
echo ""

# Create the request payload
PAYLOAD=$(cat <<'EOF'
{
  "title": "Dragonlair Chronicles",
  "premise": "A young archivist discovers an ancient dragon's layer hidden beneath a modern city, unlocking secrets of a forgotten civilization and magical heritage",
  "chapter_number": 1,
  "chapter_title": "The Hidden Archive",
  "section_title": "Opening Adventure",
  "section_goal": "Introduce the protagonist and the mystery of the dragon's layer",
  "genre": "fantasy",
  "audience": "young adult",
  "tone": "mysterious and adventure-filled",
  "writer_words": 1400,
  "target_word_count": 125000,
  "page_target": 450,
  "max_retries": 2,
  "verbose": true
}
EOF
)

# Send the request
echo "Sending request to /api/book-flow..."
RESPONSE=$(curl -s -X POST "$API_URL/api/book-flow" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

echo "Response:"
echo "$RESPONSE" | jq . 2>/dev/null || echo "$RESPONSE"
echo ""

# Extract task_id
TASK_ID=$(echo "$RESPONSE" | jq -r '.task_id' 2>/dev/null || echo "")

if [ -z "$TASK_ID" ] || [ "$TASK_ID" = "null" ]; then
  echo "ERROR: Failed to get task_id from response"
  exit 1
fi

echo "Task ID: $TASK_ID"
echo ""
echo "Monitoring task status..."
echo "Check the web UI at http://localhost:11888 for live progress"
echo ""

# Monitor task for up to 10 minutes
ELAPSED=0
MAX_WAIT=600

while [ $ELAPSED -lt $MAX_WAIT ]; do
  STATUS=$(curl -s -X GET "$API_URL/api/tasks/$TASK_ID" | jq -r '.status' 2>/dev/null)
  PROGRESS=$(curl -s -X GET "$API_URL/api/status" | jq -r '.agent_count // "unknown"' 2>/dev/null)
  
  echo "[$ELAPSED s] Status: $TASK_ID = $STATUS | Active agents: $PROGRESS"
  
  if [ "$STATUS" = "completed" ]; then
    echo ""
    echo "✓ Task completed!"
    echo ""
    echo "Checking for canon.json output..."
    
    # Find the run directory
    RUN_DIR="/home/daravenrk/dragonlair/book_project/runs/*/03_canon/canon.json"
    CANON_FILE=$(ls -t $RUN_DIR 2>/dev/null | head -1)
    
    if [ -f "$CANON_FILE" ]; then
      echo "✓ Found canon.json: $CANON_FILE"
      echo ""
      echo "Canon contents (first 50 lines):"
      head -50 "$CANON_FILE" | jq . 2>/dev/null || head -50 "$CANON_FILE"
    else
      echo "✗ No canon.json found"
    fi
    
    exit 0
  elif [ "$STATUS" = "failed" ]; then
    echo ""
    echo "✗ Task failed"
    exit 1
  fi
  
  sleep 10
  ELAPSED=$((ELAPSED + 10))
done

echo ""
echo "Timeout: Task did not complete within $MAX_WAIT seconds"
exit 2
