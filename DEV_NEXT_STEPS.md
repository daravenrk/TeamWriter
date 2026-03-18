# Development Next Steps & Process Guide

**Last Updated:** March 18, 2026  
**Project:** Dragonlair Agent Stack — Multi-agent orchestration for book writing and code generation  
**Current Focus:** Complete one end-to-end book run and validate interruption recovery in production flow

---

## 1. Project Overview

Dragonlair is a distributed multi-agent system that orchestrates specialized Ollama LLM models to collaboratively write books and generate code. The system consists of:

- **Backend**: FastAPI REST orchestrator (`agent_stack/api_server.py`) managing task queue, resource tracking, and agent health
- **Frontend**: Vanilla JavaScript SPA (`agent_stack/static/index.html`) with real-time task monitoring and spawn queue controls
- **Agent Profiles**: Specialized roles defined in `agent_stack/agent_profiles/` (e.g., book-publisher, book-researcher, book-architect, book-chapter-planner, book-writer, etc.)
- **Orchestration**: Book-flow pipeline (`agent_stack/book_flow.py`) coordinating multi-stage handoffs between agents
- **Infrastructure**: Docker Compose deployment with Ollama containers for AMD and NVIDIA routes
- **Scheduling**: Cron-based reconciliation every 5 minutes (`agent_stack/trigger_next_job.sh`)

---

## 2. System Architecture

### Core Components

| Component | Purpose | Status |
|-----------|---------|--------|
| `api_server.py` | FastAPI REST API for task queuing, agent coordination, spawn controls, UI state | ✅ Fully functional with crontab timer |
| `book_flow.py` | Entry point for book pipeline orchestration | 🔴 Publisher stage returns empty outputs |
| `orchestrator.py` | Multi-agent task sequencing and handoff management | ✅ Working (diagnostics added) |
| `static/index.html` | WebUI with crontab schedule visibility, spawn queue controls | ✅ Fully implemented |
| `agent_profiles/*.agent.md` | 15+ specialized agent role definitions with prompts | ✅ Available |
| `docker-compose.agent.yml` | Ollama service orchestration (AMD + NVIDIA routes) | ✅ Running |

### Task Lifecycle

```
queued → (10s pre-spawn delay) → running → completed/failed/cancelled
```

- **Pre-spawn delay**: 10 seconds before task moves to running state (configurable via spawn control)
- **Spawn controls**: Real-time Go/Pause/Stop actions on queued tasks
- **Agent routes**: `ollama_amd` (1 concurrent, qwen3.5:27b) and `ollama_nvidia` (3 concurrent, qwen3.5:4b)

---

## 3. Current Status

### ✅ Completed Work (23 items)

- Book/code flow infrastructure, CLI setup, output saving
- API endpoints for publisher, researcher, architect, chapter-planner
- Web UI structure with task display, refresh logic, real-time status
- Crontab timer calculation and display (shows next scheduled run)
- Pre-spawn delay (10s pause before task execution)
- Spawn queue interdiction controls (Go/Pause/Stop buttons work correctly)
- Docker deployment and validation

### ⚠️ Current Risk: Mid-run interruption without terminal completion

**Observed behavior**:
- Some runs stop after early stages (for example at `research` stage start)
- No `run_summary.json` is produced for interrupted runs
- In-memory task state was previously lost across service restarts

**Mitigations now implemented**:
- Task ledger persisted to disk (`book_project/task_ledger.json`)
- Startup bootstrap reload and requeue logic
- Interruption detection for stalled runs via run journal analysis
- Reconcile endpoint now emits interruption diagnostics and can requeue stalled book tasks

### ⏳ Remaining Active Validation

- **Todo**: Validate runtime behavior with controlled interruption drill and a full successful book completion

---

## 4. Critical Debugging Notes

### From User Memory (Patterns & Solutions)

1. **Docker Code Caching**: Code changes require `--build` flag on docker compose:
   ```bash
   docker compose -f agent_stack/docker-compose.agent.yml up -d --build
   ```

2. **Ollama CLI Access**: The host may not have `ollama` CLI installed; use:
   ```bash
   docker exec dragonlair_agent_stack ollama run [model-name]
   ```

3. **Lock Manager Pattern**: All agent changes are logged to shared changes log with producer lock:
   - Agents must acquire lock before editing
   - Publisher verifies lock status before marking tasks complete
   - Review `agent_stack/lock_manager.py` for implementation

4. **Mode-based Workflow**: Web UI uses mode selection (Book/Code) instead of profile dropdown
   - User enters prompt and presses Go
   - Backend routes to appropriate agent stack

---

## 5. Next Development Steps (Prioritized)

### 🔴 **URGENT: Complete a Book Run Now (MVP Exit Criteria)**

**Primary Goal**: Produce one terminal `run_success` with `run_summary.json` and final manuscript output.

**Execution checklist**:
1. Launch one real book run from WebUI (Book mode).
2. Verify progression beyond `research` and into later gates (`developmental_editor`, `publisher_qa`).
3. Confirm artifacts are created:
  - `06_final/manuscript_v1.md`
  - `run_summary.json`
  - `run_journal.jsonl` containing terminal `run_success`.
4. If interruption occurs, run reconcile and confirm:
  - `run_interrupted` event in `run_journal.jsonl`
  - `book_run_interrupted` in UI/resource events
  - task re-queued and resumed automatically.

**Acceptance criteria**:
- At least one run reaches terminal success.
- No run silently disappears without terminal status.
- Reconcile can recover a stalled running book task.

---

### 📋 **HIGH PRIORITY: Runtime Hardening (After First Success)**

- **Completed (2026-03-18) / Todo 39**: Add profile-signal routing scorer in orchestrator
  - Use profile `intent_keywords`, `priority`, recent quality outcomes, and token balance when selecting a profile
  - Replace first-match keyword routing with weighted scoring + deterministic tie-break rules

- **Completed (2026-03-18)**: Add profile execution policy enforcement
  - Added profile lint support for `timeout_seconds`, `retry_limit`, `allowed_routes`, and `model_allowlist`
  - `profile_loader.py` now parses those frontmatter fields into runtime profile metadata
  - `orchestrator.plan_request()` now enforces route/model allowlists and threads timeout/retry policy into execution planning
  - `handle_request_with_overrides()` now honors per-profile timeout and transient retry policy during invocation

- **Completed (2026-03-18)**: Remove duplicate `OrchestratorAgent.__init__` definition in `orchestrator.py`.
- **Completed (2026-03-18)**: Convert broad `except Exception` blocks in core runtime files to typed exceptions with error codes.
  - All remaining broad catches in `api_server.py` and `book_flow.py` verified converted or already wrapped (`AgentUnexpectedError`, `AgentStackError`, `OSError`, `ValidationError`, `json.JSONDecodeError`)
  - Full exception hierarchy (`AgentStackError`, `AgentUnexpectedError`, `Ollama*Error`, `BookExportError`, etc.) applied consistently throughout
- **Completed (2026-03-18)**: Add integration drill script for interruption recovery (start run -> interrupt -> reconcile -> resume).
  - Implemented script: `agent_stack/scripts/interruption_recovery_drill.sh`
  - Use after task lifecycle, retry, reconcile, or spawn-control changes
  - Run in staging or explicit maintenance windows before production rollout of recovery changes
  - Keep as operator/CI validation; do not run automatically from normal production cron reconciliation
- **Completed (2026-03-18)**: Add terminal-state integrity check ensuring each run ends with `run_success` or `run_failure`.
  - Added `_TERMINAL_RUN_EVENTS = frozenset({"run_success", "run_failure"})` sentinel in `api_server.py`
  - Added `_ensure_run_journal_terminal(run_dir, task_id, reason)` idempotent guard — reads journal before writing, never duplicates a terminal event
  - Replaced raw `_append_run_journal_event(..., "run_failure", ...)` calls in both `AgentStackError` and `AgentUnexpectedError` catch blocks of `_run_book_task()` with the guarded call
  - Added reconcile integrity pass: for every permanently-failed task with a known `run_dir`, `_ensure_run_journal_terminal()` is called so journals orphaned by process kill or restart are retroactively sealed

---

### 🟡 **MEDIUM PRIORITY: Infrastructure Validation (Post-publisher fix)**

These should be completed once publisher outputs successfully:

- **Completed (2026-03-18) / Todo 26**: Clean duplicate orchestrator initialization
  - Review `orchestrator.py` for redundant init patterns
  - Consolidate into single initialization path

- **Todo 27**: Add checkpoint save agent
  - Implement agent for persisting book state between stages
  - Enable pause/resume on long-running pipelines

- **Todo 28**: Add failure-path integration tests
  - Test retry logic, fallback handling, graceful degradation
  - Validate task cancellation flows

- **Todo 29**: Expose logging metrics in UI
  - Add metrics card showing agent health, queue depth, error rates
  - Integrate prometheus or similar metrics export

- **Todo 33**: Validate FastAPI runtime environment
  - Test under load (multiple concurrent tasks)
  - Verify resource limits and error handling

- **Completed (2026-03-18) / Todo 34**: Add global agent output schema validator
  - Added shared stage output schema registry in `agent_stack/output_schemas.py`
  - `run_stage(...)` now validates structured payloads against named schemas before custom quality gates run
  - Schema failures feed the existing retry loop, so missing fields automatically trigger corrective retries with actionable gate messages

- **Completed (2026-03-18) / Todo 35**: Add framework integrity gate
  - Added `FrameworkIntegrityError` to `exceptions.py` with code `FRAMEWORK_INTEGRITY_ERROR`
  - Added `check_framework_integrity(skeleton, arc_tracker, progress_index)` in `book_flow.py` — validates required fields in `book_identity`, `design_framework`, `chapter_skeleton`, `arc_tracker`, and `progress_index`
  - Gate is called after `build_framework_skeleton()` writes the skeleton, before the Canon stage; raises with a full diagnostic list of all missing/empty fields
  - A `framework_integrity_passed` event is appended to `run_journal.jsonl` on success

- **Completed (2026-03-18) / Todo 36**: Add arc consistency scorer
  - Added `score_arc_consistency(arc_tracker, rubric_report)` to `book_flow.py`
  - Open loops are **persistent story features** that carry across chapters until resolved before series end — they are NOT failures
  - Factor 1: **Open-loop persistence** — are tracked loops present in `must_carry_forward`? Loops absent from carry_forward are flagged as at risk of being dropped next chapter
  - Factor 2: **Character-arc acknowledgement** — are prior chapter character updates referenced in `character_state_updates`?
  - Fixed `update_arc_tracker` to **merge** open loops (union) instead of replacing them — loops from prior chapters can never be silently dropped
  - Untracked loops are written as `open_loop_persistence_warning` entries to `agent_context_status.jsonl` so operators/agents know which story features need attention
  - Fires after continuity stage; writes `arc_consistency_score.json` (includes `all_open_loops` and `untracked_open_loops`) and logs `arc_consistency_scored` to `run_journal.jsonl`
  - Raises `StageQualityGateError` with full diagnostic detail if score falls below `ARC_CONSISTENCY_THRESHOLD = 0.5`

- **Todo 37**: Add agent handoff expectation contract
  - Require each agent output to include downstream expectations and acceptance checks
  - Track unmet expectations in `agent_context_status.jsonl`

- **Todo 38**: Add run-level continuity dashboard payload
  - Aggregate arc progress, unresolved loops, and chapter completion into one status artifact
  - Expose this payload in `/api/status` for UI and automation

- **Todo 40**: Promote all profile sections into structured prompt directives
  - Convert sections (`persona`, `response_style`, `quality_loop`, `token_recovery_behavior`) into explicit directive blocks
  - Add prompt audit log showing which sections were rendered and applied per call

- **Todo 41**: Add dynamic model options planner per stage
  - Adjust `num_ctx`, `num_predict`, `temperature`, `think`, `num_gpus` based on stage type and task size
  - Enforce hard safety caps and fallback defaults when profile options are missing

- **Todo 42**: Add token-aware execution policy
  - Use reward token level to tighten validation, reduce creativity variance, and choose safer models
  - Trigger stricter JSON/contract checks automatically when profile tokens are low or depleted

- **Todo 43**: Add failure-memory corrective retries
  - Inject recent `quality_gate_failures.jsonl` reasons into retry prompts as explicit fix targets
  - Track whether each retry resolved the cited failure reason

- **Todo 44**: Add Ollama request caching and deduplication
  - Cache identical prompt+system+options responses for short windows to reduce duplicate compute on retries
  - Store request hash and cache-hit diagnostics in run artifacts

- **Todo 45**: Add adaptive route/model circuit breaker
  - Maintain rolling latency/error metrics per route and model; quarantine degraded combinations early
  - Route around degraded pairs before hard failures occur

- **Todo 46**: Add stage-aware streaming policy
  - Disable streaming by default for strict JSON stages and enable selectively for narrative drafts
  - Record stream mode decisions in diagnostics for each call

- **Todo 47**: Add hedged Ollama calls for high-latency stages
  - Optionally race a secondary route/model after timeout threshold and cancel loser response
  - Restrict hedging to bottleneck stages and pressure-safe capacity windows

- **Todo 48**: Add deep Ollama call telemetry
  - Capture TTFT, total latency, token throughput, retries, fallback hops, and context-size estimates
  - Expose route/model performance dashboard in `/api/status` and UI cards

- **Completed (2026-03-18) / Todo 54**: Add structured Ollama run ledger with correlation IDs
  - `ollama_subagent.run()` now accepts `correlation_id` and `ledger_path` params
  - For both stream and non-stream modes: captures `total_duration_ns`, `eval_count`, `prompt_eval_count`, `eval_duration_ns`, `load_duration_ns` from Ollama's done payload
  - Computes `tokens_per_second` field from eval_count / total_duration
  - Writes one JSONL entry per call to `ollama_run_ledger_path` (default: `book_project/ollama_run_ledger.jsonl`)
  - `orchestrator._invoke_with_triage()` now generates a `call_correlation_id = uuid.uuid4()` per invocation and threads it + the ledger path into each `subagent.run()` call
  - `ollama_run_ledger_path` exposed in health report under `health.analytics.ollama_run_ledger`
  - `latest_ollama_call` field added to `/api/ui-state` response (last ledger entry, for WebUI live display)
  - WebUI voyeur panel now shows last call's `model`, `tok/s`, `eval_count`, and `wall_seconds` in the root graph node

- **Todo 62**: Expand WebUI Pipeline Files panel with full checkpoint data and stage telemetry
  - **What each pipeline file is and does** (current 6, now expanded to 10 stage nodes):
    - `publisher_brief.md` — Book-level constraint/tone/audience brief; first gate; sets the rails for all downstream stages
    - `research.md` — Research dossier with world-building facts, character bios, setting details; feeds canon and writer prompts
    - `architect_outline.md` — Master outline: chapter structure, arc beats, section breakdown; gating document for planner
    - `chapter_planner.md` — Per-chapter section specs with word targets, dramatic beats, and continuity hooks
    - `draft.md` — Raw writer output per section; word-count-gated; feeds editor/line-editor chain
    - `editor_pass.md` — Aggregates: developmental editor (structure), line editor (prose), copy editor (grammar), proofreader (final polish)
    - `canon.md` — Canonical facts and open-loop tracking; prevents contradictions across runs
    - `continuity.md` — Cross-chapter consistency check and patch-tasks list; feeds publisher QA
    - `assembly.md` — Merged final manuscript assembled from all section drafts
    - `publisher_qa.md` — Final APPROVE/REVISE gate with scores, notes, required_fixes; forced_completion flag if retries exhausted
  - **What a user watching wants to see** (partial list; implement as WebUI cards):
    - Checkpoint progress bar `[████████░░] 8/10` from `production_status.checkpoint_score`
    - Per-stage: attempt count, gate pass/fail, artifact presence (`✓`/`✗`), gate_message
    - `next_checkpoint` label highlighted as "current target"
    - Publisher QA: APPROVE/REVISE decision, scores object, forced_completion indicator
    - Interruption/staleness: if `interruption.stalled=true`, show elapsed since last event and `stalled` badge on root node
    - Word count from `manuscript_v1.md` for writer and editor stages (read file size or line count via API)
    - `latest_ollama_call` live stats card: model, tokens/s, eval_count, wall seconds
    - Run journal terminal state: `run_success` / `run_failure` / `run_interrupted` badge on root node
  - **Implementation plan:**
    - Surface `production_status` on the book task in WebUI (already in task dict) ✅ (checkpoint score and nodes now use it)
    - Add API endpoint `GET /api/book/latest-call-stats` to return ledger tail with per-model aggregate stats
    - Add `GET /api/book/run-journal-tail` so WebUI can poll the last N journal events without full endpoint repoll
    - Expand the voyeur panel to a 3-column layout: Pipeline Files | Checkpoints | Live Agents
    - Add `words_written` field: read `len(text)` from final draft artifact and include in `production_status`

- **Todo 63**: Overhaul Dragonlair WebUI with OpenClaw-inspired sidebar dashboard design
  - **Motivation**: The current `index.html` is a single scrolling page with no navigation structure, crammed controls, and polling-only architecture. OpenClaw (github.com/openclaw/openclaw) provides a polished reference: CSS Grid shell, collapsible sidebar nav, card-based panels, live WebSocket streaming, and a clean design token system.
  - **Design token system** — introduce CSS custom properties adapted to Dragonlair's existing blue palette:
    - `--bg: #0a1025` / `--bg-elevated: #0d1630` / `--bg-hover: #131d3a` / `--card: #0b1430`
    - `--border: #1e2a45` / `--border-strong: #263252`
    - `--text: #c6d4f5` / `--text-strong: #e8eeff` / `--muted: #5a6a8a`
    - `--accent: #4a9eff` (Dragonlair blue, replacing OpenClaw red) / `--accent-subtle: rgba(74,158,255,0.12)` / `--accent-glow: rgba(74,158,255,0.22)`
    - `--ok: #22c55e` / `--warn: #f59e0b` / `--danger: #ef4444` / `--info: #3b82f6`
    - `--mono: "JetBrains Mono", ui-monospace` / `--font-body: "Inter", system-ui`
    - `--radius-sm: 6px` / `--radius-md: 10px` / `--radius-lg: 14px` / `--radius-xl: 20px`
    - `--shadow-md: 0 4px 16px rgba(0,0,0,0.4)` / `--shadow-lg: 0 12px 32px rgba(0,0,0,0.5)`
    - `--ease-out: cubic-bezier(0.16,1,0.3,1)` / `--duration-fast: 100ms` / `--duration-normal: 180ms`
    - `--shell-nav-width: 248px` / `--shell-nav-rail-width: 72px` / `--shell-topbar-height: 52px`
  - **Shell layout** — CSS Grid replacing flex-column (mirrors OpenClaw `layout.css`):
    - `grid-template-areas: "nav topbar" "nav content"` with `height: 100dvh; overflow: hidden`
    - `.shell--nav-collapsed` reduces sidebar to `--shell-nav-rail-width` (icon-only rail)
  - **Navigation sidebar** — collapsible to icon-only rail; grouped nav sections:
    - **Monitor** — health summary: agent heartbeats, last checkpoint badge, live root node status
    - **Pipeline** — 10-stage pipeline with checkpoint progress bar, stage cards, artifact and gate status
    - **Tasks** — task queue table: status pills, retry counters, cancel/pause/resume row actions
    - **Send** — message/book-flow submission form and streaming response viewer
    - **Cron** — next-run timer, reconcile controls, job history list
    - **Agents** — live agent health cards: state, model, tokens, heartbeat, output excerpts
    - **Analytics** — Ollama run ledger: tok/s gauges, latency trends, eval counts, last-call card
    - **Logs** — live tail of `changes.log`, `run_journal.jsonl`, `diagnostic_report.md`) with text filter and download button
    - **Config** — book-flow request defaults, route/model overrides, pressure mode controls (schema form or raw JSON)
  - **Nav item component** (mirrors OpenClaw nav-item pattern):
    - 40px min-height, `12px` border-radius, `1px` transparent border
    - Active: accent left-border pip (3px, absolute positioned), `--accent-subtle` background
    - Icon: 16x16 inline SVG, `opacity: 0.72` resting, `1.0` on hover/active; stroke-width 1.5px
    - Collapsed: icon-only 44px square, no text
  - **Topbar** — sticky, `backdrop-filter: blur(12px) saturate(1.6)`, breadcrumb, connection-status pill (online=green dot, offline=red dot), sidebar collapse toggle, theme toggle
  - **Status pills** — `span.pill` components: `completed` → `--ok`, `running` → pulsing `--accent`, `failed` → `--danger`, `queued` → `--warn`, `paused` → `--muted`
  - **Live WebSocket** — add `/ws` WebSocket endpoint to `api_server.py` that broadcasts the same payload as `/api/ui-state` on every state change, replacing 2-second polling; UI auto-reconnects with exponential backoff; keep polling as fallback
  - **Animations** — `@keyframes dashboard-enter { from { opacity:0; transform:translateY(12px); } }` on view switches; `@keyframes shimmer` skeleton loading for cards during WS connect
  - **Mobile breakpoint** — at `max-width: 900px` collapse sidebar to full-width overlay: topbar hamburger toggle, content uses full width, backdrop dismisses sidebar
  - **Dark/light theme toggle** — topbar button flips `data-theme-mode="light"` on `<html>`; light theme token overrides in `:root[data-theme-mode="light"]` block
  - **Implementation approach** — keep single `index.html` (no build step); split CSS into `<style>` sections by concern (`base`, `layout`, `components`, `views`); vanilla JS view-router: `Map<string, () => string>` keyed by view name, rendered into `.content` div
  - **Reference files**: `github.com/openclaw/openclaw/blob/main/ui/src/styles/base.css` and `layout.css` are the primary CSS references

- **Todo 64**: Build `dragonlair` CLI wrapper with structured subcommands and bash completion
  - **Motivation**: Current `bin/` contains loose shell files (`ask-amd`, `book-flow`, `agentctl`, etc.) with no unified interface, no help output, and no `--json` flag. A `dragonlair` CLI (inspired by `openclaw` CLI pattern: `openclaw status`, `openclaw message send`, `openclaw agent`) makes automation and human operation consistent.
  - **Entry point**: Create `bin/dragonlair` as a single bash script with subcommand dispatch; shared `API_BASE=${DRAGONLAIR_API:-http://127.0.0.1:11888}`
  - **Subcommands**:
    - `dragonlair status [--json]` — GET `/api/ui-state`; human summary: queue depth, agent health, last checkpoint, last Ollama call tok/s
    - `dragonlair send --message "…" [--profile X] [--model Y] [--stream] [--json]` — POST `/api/tasks`, print task_id; `--stream` follows output via `/api/stream`
    - `dragonlair book --title "…" --chapter-number N [--genre X] [--word-target N] [--json]` — POST `/api/book/start`, print run_id and task_id
    - `dragonlair tasks [--status running|failed|queued|all] [--json]` — GET `/api/tasks`; tabulate: task_id (short), status, agent, elapsed, retries
    - `dragonlair cancel <task_id>` — POST `/api/tasks/<id>/cancel`; prompts "Cancel task XYZ? [y/N]" without `--yes`
    - `dragonlair pause <task_id>` / `dragonlair resume <task_id>` — spawn-control endpoints
    - `dragonlair reconcile [--dry-run]` — POST `/api/reconcile`; print recovered/skipped task count
    - `dragonlair logs [--follow] [--tail N] [--source changes|journal|diagnostic]` — tail log files via `GET /api/logs?source=X&tail=N` (new endpoint, see below)
    - `dragonlair reward-ledger [--profile X] [--json]` — token balances from GET `/api/agent-health`
    - `dragonlair doctor` — health self-check: API reachable? routes healthy? GPU available? model list non-empty? prints ✅/❌ per check; inspired by `openclaw doctor`
    - `dragonlair stack up|down|logs|restart` — wrappers around existing `bin/agent-stack-*` scripts with unified help
  - **Global flags**: `--json` → raw JSON output; `--quiet` → suppress progress lines; `--api URL` → override `$DRAGONLAIR_API`
  - **Help system**: `dragonlair help` prints grouped command list; `dragonlair help <cmd>` shows usage + examples; unknown subcommand exits 1 with a hint
  - **Bash completion**: generate `bin/dragonlair-completion.bash`; completes subcommands, flags, and task IDs (fetched via `dragonlair tasks --json`); install docs in README
  - **New API endpoint required**: `GET /api/logs?source={changes|journal|diagnostic}&tail={N}` — reads last N lines from the specified log file inside `book_project/`; hard allowlist on `source` (`changes` → `changes.log`, `journal` → `run_journal.jsonl`, `diagnostic` → `diagnostic_report.md`) to block path-traversal; returns `{"lines": [...]}`
  - **Deprecation path**: once `dragonlair` is stable, mark `bin/ask-amd`, `bin/ask-nvidia`, `bin/chat-*` as deprecated with a wrapper message pointing to `dragonlair send --profile X`

- **Todo 65**: Run a technical review of OpenClaw's agent-control design and convert findings into Dragonlair improvements
  - **Goal**: Hold a technical review and improvement-planning pass focused on how OpenClaw handles agent control, control-plane UX, runtime visibility, session/state management, and operator workflows, then turn that review into concrete Dragonlair architecture and UI changes.
  - **Review scope**:
    - How OpenClaw structures the control surface: sidebar navigation, grouped views, status surfaces, session controls, live logs, tool streaming, config editing, node/agent visibility
    - How OpenClaw represents control flow at each point: user request entry, channel/session selection, agent/tool execution, approval gates, logs/debug traces, config mutation, cron/background jobs
    - How OpenClaw separates operator concerns: chat, sessions, channels, skills, nodes, approvals, logs, debug, usage, config
    - How OpenClaw handles state transport: WebSocket updates, SPA shell layout, view switching, loading states, failure states, reconnect behavior
    - How OpenClaw exposes runtime data: connection health, agent/node status, streamed events, tool calls, usage metrics, auditability
  - **Point-by-point technical review deliverable**:
    - Map each OpenClaw control point to a Dragonlair equivalent or gap: `Send`, `Tasks`, `Pipeline`, `Agents`, `Analytics`, `Logs`, `Config`, `Cron`, approvals/retries/reconcile
    - Document what OpenClaw does well, what is merely different, and what Dragonlair should explicitly improve beyond OpenClaw
    - Identify where Dragonlair currently has stronger domain-specific needs than OpenClaw: book-flow checkpoints, producer locks, reward ledger, route quarantine, run journals, quality gates, recovery/reconcile paths
    - Produce a control-plane gap matrix with columns: `OpenClaw feature`, `Current Dragonlair state`, `Gap severity`, `Implementation candidate`, `Priority`
  - **Questions the review must answer**:
    - What should the primary operator workflow be in Dragonlair for code mode vs book mode?
    - What runtime state should be visible at the top level without drilling down?
    - Where should approvals, retries, quarantine state, and recovery state live in the UI?
    - What should be event streamed vs polled vs loaded on demand?
    - Which OpenClaw concepts should be copied closely, and which should be adapted or rejected because Dragonlair is orchestration-heavy rather than general chat-first?
  - **Expected outputs from the review meeting**:
    - A written technical review memo in the repo summarizing findings and tradeoffs
    - A prioritized list of follow-up implementation todos split across backend, API, WebUI, CLI, and observability
    - A recommended control-plane information architecture for Dragonlair
    - A list of 3 to 5 areas where Dragonlair should improve upon OpenClaw rather than imitate it directly
  - **Likely Dragonlair improvements to evaluate**:
    - Dedicated control-plane event stream with typed events instead of monolithic status repolls
    - Better agent/node detail panes: route, model, profile, inflight slot, last failure, quarantine timer, hibernation state, reward balance
    - First-class book-flow run inspector: stage attempts, gate decisions, handoff artifacts, run journal tail, forced-completion and recovery markers
    - Explicit operator actions panel: retry stage, resume run, reconcile stalled run, clear quarantine, export logs, compare runs
    - Session-aware command history and task drill-down instead of a single-page global dashboard
  - **Success criterion**: this todo is complete only when the technical review has produced actionable design decisions, not just inspiration screenshots or styling notes

- **Todo 66**: Add quarantine diagnostics and recovery controls to the WebUI
  - Add a dedicated diagnostics panel under System Snapshot showing flagged agents, quarantine countdown, last error, last recovery reason, and last recovery time
  - Wire operator actions to existing recovery endpoints so the UI can recover flagged agents without dropping into curl/manual API calls
  - Distinguish active quarantine from stale failed/hung history in the operator display so Attention only means an active problem

- **Todo 67**: Add quarantine audit trail and route-level failure diagnostics
  - Persist quarantine transitions, auto-recover events, and manual recover actions to a structured diagnostics stream for later debugging
  - Add route/model context to quarantine diagnostics so operators can see whether the issue is agent-local, endpoint-local, or workload-specific
  - Surface the last few quarantine/recovery events in the UI and API so recurring failures can be recognized quickly

- **Todo 68**: Add protected recovery actions and cooldown safety rails
  - Add explicit UI/API distinction between safe recover, forced recover, and recover-preview so operators can see what will be reset before taking action
  - Prevent force-recovery from masking active book-flow stage execution without a stronger warning path and audit entry
  - Add cooldown metadata so repeated recover loops are obvious and rate-limited

- **Todo 69**: Add quarantine trend metrics and repeated-failure heuristics
  - Track repeated quarantine events per route/model/profile over rolling windows and surface the trend in API diagnostics
  - Recommend model fallback, route downgrade, or profile reroute automatically when the same failure signature repeats
  - Feed these counters into the future Prometheus/Grafana work so quarantine is observable over time

- **Todo 70**: Expose effective profile execution policy in CLI/API/UI
  - Show resolved `timeout_seconds`, `retry_limit`, route allowlist, and model allowlist in `agentctl plan`, `/api/status`, and operator diagnostics
  - Make it obvious when a request is using profile defaults versus explicit overrides so operators can debug policy behavior quickly
  - Include policy-violation diagnostics in API responses when a model or route is rejected by profile rules

- **Todo 71**: Review Claude-produced code/artifacts (if available) for production-gap analysis
  - Locate Claude-authored code paths, prompts, docs, and implementation notes in this repo (or adjacent workspaces if linked)
  - Compare Dragonlair decisions against Claude artifacts for control-plane reliability, autonomous recovery, schema safety, and writing-pipeline quality
  - Produce a concrete gap report with prioritized "adopt", "adapt", and "reject" actions tied to Dragonlair backlog items

- **Todo 72**: Complete open-port audit and least-exposure hardening
  - Inventory active listeners (`11434`, `11435`, `11888`, `11999`, plus infra ports) and map each to owner service, dependency path, and operator need
  - Recommend and implement least-exposure bindings where safe (prefer localhost or internal Docker network for non-public services)
  - Add operator docs for which ports are required in dev/LAN/prod modes and include firewall guidance

- **Todo 73**: Run one full end-to-end book flow after schema enforcement changes
  - Execute a full chapter run with current retries/fallbacks and capture run journal + diagnostics
  - Confirm schema validation failures surface actionable gate messages and recover through retries when possible
  - Record pass/fail results and next fixes back into `DEV_NEXT_STEPS.md`

- **Todo 74**: Add structured run summary artifact per chapter run
  - At the end of each `run_book_chapter()`, write a `run_summary.json` into the run dir with stage outcomes, gate verdicts, retry counts, token totals, and wall-clock durations per stage
  - Expose this artifact via `/api/status` so the UI can show a post-run digest without parsing the full run journal

- **Todo 75**: Harden context_store propagation with typed dataclass
  - Replace the untyped `context_store` dict passed between stages with a `RunContext` dataclass or TypedDict
  - Catch missing/misspelled context keys at type-check time instead of at runtime deep in a stage

- **Todo 76**: Add writing-quality regression harness for model/profile changes
  - Capture a golden-set of stage outputs from a known-good run, store as fixtures
  - Run the harness after any model pull or profile change to flag quality regressions before the next full book run
  - Track rubric scores over time to detect drift

- **Todo 77**: Gate model pull on smoketest pass before committing to production route
  - After `pull-amd` or `pull-nvidia`, run the `smoketest_coder_models.py` logic against writing/coder profiles
  - Refuse to update the active model alias if the smoketest score falls below threshold

- **Todo 78**: Add per-stage wall-clock budget enforcement
  - Define optional `stage_timeout_seconds` in each stage's kwargs or profile policy
  - If a stage exceeds its budget, record the overrun in the run journal and apply fallback (skip, degrade, or abort) rather than waiting indefinitely

- **Todo 79**: Promote `changes.log` to structured JSONL and index it
  - Replace the free-text `changes.log` written per-run with a structured JSONL of `{timestamp, stage, agent, field, before, after}` events
  - Build a thin query helper so audits can filter changes by stage or agent across runs

- **Todo 80**: Add automated .gitignore drift detection
  - On each commit (pre-commit hook) or CI step, check that newly generated runtime artifacts are covered by `.gitignore`
  - Alert operator if untracked runtime files appear that are not in the ignore list, preventing artifact creep back into the index

- **Todo 81**: Add run-level retry budget and abort policy
  - Track cumulative retries across all stages per run; enforce a hard cap (e.g., 20 total retries) to prevent runaway runs
  - On budget exhaustion, write a `run_aborted.json` artifact with stage-by-stage retry counts, then raise a clean abort error instead of hanging

- **Todo 82**: Add profile decay and rebalance mechanism
  - When a profile repeatedly fails quality gates or exhausts tokens, decay its priority weight for the next N routing decisions
  - Auto-rebalance after a configured recovery window so profiles rehabilitate after good performance rather than staying suppressed indefinitely

- **Todo 83**: Expose stage outputs and gate verdicts in the WebUI run view
  - Add a collapsible stage timeline panel to the WebUI that shows each stage, its gate verdict (pass/fail/retry count), and a truncated output preview
  - Wire from the `run_journal.jsonl` so it populates from existing data without requiring new API routes

- **Todo 84**: Add run dry-run / preflight mode
  - Add `--dry-run` flag to book_flow CLI that validates args, profiles, model availability, and framework integrity without calling Ollama
  - Emit a structured preflight report so operators can catch configuration problems before burning GPU time

- **Todo 85**: Add stale run detection and cleanup
  - Track `run_start` events; flag runs with no `run_end` event after a configurable TTL as stale
  - Expose stale runs in the WebUI with a quarantine / abandon action and clean up their run dirs or mark them archived

- **Todo 86**: Add API rate-limit and concurrency guard
  - Prevent the agent stack from accepting a new book-flow job while one is already in flight (or limit to a configurable concurrency)
  - Return a clear 429 / busy response so clients can queue rather than accidentally spawning parallel runs that overload VRAM

- **Todo 87**: Overhaul review validation logic and writer scene guidance for story-aware correctness
  - **Review targets acceptance criteria**: Every review stage (rubric, developmental, section review, publisher QA) must explicitly receive and evaluate against the chapter's `acceptance_criteria` from the book brief and framework skeleton — not just generic rubric dimensions. Reviews that pass without addressing declared acceptance criteria should be treated as incomplete.
  - **Review pass → external notes propagation**: When a review stage passes its gate, extract and commit pertinent story elements (open loops introduced or progressed, character state changes, canon additions, timeline events) to `arc_tracker.json` and `agent_context_status.jsonl` immediately — not only at run-end. This ensures elements survive even if a later stage fails.
  - **Temporal chapter awareness in reviews**: Review agents should receive past chapter summaries (from `session_handoffs.jsonl` / `rolling_memory.json`) AND relevant planned future chapter beats from the framework skeleton's outline structure, so they can evaluate whether the current chapter sets up future chapters correctly and closes what it should.
  - **Structured Writer's Scene Briefing**: Replace the current `local_task_memory` dict passed to the Section Writer with a structured `build_scene_briefing()` function that assembles:
    - *Past context* — open loops (all, with chapter-introduced annotation), character states from arc tracker, relevant prior chapter notes filtered by section goal
    - *Current context* — chapter acceptance criteria, section goal, must_include/must_avoid from chapter spec, continuity watch items from last rubric
    - *Future context* — upcoming chapter beats from framework outline (so the writer can set them up without resolving them)
    - *Guidance header* — a short preamble listing the 3-5 most critical items the writer must honor in this scene (open loops to NOT resolve, character state to maintain, acceptance criterion to hit)
  - **`build_relevant_chapter_notes` fix**: Currently only scores backward-looking summaries. Extend it to also include forward-looking notes from the framework outline for planned but not-yet-written chapters, clearly tagged as `[FUTURE]` so the writer knows not to resolve them prematurely.
  - **Review-on-pass annotation contract**: Add a `review_annotations` field to rubric/developmental/continuity output schemas. When a review passes, write `review_annotations.story_elements` to `agent_context_status.jsonl` with phase `review_passed_annotation` so story state is externalized at the point of approval, not only at pipeline end.

- **Todo 88**: Add Story Skeleton Pre-Run — high-context fast pass before any chapter writing begins
  - **Concept**: Before any chapter is written, run a single dedicated high-context planning pass using a small, fast model (e.g. `qwen3.5:9b` on AMD at 192k ctx) to generate a complete **story skeleton** for the full book or series. This is deliberately low fidelity — the goal is speed and breadth, not quality. The skeleton becomes the authoritative planning artifact that all chapter runs reference throughout the pipeline.
  - **What the skeleton produces** (one structured JSON artifact per book/series):
    - `story_spine` — the single-sentence through-line of the entire story
    - `major_beats` — ordered list of key story events with chapter assignments (e.g. `{beat: "dragon awakens", chapter: 3, type: "inciting_incident"}`)
    - `open_loops` — every plot thread that opens during the book, with `opens_chapter`, `resolves_chapter` (or `"series"` if unresolved at book end), and `resolve_type` (`"answered"` / `"subverted"` / `"deferred"`)
    - `character_arcs` — each named character's starting state, arc milestone per chapter, and ending state
    - `chapter_frames` — one entry per chapter: purpose, what it must set up, what it must resolve, tone, and the 2-3 hardest constraints the writer faces
    - `series_threads` — for multi-book series, threads that intentionally carry beyond this book
  - **Integration points**:
    - Skeleton is written to `book_project/<slug>/framework/story_skeleton.json` at the start of the first chapter run and re-used for all subsequent chapter runs in the same book
    - `build_scene_briefing()` (Todo 87) draws `[FUTURE]` guidance from `chapter_frames` and `open_loops.resolves_chapter` — the writer always knows which loops are theirs to *open*, *sustain*, or *close*
    - `arc_tracker.open_loops` is pre-populated from `story_skeleton.open_loops` tagged as `[PLANNED]` so the arc consistency scorer has ground truth from chapter 1
    - Review agents (rubric, developmental, publisher QA) receive the `chapter_frames` entry for the current chapter as an explicit acceptance target alongside the existing criteria
    - `framework_integrity_gate` (Todo 35) can validate the skeleton exists before chapter 1 begins
  - **New CLI entry point**: `book-flow skeleton --title ... --premise ... --chapters N [--series]` — runs only the skeleton pass and saves the artifact; can be invoked separately before the first chapter run or auto-triggered when no skeleton exists
  - **New agent profile**: `book-story-skeleton.agent.md` — routes to AMD (`ollama_amd`), uses `qwen3.5:9b` at `num_ctx: 192000`, low temperature (`0.3`), high `num_predict` to guarantee full coverage; persona is a "Story Architect" focused on structural planning, not prose quality
  - **Output schema**: Add `story_skeleton` to `output_schemas.py` validating required top-level keys, minimum beat count, and that each open loop has `opens_chapter` and `resolves_chapter` set
  - **Skeleton reuse policy**: If `story_skeleton.json` already exists for the book slug, re-use it without re-running the skeleton pass (operator can force-refresh with `--refresh-skeleton`)

- **Todo 89**: Add Series Layer — multi-book structural design, decomposition, and feasibility analysis
  - **Concept**: When a story concept is large enough to span multiple books, add a series-level planning layer that runs before individual book skeletons (Todo 88). This layer decides how to split the full narrative, assigns story threads and character arcs to specific books, and validates the split is structurally sound before any writing begins.
  - **Series skeleton artifact** (`series_project/<slug>/framework/series_skeleton.json`):
    - `series_spine` — the overarching narrative arc that runs across all books
    - `book_breakdown` — list of books with working title, purpose in the series, approximate word count target, and which story threads it opens/carries/closes
    - `cross_book_threads` — plot loops and character arcs that span more than one book, with `opens_book`, `resolves_book`, and `relay_handoff` notes (what state must be passed to the next book's skeleton)
    - `series_acceptance_criteria` — structural conditions the full series must satisfy (e.g. "every open loop from book 1 is resolved by book 3", "protagonist arc completes in final book")
    - `book_n_seeds` — for each book, the minimal seed data needed to run that book's skeleton pass (premise fragment, inherited open loops, character starting states)
  - **Feasibility analysis pass**: After the series skeleton is generated, run a second fast high-context pass (`book-series-feasibility` agent profile) that checks: are there enough story threads to fill each book's page target? Are any cross-book handoffs logically impossible? Do character arcs have enough room to pay off? Output a structured `feasibility_report.json` with `passed`, `concerns`, and `structural_blockers`.
  - **Series integrity gate**: Block individual book skeleton passes if `feasibility_report.json` has `structural_blockers`. Emit actionable diagnostics naming the specific thread, character arc, or book assignment that is infeasible.
  - **Book ordering and dependency**: Generate an execution plan (`series_execution_plan.json`) that lists books in recommended writing order with dependency annotations — some books may need to be written before others to lock in canon that later books inherit.
  - **New CLI entry points**: `book-flow series-skeleton` and `book-flow series-feasibility`; both feed into the existing `book-flow skeleton` for each individual book.
  - **New agent profiles**: `book-series-architect.agent.md` (AMD 9B at 192k ctx, series decomposition persona) and `book-series-feasibility.agent.md` (AMD 9B at 192k ctx, structural analyst persona)

- **Todo 90**: Add Living Skeleton — rewrite skeleton after each accepted chapter to lock written content as law
  - **Concept**: The story skeleton (Todo 88) starts as a plan. After each chapter is reviewed and accepted (publisher QA gate passes), the skeleton is rewritten with the actual accepted content merged in. Anything the writer produced that passed review is now **law** — it overrides the original plan in that chapter's frame. Future chapters continue to use the plan, but the plan's past chapters are replaced by ground truth. This creates a continuously improving guide that reflects the story as it was actually told.
  - **Living skeleton fields per chapter frame**:
    - `status`: `"planned"` → `"in_progress"` → `"accepted"` (locked)
    - `planned_content`: original skeleton guidance (preserved for audit)
    - `accepted_content`: summary of what was actually written and accepted — major events, character state changes, open loops opened/progressed/closed, tone achieved
    - `law_items`: specific facts extracted from accepted text that all future chapters must treat as immutable (character decisions, revealed facts, timeline events)
    - `delta_notes`: differences between plan and accepted content, flagged for any future chapter that relied on the original plan
  - **Trigger**: Runs automatically after publisher QA approves a chapter. A lightweight extraction pass (AMD 9B, low `num_predict`) reads the accepted manuscript and rubric/canon artifacts to fill `accepted_content` and `law_items`.
  - **Propagation**: After skeleton rewrite, re-run arc consistency pre-check (no Ollama call — pure JSON comparison) to detect if any future chapter's `chapter_frames` plan now conflicts with newly locked law items. Emit warnings as `skeleton_delta_warnings.jsonl` into the book framework dir.
  - **Series integration**: For series books, propagate `law_items` from the final accepted chapter of book N into `book_n+1_seeds` in the series skeleton, so the next book inherits ground truth rather than planned state.
  - **New agent profile**: `book-skeleton-updater.agent.md` — AMD 9B at 128k ctx, extraction persona only, minimal temperature (0.1), high precision output; receives accepted manuscript + original skeleton chapter frame, returns structured `accepted_content` and `law_items`

- **Todo 91**: Add Style Reformatting Layer — generate alternative book structures from accepted chronological draft
  - **Concept**: The writing pipeline produces a complete, chronologically ordered story first (this is always the primary artifact). After the full book is accepted, a separate style reformatting pass generates alternative structural presentations of the same story — non-linear timelines, multiple POV arrangements, epistolary formats, etc. Each style variant is a structural reorganization of accepted content, not a rewrite of prose.
  - **Research required (defer implementation until research is complete)**:
    - Survey established literary structural styles: in-medias-res, frame narrative, parallel timelines, unreliable narrator structures, epistolary, mosaic/fragmented, reverse chronology, braided narrative, dual timeline
    - For each style: define the algorithmic transformation rules (which chapters move where, what framing text must be generated, what is added vs. reordered), the acceptance criteria (does it preserve all open loops? does it maintain character arc integrity?), and the target reader experience
    - Identify which styles are safe to generate algorithmically (pure reordering) vs. require new prose generation (frame text, chapter headers, interstitial bridges)
    - Produce a `style_catalogue.md` in the repo documenting each style, its transformation rules, and its quality gate requirements
  - **Style reformatting pipeline** (after research):
    - Input: accepted chronological manuscript + accepted living skeleton
    - For each target style: generate a `style_variant_plan.json` (which chapters reorder to which positions, what bridge text is needed), validate structural integrity (no open loop introduced after its resolution point, character state consistency), generate bridge/framing prose where needed, produce final styled manuscript
    - Each style variant is a separate output in `06_final/style_variants/<style_name>/`
  - **Style acceptance gate**: Each variant must pass arc consistency check against the original living skeleton's `law_items` — no variant may alter the story's facts, only their presentation order

- **Todo 92**: Add chronological-first writing enforcement to the pipeline
  - **Concept**: Formalize the rule that all section writing happens in chapter-chronological order, regardless of any style intent. The structural design may plan a non-linear presentation, but the writer always drafts in story-time order. This is enforced, not advisory.
  - Add a `chapter_sequence_validator` that checks the chapter number of each run against `progress_index.completed_chapters` before allowing execution — if chapter N is requested but chapter N-1 is not yet in the accepted state, reject the run with a clear error naming the missing prerequisite
  - Add `chronological_order: true` as a required field in `story_skeleton.chapter_frames` so the presence of the skeleton itself signals the enforcement intent
  - Expose an override flag (`--force-chapter-order`) for diagnostic/recovery use only, logged prominently to run journal

- **Todo 93**: Add skeleton diff report after each chapter acceptance
  - After the living skeleton is updated (Todo 90), produce a human-readable `skeleton_diff_<chapter>.md` in the book framework dir showing: what changed from planned to accepted, which future chapter frames are now affected, and which `law_items` were locked this chapter
  - This gives the operator a per-chapter audit trail of how the story evolved from the original skeleton plan and where divergences accumulated

- **Todo 94**: Add series execution orchestrator — run all books in a series sequentially with state handoff
  - A higher-level CLI command `book-flow series-run` that reads the `series_execution_plan.json` (Todo 89) and orchestrates full runs of each book in dependency order, passing `relay_handoff` data and `law_items` from one book to the next automatically
  - Supports resume: if book N is already complete, skip to book N+1 and inject the prior book's accepted state

- **Todo 95**: Research and catalogue book presentation styles for Todo 91
  - Dedicated research task: survey literary structural styles, define transformation rules for each, produce `style_catalogue.md` as the specification that Todo 91's implementation will execute against
  - Deliverable is documentation only; no code changes

- **Todo 55**: Expose Prometheus metrics for Ollama economics and health
  - Add `/metrics` endpoint with request counters, latency histograms, inflight gauges, fallback counters, quality-gate counters, and per-profile token balance gauges
  - Export route/model labels carefully so the metric cardinality stays bounded and operationally safe

- **Todo 56**: Install observability stack for logs and dashboards
  - Add Prometheus, Grafana, Loki, and Promtail services to deployment with persistent storage and baseline scrape/log shipping configs
  - Keep app metrics, JSONL analytics logs, and dashboard provisioning versioned in the repo

- **Todo 57**: Add GPU exporters for AMD and NVIDIA Ollama endpoints
  - Install NVIDIA DCGM exporter for the NVIDIA route and a ROCm/`rocm-smi` based exporter or sidecar for the AMD route
  - Correlate GPU utilization, VRAM pressure, temperature, and power draw with route/model latency analytics

- **Todo 58**: Build Grafana dashboard pack for Ollama runs
  - Create endpoint-health, model-efficiency, profile-economics, and book-flow diagnostics dashboards from the new metrics/logs
  - Add alert thresholds for p95 latency, error spikes, stalled throughput, and repeated quality-gate failures

- **Todo 59**: Add analytics retention, rollup, and redaction policy
  - Define retention windows and rollups for JSONL analytics logs, reward events, and diagnostics so observability data does not grow without bound
  - Add prompt/output fingerprinting and redaction rules so analytics captures performance signals without leaking unnecessary full-text content

- **Todo 60**: Add diagnostics sampling and backpressure controls
  - Gate verbose per-call diagnostics and stream token logging behind configurable sampling rates and pressure-aware fallbacks
  - Prevent analytics writes from becoming a bottleneck during concurrent runs or long streamed responses

- **Todo 61**: Replace flat reward deltas with spend-and-settle token economics
  - Charge profiles a run-start spend based on endpoint, model class, and elapsed runtime, then settle with payout only on successful quality outcomes
  - Use rolling cost-per-pass and quality efficiency metrics to influence routing instead of relying only on static token balances

- **Todo 51**: Expose profile-score reasoning in API/UI
  - Include top candidate profiles and score factors in status diagnostics for each request
  - Add lightweight UI panel to show why the selected profile won

- **Completed (2026-03-18) / Todo 52**: Add centralized exception-to-HTTP mapper
  - Map `AgentStackError.code` values to consistent API status codes and response payload schema
  - Ensure streaming and non-stream endpoints emit structured error objects uniformly

- **Todo 53**: Add maintenance supervisor agent/service
  - Promote cron/script-based recovery duties into a first-class maintenance actor with watchdog, reconcile, and drill orchestration responsibilities
  - Decide whether it should be a dedicated agent profile, a daemon/service, or a hybrid operational controller
  - Scope responsibilities: continuous watchdog + reconcile, on-demand drill execution, and operational health artifact reporting
  - Prefer maintenance-mode or staging-only drill execution rather than always-on production drill automation

- **Todo 49**: Add proactive warmup and keepalive strategy
  - Warm likely next-stage models before handoff and tune `keep_alive` by pipeline mode
  - Validate memory impact against pressure mode limits

- **Todo 50**: Replace hardcoded quality fallbacks with profile-driven ladders
  - Define fallback ladders in profile/frontmatter and resolve via orchestrator policy
  - Validate fallback compatibility with stage output schema requirements

---

## 6. How to Test & Validate

### Quick Health Check

```bash
# 1. Verify API is running
curl http://127.0.0.1:11888/api/status | python3 -m json.tool | head -20

# 2. Check agent health
curl http://127.0.0.1:11888/api/status | grep -A 2 "health"

# 3. Verify crontab schedule
curl http://127.0.0.1:11888/api/ui-state | grep "cron"

# 4. Check task queue
curl http://127.0.0.1:11888/api/status | grep -E "queued|running|completed|failed"
```

### Running Book-Flow Test

```bash
# Minimal test arguments (required)
python3 -m agent_stack.book_flow \
  --title "Test Flow" \
  --premise "Testing the pipeline" \
  --chapter-title "Chapter One" \
  --section-title "Opening" \
  --section-goal "Introduce setting"

# Full test with output directory
python3 -m agent_stack.book_flow \
  --title "Test Book" \
  --premise "A comprehensive test" \
  --chapter-number 1 \
  --chapter-title "Chapter One" \
  --section-title "Opening" \
  --section-goal "Introduce setting and conflict" \
  --output-dir ./book_project
```

### Monitoring Task Execution

```bash
# Watch changes log in real-time
tail -f book_project/changes.log

# Check specific stage outputs
cat book_project/04_drafts/brief_*.md 2>/dev/null

# View task queue state
curl http://127.0.0.1:11888/api/ui-state | python3 -m json.tool
```

---

## 7. Key Files Reference

| File | Purpose | Priority |
|------|---------|----------|
| `agent_stack/api_server.py` | FastAPI REST orchestrator, task queue, UI state | Core |
| `agent_stack/book_flow.py` | Book pipeline entry point, multi-stage orchestration | Core |
| `agent_stack/orchestrator.py` | Agent task execution, output capture, handoff | Core |
| `agent_stack/static/index.html` | WebUI, crontab timer, spawn controls | UI |
| `agent_stack/agent_profiles/book-publisher.agent.md` | Publisher role definition (CURRENT BLOCKER) | 🔴 |
| `agent_stack/lock_manager.py` | Resource serialization, producer locks | Infrastructure |
| `agent_stack/docker-compose.agent.yml` | Ollama service definition | Deployment |
| `agent_stack/trigger_next_job.sh` | Cron-triggered reconciliation | Automation |
| `book_project/changes.log` | Event log for all task stages | Debugging |

---

## 8. Common Debugging Patterns

---

## 9. Post-MVP Backlog

### Series-of-Books Architecture (Deferred)

This is intentionally deferred until one complete single-book run is reliably repeatable.

**Planned Post-MVP scope**:
- Add series-level canon (`series_bible`, master timeline, recurring cast registry).
- Add cross-book continuity gates pre-write and pre-publish.
- Add series progression metadata (`series_id`, `book_number`, arc milestones) to book-flow requests.
- Add series recap and handoff artifacts between books.

**Why deferred**:
- Current priority is operational reliability and proven end-to-end completion for one book run.
- Series orchestration multiplies state complexity and should be built on a stable single-book foundation.

### Issue: Code Changes Not Reflected

**Symptom**: Modified `api_server.py` but API still returns old responses

**Solution**:
```bash
docker compose -f agent_stack/docker-compose.agent.yml down
docker compose -f agent_stack/docker-compose.agent.yml up -d --build
```

### Issue: Publisher Returns Empty Output

**Symptom**: Book-flow test fails at publisher-brief stage with empty output

**Debug Steps**:
1. Check if `ollama_amd` route is healthy: `curl http://127.0.0.1:11888/api/status | grep -A 3 "ollama_amd"`
2. Verify model availability: `docker exec dragonlair_agent_stack ollama list | grep qwen3.5:27`
3. Inspect changes.log: `cat book_project/changes.log | grep -E "publisher|stage_failure"`
4. Add debug logging in orchestrator output capture (see Todo 31)

### Issue: Spawn Control Buttons Not Working

**Symptom**: Clicking Go/Pause/Stop buttons in WebUI has no effect

**Debug Steps**:
1. Check browser console for fetch errors (F12 → Console)
2. Verify API endpoint: `curl -X POST http://127.0.0.1:11888/api/tasks/[task-id]/spawn-control`
3. Confirm task ID exists: `curl http://127.0.0.1:11888/api/status | grep task_id`
4. Check API logs: `docker logs dragonlair_agent_stack | tail -50`

---

## 9. Development Workflow

### Standard Development Loop

1. **Identify issue** from todo list or bug report
2. **Read/understand** relevant code files
3. **Make changes** to source files (not in Docker container)
4. **Test locally** if possible (Python syntax check, curl API calls)
5. **Rebuild Docker**: `docker compose up -d --build`
6. **Validate** with end-to-end test (book-flow, code-flow, or manual API test)
7. **Mark todo complete** when validated

### Code Change Patterns

- **API endpoints**: Edit `agent_stack/api_server.py`, rebuild, test with curl
- **WebUI/UI**: Edit `agent_stack/static/index.html`, rebuild, refresh browser
- **Agent roles**: Edit `agent_stack/agent_profiles/*.agent.md`, rebuilding not needed (live-reload via volume mount)
- **Orchestration logic**: Edit `agent_stack/orchestrator.py` or `agent_stack/book_flow.py`, rebuild, run integration test

---

## 10. Quick Reference: Current System State

- **API Running**: http://127.0.0.1:11888 ✅
- **Frontend**: http://127.0.0.1:11888/static/ ✅
- **Agent Routes**: ollama_amd (1 slot), ollama_nvidia (3 slots) ✅
- **Task Queue**: Empty (0 queued, 0 running) ✅
- **Crontab Timer**: Working, next run at 19:35:00 UTC ✅
- **Last Test**: Book-flow initiated, awaiting output analysis 🔄

---

## 11. Resources & Documentation

- Full project plan: `AGENT_PLAN.md`
- System architecture: `SYSTEM_NOTES_AND_AUTONOMY.md`
- Usage guide for end-users: `USER_GUIDE.md`
- Bare-metal recovery: `BARE_METAL_RECOVERY.md`
- Agent profiles: `agent_stack/agent_profiles/` (15+ specialized roles)

---

## Next Action

**Immediate**: Complete publisher output investigation (Todo 25)
1. Run book-flow diagnostic test
2. Capture failure point from changes.log
3. Apply appropriate fix (Todo 30/31/32)
4. Re-validate with successful end-to-end test

**Then**: Complete remaining infrastructure validation (Todos 26-29, 33)

---

*This guide should be your companion while developing. Update it as patterns emerge or new issues are discovered.*
