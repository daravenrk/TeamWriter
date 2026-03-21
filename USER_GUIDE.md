# Dragonlair User Guide

## Interaction Philosophy And Control Model

- Minimal user interaction means reduced friction, not reduced control.
- Default behavior is guided and simple so users can move quickly.
- Advanced controls remain available for users who want to inspect, approve, or override decisions.

### What Users Should Expect

- The system should propose options, explain tradeoffs, and ask for approval at key checkpoints.
- Users can choose a fast path (accept defaults) or a detailed path (review strategy, route, model, context).
- Automation remains transparent and reversible (pause/cancel/redirect) when a run is active.

### Assistive Layer Direction

- Dragonlair is evolving toward a conversational assistive layer that mediates user intent and system execution.
- This layer is expected to maintain persistent user memory (preferences, project history, goals, interaction style).
- Long-term trajectory: this assistant-first layer becomes the primary interface model and can expand into broader Ubuntu/Linux workflow mediation.

## System Overview

- **Architecture:** Docker-based, portable across any Linux system with Docker, Docker Compose, and the required GPU stack (NVIDIA CUDA or AMD ROCm).
- **Endpoints:**
  - AMD: `http://127.0.0.1:11435` (container: `ollama_amd`)
  - NVIDIA: `http://127.0.0.1:11434` (container: `ollama_nvidia`)
- **Agent API/UI:** `http://127.0.0.1:11888` (container: `dragonlair_agent_stack`)
- **Fetcher service:** `http://127.0.0.1:11999` (container: `fetcher`)
- **Toolkit:** All scripts and controls are in `/home/daravenrk/dragonlair/bin`
- **Model plans and lists:** `/home/daravenrk/dragonlair/model-sets/`

## Port Matrix + Exposure Policy

| Host port | Service | Bind default | Purpose | Exposure policy |
|---|---|---|---|---|
| `11434` | `ollama_nvidia` | `127.0.0.1` | NVIDIA Ollama route | Local-only (do not expose on LAN/WAN) |
| `11435` | `ollama_amd` | `127.0.0.1` | AMD Ollama route | Local-only (do not expose on LAN/WAN) |
| `11888` | `dragonlair_agent_stack` | `0.0.0.0` | Web UI + API | LAN-optional; restrict in prod |
| `11999` | `fetcher` | `127.0.0.1` | Research fetch service | Local-only (non-public helper service) |

### Mode-specific guidance

- **Dev (single host):** keep defaults; use `127.0.0.1` for Ollama + fetcher, and open `11888` only if needed.
- **LAN mode:** only `11888` should be reachable from other hosts; keep `11434`, `11435`, and `11999` local.
- **Prod mode:** prefer reverse-proxy/TLS in front of `11888`; keep all backend/model ports local-only.

### Firewall baseline

- Allow inbound TCP `11888` only for trusted source ranges when remote access is required.
- Deny inbound TCP `11434`, `11435`, and `11999`.
- Keep SSH (`22`) restricted to trusted admins.

Notes:
- VS Code "Ports" can show auto-forwarded entries that are not currently listening processes.
- Confirm real listeners with:

```sh
ss -ltnp
```

## Control & Usage

### Quick Start

1. Add toolkit to your PATH:
   ```sh
   export PATH="$HOME/dragonlair/bin:$PATH"
   ```
   To persist:
   ```sh
   echo 'export PATH="$HOME/dragonlair/bin:$PATH"' >> ~/.bashrc
   source ~/.bashrc
   ```

2. Preview model pulls (no downloads):
   ```sh
   pull-models --dry-run
   pull-amd --dry-run
   pull-nvidia --dry-run
   ```

3. Pull models:
   ```sh
   pull-models --env amd --file ~/dragonlair/model-sets/amd-coder-14plus-plan.txt
   pull-models --env amd --file ~/dragonlair/model-sets/amd-writing-14plus-plan.txt
   pull-models --env nvidia --file ~/dragonlair/model-sets/nvidia-writing-balanced-plan.txt
   ```

4. Chat/Ask:
   ```sh
   chat-amd
   chat-nvidia
   ask-amd "Explain event loops"
   ask-nvidia "Write a bash function"
   ```

### Planning & Benchmarking

- Model plans are in:
  - `/home/daravenrk/dragonlair/model-sets/amd-coder-14plus-plan.txt`
  - `/home/daravenrk/dragonlair/model-sets/amd-writing-14plus-plan.txt`
  - `/home/daravenrk/dragonlair/model-sets/nvidia-writing-balanced-plan.txt`
- Use `--dry-run` to preview, then run without it to pull.
- Benchmark context ladder: 32768, 49152, 65536 (AMD); 16384, 24576, 32768 (NVIDIA).

### Backup & Restore

- **Backup (no model data):**
  ```sh
  /home/daravenrk/dragonlair/bin/dragonlair_backup_nodata.sh
  ```
  - Backs up configs, scripts, and model lists to `daravenrk@192.168.86.34:/backups/dragonlair`
  - Excludes all model data for lightweight backups.

- **Restore:**
  - Use `rsync` to copy from the backup server to your system:
    ```sh
    rsync -avz daravenrk@192.168.86.34:/backups/dragonlair/opt/ai-stack/ /opt/ai-stack/
    rsync -avz daravenrk@192.168.86.34:/backups/dragonlair/home/daravenrk/dragonlair/ /home/daravenrk/dragonlair/
    ```
  - Reinstall Docker, Docker Compose, and GPU drivers as needed.
  - Pull models as needed using your model lists.

- **Portability:**  
  This system will run on any compatible Linux host with Docker, Compose, and the right GPU stack. Just restore configs/scripts, install dependencies, and pull models.

Preferred stack-level scripts:

```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_backup
/home/daravenrk/dragonlair/bin/dragonlair_stack_restore
```

Include model blobs only when needed:

```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_backup --with-models
/home/daravenrk/dragonlair/bin/dragonlair_stack_restore --with-models
```

Bare-metal backup for hardware-failure recovery:

 /home/daravenrk/dragonlair/bin/dragonlair_metal_backup

Recovery instructions are in:

 - /home/daravenrk/dragonlair/BARE_METAL_RECOVERY.md

## AMD Stack: Available Models

Currently available models on the AMD endpoint (`ollama_amd`):

- deepseek-coder-v2:16b
- starcoder2:15b
- codellama:13b
- dragonlair-active:latest
- dragonlair-coding-amd:latest
- qwen2.5-coder:14b
- dragonlair-book-amd:latest
- qwen3.5:27b

**Planned writing models (from amd-writing-14plus-plan.txt):**
- qwen3.5:27b
- qwen2.5:14b-instruct
- qwen2.5:32b-instruct
- gemma2:27b

**Planned coder models (from amd-coder-14plus-plan.txt):**
- qwen2.5-coder:14b
- codellama:13b
- starcoder2:15b
- deepseek-coder-v2:16b

## Diagrams

### System Architecture

```mermaid
graph TD
  User[User/Client] -->|HTTP API| AMD_Ollama[Ollama AMD (11435)]
  User[User/Client] -->|HTTP API| NVIDIA_Ollama[Ollama NVIDIA (11434)]
  AMD_Ollama -->|Models| AMD_Models[AMD Model Store]
  NVIDIA_Ollama -->|Models| NVIDIA_Models[NVIDIA Model Store]
  User -->|SSH/rsync| BackupServer[Backup Server (192.168.86.34)]
  subgraph Home Toolkit
    BinScripts[dragonlair/bin/*]
    ModelSets[dragonlair/model-sets/*]
    USAGE[USAGE.md]
    MODELPLAN[MODEL_PLAN.md]
  end
  User -->|Shell| BinScripts
  BinScripts -->|Pull/Chat/Ask| AMD_Ollama
  BinScripts -->|Pull/Chat/Ask| NVIDIA_Ollama
```

## Control Flow

1. User runs toolkit scripts (pull, chat, ask) from any shell.
2. Scripts interact with the correct Ollama endpoint/container.
3. Model lists and plans are editable and drive pulls/benchmarking.
4. Backups are performed via rsync to a remote server, excluding model data.
5. Restoration is a reverse rsync, followed by environment setup and model pulls.

## Context Planning Before Requests

Use the planner to estimate required context window before running a full model request:

```sh
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/plan-context \
  --prompt "Write a structured debate on local AI model orchestration and endpoint reliability." \
  --expected-output 900
```

Optional controls:

- `--profile amd-coder|amd-writer|nvidia-fast`
- `--history-tokens <N>`
- `--safety-ratio <float>`

The planner outputs `SUGGESTED_NUM_CTX`, which you can place in profile frontmatter (`num_ctx`) for that agent.

## Dockerized Agent Stack

### Research Agent Update (March 2026)
- The book-researcher agent now uses qwen3.5:14b (128k context window).
- Research agent pulls from news, Wikipedia, and the internet for up-to-date facts.
- Research output is more data-driven and includes structured dossiers and fact cards.

Start backend + frontend:

```sh
/home/daravenrk/dragonlair/bin/agent-stack-up
```

Important deployment note after code changes:

- Changes to Python backend files require rebuild/restart of the API container. Run `agent-stack-up` again to apply them.
- Changes to files under `agent_stack/static/` are bind-mounted and apply on browser refresh.
- Changes to files under `agent_stack/agent_profiles/` are bind-mounted and hot-reloaded.

Open frontend:

- `http://127.0.0.1:11888`
- `http://<HOST_IP>:11888` from external systems on your LAN

Example on this host:

- `http://192.168.86.36:11888`

Stop and logs:

```sh
/home/daravenrk/dragonlair/bin/agent-stack-down
/home/daravenrk/dragonlair/bin/agent-stack-logs
```

Validation commands (post-deploy):

```sh
docker compose -f /home/daravenrk/dragonlair/agent_stack/docker-compose.agent.yml ps
curl -sS http://127.0.0.1:11888/api/health | jq .
curl -sS http://127.0.0.1:11888/api/ui-state | jq '.task_counts, .latest_stage_event, .latest_gate_failure'
python3 -m py_compile /home/daravenrk/dragonlair/agent_stack/api_server.py /home/daravenrk/dragonlair/agent_stack/book_flow.py /home/daravenrk/dragonlair/agent_stack/orchestrator.py
bash /home/daravenrk/dragonlair/agent_stack/scripts/failure_path_integration_drill.sh
```

The failure-path integration drill exercises interruption recovery, paused review-gate resume across restart, task cancellation, and fallback-integrity checks as one repeatable operator validation.

### Writing Feedback + Review Gate Flow

The system now supports human-in-the-loop pause and resume decisions in book mode.

1. Submit writing feedback (UI card or API).
2. If `pause_before_continue=true`, the run enters review-gate wait state after section review.
3. Review the paused section and reviewer output in the Web UI paused review card.
4. Apply one action: `continue`, `rewrite`, or `defer`.

API examples:

```sh
curl -sS -X POST http://127.0.0.1:11888/api/book-feedback \
  -H 'Content-Type: application/json' \
  -d '{
    "task_id":"<task_id>",
    "approved":false,
    "needs_rewrite":true,
    "score":4.0,
    "comment":"Tone mismatch in middle beats.",
    "feedback_type":"thumb",
    "issue_tags":["tone_mismatch"],
    "rewrite_scope":"ask_each_time",
    "pause_before_continue":true,
    "assistant_rewrite_requested":true
  }' | jq .

curl -sS -X POST http://127.0.0.1:11888/api/tasks/<task_id>/review-action \
  -H 'Content-Type: application/json' \
  -d '{"action":"rewrite","note":"tighten tone and preserve canon","reviewer":"operator"}' | jq .

curl -sS 'http://127.0.0.1:11888/api/book-feedback?run_id=<run_id>&chapter_number=1' | jq .
```

### Adaptive Quality Thresholds

Book quality gates now support adaptive thresholds that can tighten over time based on observed rubric performance.

Base floor controls:

- `BOOK_QUALITY_MIN_SCORE`
- `BOOK_QUALITY_MIN_AVG_SCORE`
- `BOOK_QUALITY_MIN_CONTENT_SCORE`

Adaptive controls:

- `BOOK_QUALITY_ADAPTIVE_ENABLED`
- `BOOK_QUALITY_ADAPTIVE_ALPHA`
- `BOOK_QUALITY_ADAPTIVE_GAIN`
- `BOOK_QUALITY_ADAPTIVE_WARMUP_RUNS`
- `BOOK_QUALITY_ADAPTIVE_MAX_SCORE`
- `BOOK_QUALITY_ADAPTIVE_MAX_CONTENT_SCORE`

Run artifacts to inspect:

- `run_journal.jsonl` includes `quality_thresholds_loaded` and `quality_learning_state_updated` events
- `run_summary.json` includes a `quality_thresholds` block with base/effective values and learning snapshot
- Per-book learning state is persisted in `quality_learning_state.json` at the book root

Quick checks:

```sh
docker exec dragonlair_agent_stack env | grep BOOK_QUALITY_

RUN_DIR=/home/daravenrk/dragonlair/book_project/<book-slug>/runs/<run-name>
rg -n 'quality_thresholds_loaded|quality_learning_state_updated' "$RUN_DIR/run_journal.jsonl"
jq '.quality_thresholds' "$RUN_DIR/run_summary.json"
```

CLI status/watch for subagents and task queue:

```sh
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl server-status
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl server-watch --interval 1
```

## API Status Fallback Filters

Use `/api/status` filters to isolate runs that used deterministic fallback artifacts.

Base status payload:

```sh
curl -s "http://127.0.0.1:11888/api/status" | jq '.task_counts, (.tasks | length)'
```

Only tasks with fallback provenance (`used_fallbacks` non-empty):

```sh
curl -s "http://127.0.0.1:11888/api/status?fallback_used=true" | jq '.tasks[] | {id, status, runtime_stage, fallback_provenance_summary}'
```

Only tasks without fallback provenance (`used_fallbacks` empty or missing):

```sh
curl -s "http://127.0.0.1:11888/api/status?fallback_used=false" | jq '.tasks[] | {id, status, runtime_stage}'
```

Only tasks that used a specific fallback stage (current stage example: `canon`):

```sh
curl -s "http://127.0.0.1:11888/api/status?fallback_stage=canon" | jq '.tasks[] | {id, status, runtime_stage, used_fallbacks: .fallback_provenance_summary.used_fallbacks}'
```

Combined filter example (must have fallback + include canon stage):

```sh
curl -s "http://127.0.0.1:11888/api/status?fallback_used=true&fallback_stage=canon" | jq '.tasks[] | {id, status, hold, fallback_integrity_summary, fallback_provenance_summary}'
```

Interpretation notes:

- `fallback_provenance_summary.used_fallbacks` is the authoritative stage list from `run_summary.json`.
- `fallback_integrity_summary.blocked=true` indicates auto-resume was blocked due to integrity failures and needs operator repair/retry.
- Filters apply to the current status task window; older runs may not appear if they are outside the API's returned task list.

### Stage Normalization Policy

The `fallback_stage` filter parameter applies case-insensitive, whitespace-tolerant matching:

- Input is automatically converted to lowercase (e.g., `"CANON"`, `"Canon"` → `"canon"`)
- Leading/trailing whitespace is trimmed (e.g., `"  canon  "` → `"canon"`)
- Internal whitespace is NOT collapsed (e.g., `"can on"` will be rejected as invalid)
- If invalid, the API returns HTTP 400 with a list of valid values

Examples:

```sh
# All of these are equivalent and will return the same results:
curl -s "http://127.0.0.1:11888/api/status?fallback_stage=canon"
curl -s "http://127.0.0.1:11888/api/status?fallback_stage=CANON"
curl -s "http://127.0.0.1:11888/api/status?fallback_stage=Canon"
curl -s "http://127.0.0.1:11888/api/status?fallback_stage=%20canon%20"  # with spaces, URL-encoded

# This will return HTTP 400 error:
curl -s "http://127.0.0.1:11888/api/status?fallback_stage=can+on"
# Response: {"detail": "invalid fallback_stage 'can on'; valid values: canon"}
```

### API Error Contract

The `/api/status` endpoint returns structured error responses for invalid filter parameters.

#### Invalid `fallback_stage` Value

**Request:**
```sh
curl -s "http://127.0.0.1:11888/api/status?fallback_stage=invalid_stage" | jq .
```

**Response (HTTP 400):**
```json
{
  "detail": "invalid fallback_stage 'invalid_stage'; valid values: canon"
}
```

**Troubleshooting:**
- The error message lists all **valid stage names** after "valid values:"
- Copy-paste one of the listed stage names into your next request
- If the list is empty, no fallback stages are currently registered (check `_FALLBACK_STAGE_CONFIGS` in `api_server.py`)

#### Invalid `fallback_used` Value

**Request:**
```sh
curl -s "http://127.0.0.1:11888/api/status?fallback_used=maybe"
```

**Response (HTTP 422 - Validation Error):**
```json
{
  "detail": [
    {
      "type": "bool_parsing",
      "loc": ["query", "fallback_used"],
      "msg": "Input should be a valid boolean",
      "input": "maybe"
    }
  ]
}
```

**Valid values for `fallback_used`:**
- `true` (lowercase)
- `false` (lowercase)
- `1` (interpreted as true)
- `0` (interpreted as false)

#### Malformed Query Combinations

**Invalid: Multiple `fallback_stage` values**
```sh
curl -s "http://127.0.0.1:11888/api/status?fallback_stage=canon&fallback_stage=sections_written"
# Only the FIRST value is used; second is silently ignored (standard HTTP behavior)
```

**Valid: Filter by multiple criteria simultaneously**
```sh
# Both filters applied (AND logic): fallback_used=true AND fallback_stage=canon
curl -s "http://127.0.0.1:11888/api/status?fallback_used=true&fallback_stage=canon" | jq '.tasks | length'
```

#### Common Error Scenarios

| Scenario | Error | Fix |
|----------|-------|-----|
| Typo in stage name: `fallback_stage=Canon` (capital C) | No error — auto-lowercased to `canon` | Works as-is (case-insensitive) |
| Spaces in stage: `fallback_stage=can%20on` | HTTP 400 with valid values list | Use a listed valid stage name |
| Invalid boolean: `fallback_used=true` (case-correct) | Works; filters correctly | No fix needed |
| Invalid boolean: `fallback_used=True` (capitalized) | HTTP 422 Validation Error | Change to lowercase: `true` or `false` |
| No matching results | Empty `tasks` array | Filters may be too restrictive; try base `/api/status` |
| Server returns 500 Internal Error | Check API server logs | Restart API with `agent-stack-up` |

---

For more details, see the files in `/home/daravenrk/dragonlair/` and `/opt/ai-stack/`.

Additional operations and backend autonomy notes are here:

- `/home/daravenrk/dragonlair/SYSTEM_NOTES_AND_AUTONOMY.md`
