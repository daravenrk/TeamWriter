# agent_stack/README.md

# Agent Stack (Python)

This directory contains the Python-based agent stack for Dragonlair.

## Structure
- `orchestrator.py`: Main entry point, routes tasks to subagents.
- `ollama_subagent.py`: Handles Ollama LLM endpoint requests.
- `chatgpu_subagent.py`: Handles ChatGPU tool/endpoint requests.
- `copilot_subagent.py`: Handles Copilot Codes tool/endpoint requests.
- `lock_manager.py`: File-lock and endpoint anti-spam controller.
- `profile_loader.py`: Loads markdown agent behavior profiles.
- `agent_profiles/*.agent.md`: Behavior/action definitions in markdown.

## Usage
- Extend `OrchestratorAgent` to implement routing logic.
- Each subagent class should implement its own API call logic.

## CLI Layer

Use the CLI wrapper to operate the orchestrator directly:

```sh
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl <subcommand>
```

Examples:

```sh
agentctl profiles
agentctl profile-lint
agentctl plan "write a concise debate on local AI governance"
agentctl once "design a robust state machine"
agentctl --stream once "design a robust state machine"
agentctl --stream chat
agentctl health
```

Profile validation and prompt-size guardrail:

- `agentctl profile-lint`
- `AGENT_MAX_SYSTEM_PROMPT_CHARS=12000` limits rendered profile system prompts during lint and runtime

## Docker Deployment

Compose file:

- `docker-compose.agent.yml`

Start:

```sh
/home/daravenrk/dragonlair/bin/agent-stack-up
```

Deployment behavior after edits:

- Python backend changes (for example `api_server.py`, `book_flow.py`, `orchestrator.py`) require a container rebuild/restart:

```sh
/home/daravenrk/dragonlair/bin/agent-stack-up
```

- Static UI changes under `agent_stack/static/` are bind-mounted and become available without a rebuild (browser refresh required).
- Profile changes under `agent_stack/agent_profiles/` are bind-mounted and hot-reloaded by the orchestrator.

Frontend:

- `http://127.0.0.1:11888`

Service ports (current compose defaults):

- `11888` -> `dragonlair_agent_stack` API/UI
- `11434` -> `ollama_nvidia`
- `11435` -> `ollama_amd`
- `11999` -> `fetcher` (from `docker-compose.fetcher.yml`)

Operator note:

- VS Code forwarded ports may include stale auto-forward entries; verify active listeners with `ss -ltnp`.

Service status and queue monitoring from CLI:

```sh
agentctl server-status
agentctl server-watch --interval 1
agentctl server-submit "reply with ok" --profile nvidia-fast
```

Runtime validation checklist:

```sh
docker compose -f /home/daravenrk/dragonlair/agent_stack/docker-compose.agent.yml ps
curl -sS http://127.0.0.1:11888/api/health | jq .
curl -sS http://127.0.0.1:11888/api/ui-state | jq '.task_counts, .latest_stage_event'
python3 -m py_compile /home/daravenrk/dragonlair/agent_stack/api_server.py /home/daravenrk/dragonlair/agent_stack/book_flow.py /home/daravenrk/dragonlair/agent_stack/orchestrator.py
bash /home/daravenrk/dragonlair/agent_stack/scripts/failure_path_integration_drill.sh
```

The integrated failure-path drill covers interruption recovery, paused review-gate restart/resume, task cancellation, and fallback-integrity validation in one operator run.

New feedback and review-gate APIs:

- `POST /api/book-feedback` records section feedback and can request pause (`pause_before_continue=true`).
- `GET /api/book-feedback` returns filtered feedback rows.
- `POST /api/tasks/{task_id}/review-action` accepts `continue`, `rewrite`, or `defer` for paused review checkpoints.

Example payloads:

```sh
curl -sS -X POST http://127.0.0.1:11888/api/book-feedback \
	-H 'Content-Type: application/json' \
	-d '{
		"task_id": "<task_id>",
		"approved": true,
		"needs_rewrite": false,
		"score": 8.5,
		"comment": "Section works, pause for approval.",
		"feedback_type": "thumb",
		"pause_before_continue": true
	}' | jq .

curl -sS -X POST http://127.0.0.1:11888/api/tasks/<task_id>/review-action \
	-H 'Content-Type: application/json' \
	-d '{"action":"continue","note":"approved","reviewer":"operator"}' | jq .
```

## Markdown Agent Profiles

### March 2026: Research Agent Update
- book-researcher now uses qwen3.5:14b (128k context window)
- Internet, news, and Wikipedia research is now standard for research agent
- Research output is structured and data-driven

Agents are now behavior-defined via markdown profile files under:

- `agent_profiles/*.agent.md`

Profile format:

1. YAML-like frontmatter between `---` blocks
2. Markdown sections for behavior/action notes

Required frontmatter keys:

- `name`
- `route` (example: `ollama_amd`, `ollama_nvidia`)
- `model` (default model used for that profile)

Optional frontmatter keys:

- `intent_keywords` comma-separated match terms
- `priority` higher value wins during routing
- `default_stream` true/false
- `num_ctx` integer (forwarded to Ollama options)
- `num_predict` integer (forwarded to Ollama options)
- `temperature` float (forwarded to Ollama options)
- `timeout_seconds` per-profile execution timeout override
- `retry_limit` transient retry count before surfacing failure
- `allowed_routes` comma-separated route allowlist enforced at runtime
- `model_allowlist` comma-separated model allowlist enforced at runtime

Routing behavior:

- Orchestrator loads all profiles at startup.
- Orchestrator hot-reloads profiles when `.agent.md` files are changed.
- Input is selected via weighted profile scoring using `intent_keywords`, `priority`, token balance, recent quality outcomes, and deterministic tie-break rules.
- Selected profile determines route + model + stream default.
- Runtime policy enforcement blocks disallowed route/model selections and applies per-profile timeout/retry policy.
- Triage and fallback logic still applies.

Prompt composition behavior:

- Profile sections `# Purpose`, `# System Behavior`, and `# Actions` are composed into a single system prompt.
- That composed system prompt is sent to Ollama in the `system` field.
- The user input is still sent as the request `prompt`.

This means profile md content is part of baseline model context for every request routed by that profile.

This gives you the "agent behavior in md" workflow while keeping execution controlled by Python classes.

### Profile Sets

You do not need to run all profiles at once.

Use environment variable `AGENT_PROFILE_SET` to select an active profile group:

- `all` (default): load all profiles
- `book`: load only `book-*` profiles
- `code`: load only non-`book-*` profiles

Examples:

```sh
AGENT_PROFILE_SET=book
AGENT_PROFILE_SET=code
```

This keeps role names stable while letting you run a clean book set or code set.

### OpenClaw Compatibility Mode

OpenClaw compatibility is provided by this same server using OpenAI-form endpoints while keeping local AMD/NVIDIA routes.

Use a dedicated server mode:

- `AGENT_SERVER_MODE=standard` (default)
- `AGENT_SERVER_MODE=openclaw-client`

In `openclaw-client` mode, this server exposes compatible endpoints and routes requests to local profiles on your existing AMD/NVIDIA Ollama endpoints.

GPU behavior note:

- OpenClaw mode does not reconfigure or recluster GPUs.
- GPU topology remains unchanged; mode only affects profile selection and request formatting.

Environment variables:

- `AGENT_SERVER_MODE=openclaw-client`

Change-log lock best practices (non-blocking logging):

- `AGENT_CHANGELOG_ASYNC=true` (default): enables intermediate async logging agent.
- `AGENT_CHANGELOG_LOCK_TIMEOUT_SECONDS=0.05` (default): short lock wait for log writes.
- `AGENT_CHANGELOG_QUEUE_MAX=2048` (default): bounded queue to avoid unbounded memory growth.

Behavior:

- Agent work enqueues change-log events and returns immediately when possible.
- Log writer flushes in a background thread with short lock timeouts.
- On shutdown, logger stops quickly (best-effort flush) so agent termination is not delayed.

OpenClaw-compatible inbound endpoints (OpenAI-form) in openclaw-client mode:

- `GET /v1/models`
- `POST /v1/chat/completions`

These endpoints are enabled only when `AGENT_SERVER_MODE=openclaw-client`.

Model/profile selection controls for OpenClaw-compatible requests:

- `OPENCLAW_FAST_PROFILE` (default `nvidia-fast`)
- `OPENCLAW_DEEP_PROFILE` (default `amd-writer`)
- `OPENCLAW_TOOL_PROFILE` (default `amd-coder`)
- `OPENCLAW_MODEL_PROFILE_MAP` (optional direct model mapping)

Routing rule:

- Tool profile is used only when `tools` is present in the request payload.
- Without `tools`, requests route to fast/deep profiles (or non-tool direct mapping).

`OPENCLAW_MODEL_PROFILE_MAP` examples:

```sh
OPENCLAW_MODEL_PROFILE_MAP='{"openclaw-fast":"nvidia-fast","openclaw-deep":"amd-writer","openclaw-tool":"amd-coder"}'
```

or

```sh
OPENCLAW_MODEL_PROFILE_MAP=openclaw-fast=nvidia-fast,openclaw-deep=amd-writer,openclaw-tool=amd-coder
```

Strict mode validation:

- `AGENT_STRICT_MODE_VALIDATION=true` (default)

This causes startup failure for conflicting mode and alias settings (fail-fast).

Smoke test script:

- `agent_stack/scripts/openai_compat_smoke.sh`

Usage:

```sh
bash agent_stack/scripts/openai_compat_smoke.sh http://127.0.0.1:11888 openclaw-fast
```

Route aliasing remains local-route only (between `ollama_amd` and `ollama_nvidia`).

### OpenClaw Env Matrix

Standard mode:

```sh
AGENT_SERVER_MODE=standard
```

OpenClaw client mode:

```sh
AGENT_SERVER_MODE=openclaw-client
AGENT_PROFILE_SET=book   # or code
```

Recommended fast/deep/tool defaults:

```sh
OPENCLAW_FAST_PROFILE=nvidia-fast
OPENCLAW_DEEP_PROFILE=amd-writer
OPENCLAW_TOOL_PROFILE=amd-coder
```

These defaults preserve the "small fast instruction + larger thoughtful AMD + tool-capable tool lane" pattern.

## Locking And Rate Controls

The stack now includes a shared lock manager with two protections:

- Edit lock: protects routing/editing decisions from concurrent volatile writes.
- Endpoint slot lock: prevents endpoint spam with rate-limit and in-flight caps.

Current defaults:

- AMD endpoint (`11435`): `max_inflight=1`, `min_interval_seconds=1.5`
- NVIDIA endpoint (`11434`): `max_inflight=1`, `min_interval_seconds=1.0`

Lock/state files are stored under:

- `/tmp/dragonlair_agent_stack`

You can override root path with:

- `DRAGONLAIR_LOCK_ROOT=/your/path`

## Hung/Failure Triage

The orchestrator now tracks runtime health for each registered subagent.

Behavior:

- Marks subagent as `running` when invoked.
- Detects hung calls using timeout (`call_timeout_seconds`, default 120s).
- Marks timeout as `hung`, increments `hung_count`, and quarantines the subagent.
- Marks exceptions as `failed`, increments `failed_count`, and quarantines the subagent.
- Uses fallback subagent when primary is hung/failed and another agent is available.

Health report API:

- `OrchestratorAgent.get_agent_health_report()`

Returned fields include state, last start/completion, duration, counters, and quarantine windows.

## Next Steps
- Add more subagents as needed.
- Integrate with the rest of the Dragonlair toolkit.

## Book Flow Pipeline

The book pipeline is implemented in `book_flow.py` and runs a hierarchical role-based flow:

- `book-publisher` (brief + final approval)
- `book-researcher`
- `book-architect`
- `book-chapter-planner`
- `book-canon`
- `book-writer`
- `book-assembler`
- `book-developmental-editor`
- `book-line-editor`
- `book-continuity`

### Guarantees

- Shared changes log is used for every stage.
- Lock status is checked and recorded before/after stage work.
- Stage artifacts are written under an exclusive publisher store lock.
- Stage completion events are logged for publisher review.
- Per-stage handoff packets are persisted for downstream agents.
- Detailed prompt/output diagnostics are available behind verbose mode.

### Verbose Logging

For book flow, use `-v` / `--verbose` (or API `verbose=true`) to enable full diagnostics capture.

- Default mode: lightweight operational logging in `changes.log`.
- Verbose mode: adds `diagnostics/agent_diagnostics.jsonl` with full per-attempt prompt/output payloads and timestamps.

### Runtime Presets And Ollama Boundaries

Profile-driven inference is now preset-governed.

- Agent profiles must declare `runtime_preset`.
- `agent_stack/runtime_presets.json` owns the effective `route`, `model`, `num_ctx`, and `num_gpu` baseline.
- The orchestrator resolves runtime settings through `OrchestratorAgent._resolve_profile_runtime_settings(...)` before any Ollama call is issued.
- Inline profile overrides for `route`, `model`, `num_ctx`, and `num_gpu` are rejected by profile lint/runtime validation.

This means the supported path for book flow is:

- `book_flow.py` -> orchestrator profile selection / overrides -> runtime preset resolution -> `OllamaSubagent.run()` -> Ollama `/api/generate`

Direct Ollama calls still exist, but only as intentional exceptions for maintenance or diagnostics.

- `orchestrator.py::_unload_route_model(...)` sends a direct empty-prompt request to unload a model during hibernation.
- Calibration/probe scripts under `agent_stack/scripts/` may call `/api/generate` directly to test endpoint behavior, GPU offload, or context limits.

Those direct probe utilities bypass runtime preset abstraction by design. They are valid for endpoint calibration, but they should not be used as proof that book-flow or agent-profile execution bypassed preset governance.

When diagnosing a book-flow regression, use this sequence first:

- Read `run_journal.jsonl` for stage lifecycle and the last completed event.
- Read `diagnostics/agent_diagnostics.jsonl` for the stage prompt, resolved profile, resolved route/model/options, and correlation ID.
- Search `book_project/ollama_run_ledger.jsonl` for the same correlation ID.

Interpretation:

- If diagnostics show a correlation ID but the run ledger has no matching entry, the failure happened before or outside `OllamaSubagent.run()`.
- If the run ledger has a matching entry with `success=false`, the endpoint call was attempted and the error field is the next diagnostic anchor.
- If the run ledger shows successful calls but book flow stalls later, the issue is in stage parsing, quality gates, or downstream artifact handling rather than preset resolution.

### Prompt Contracts And Gates

Every stage uses a strict contract structure:

- `ROLE`
- `OBJECTIVE`
- `CONSTRAINTS`
- `INPUTS`
- `OUTPUT FORMAT`
- `FAILURE CONDITIONS`

Quality gates and retries are enforced for:

- chapter spec quality
- developmental editor scoring
- continuity blocking issues
- publisher approval decision

Quality scoring thresholds are runtime configurable and can be adaptive:

- Base floors: `BOOK_QUALITY_MIN_SCORE`, `BOOK_QUALITY_MIN_AVG_SCORE`, `BOOK_QUALITY_MIN_CONTENT_SCORE`
- Adaptive controls: `BOOK_QUALITY_ADAPTIVE_ENABLED`, `BOOK_QUALITY_ADAPTIVE_ALPHA`, `BOOK_QUALITY_ADAPTIVE_GAIN`, `BOOK_QUALITY_ADAPTIVE_WARMUP_RUNS`, `BOOK_QUALITY_ADAPTIVE_MAX_SCORE`, `BOOK_QUALITY_ADAPTIVE_MAX_CONTENT_SCORE`
- Per-book learning state is persisted at `quality_learning_state.json`; threshold snapshots are recorded in run artifacts for traceability.

### Memory Layers

Book flow persists three memory layers in artifacts:

- Permanent memory: `00_brief`, `02_outline`, `03_canon`
- Rolling memory: `03_canon/rolling_memory.json`
- Local task memory: constructed per stage from chapter spec + canon + recent summaries

### Artifact Validation

At run completion, required artifacts are verified and the result is written to `run_summary.json`.
