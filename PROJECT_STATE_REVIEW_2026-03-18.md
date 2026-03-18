# Dragonlair Project State Review (2026-03-18)

## Scope
This review summarizes the current Dragonlair codebase and operations state across runtime code, orchestration policy, book-flow pipeline quality controls, deployment surface, and operator documentation.

## Repository Snapshot
- Python modules: 23
- Markdown documents: 149
- Active branch: `main`
- Primary remote: `origin` -> `https://github.com/daravenrk/TeamWriter.git`

## Current Runtime Topology

### Core services
- `dragonlair_agent_stack` (FastAPI + Web UI): host port `11888`
- `ollama_nvidia` (NVIDIA route): host port `11434`
- `ollama_amd` (AMD route): host port `11435` (container port `11434`)
- `fetcher` (research fetch service): host port `11999`

### Current listener assessment
- Meaningful listeners for Dragonlair operations:
  - `11434`, `11435`, `11888`, `11999`
- Infrastructure listeners (non-Dragonlair app logic):
  - `22` (SSH)
  - loopback DNS listeners on `53`
- VS Code forwarded ports can include stale entries that are not active listeners.

## Implemented Since Last Baseline

### Control-plane hardening
- Added strict profile lint workflow and CLI command:
  - `agentctl profile-lint`
- Added rendered system-prompt guardrail:
  - `AGENT_MAX_SYSTEM_PROMPT_CHARS`
- Added runtime profile policy parsing/enforcement:
  - `timeout_seconds`
  - `retry_limit`
  - `allowed_routes`
  - `model_allowlist`

### Routing and execution policy
- Orchestrator uses weighted profile scoring (keywords, priority, token balance, quality outcomes, deterministic tie-breaks).
- Plan output now exposes effective timeout/retry policy fields.
- Runtime route/model policy violations now fail fast with explicit diagnostics.

### Book-flow schema safety
- Added shared structured-output schema registry:
  - `agent_stack/output_schemas.py`
- `run_stage(...)` validates JSON stage payloads against named schemas before custom gates.
- Existing quality gates remain in place for semantic thresholds (scores, blocking issues, etc.).

## Documentation State (Now)

### Updated and aligned
- `AGENT_PLAN.md` reflects completed phase-1 hardening items.
- `DEV_NEXT_STEPS.md` reflects completed Todo 34 and Todo 39, and includes new todos:
  - Todo 71: Claude artifact gap review
  - Todo 72: Open-port audit and least-exposure hardening
  - Todo 73: Full end-to-end run after schema enforcement
- `agent_stack/README.md` documents profile lint and profile execution policy fields.

### Remaining documentation gap
- User-facing docs still need a dedicated, centralized "port matrix + exposure policy" section in all operator entry points.

## Production Gap Analysis

### Strengths
- Strong md-driven profile architecture with runtime linting and policy enforcement.
- Better resilience with retries, quarantine logic, and route/model guardrails.
- Improved stage reliability through centralized schema checks.

### Gaps to close next
1. End-to-end success proof under live load (book-flow with clean completion and recovery verification).
2. Port least-exposure hardening defaults (especially fetcher and optional LAN exposure).
3. Observability depth (metrics/telemetry stack and trend dashboards still pending).
4. External comparative review (Claude-produced artifacts) to identify missed production patterns.

## Recommended Execution Order
1. Complete Todo 72: enforce least-exposure port bindings and document mode-specific exposure.
2. Complete Todo 73: run and capture one clean end-to-end book-flow pass post-schema changes.
3. Complete Todo 71: Claude artifact comparative review and convert findings into concrete backlog actions.
4. Continue observability and operational hardening (Todos 55-61).

## Acceptance Signal For "Production-Ready Candidate"
- One repeatable end-to-end chapter run succeeds without forced completion.
- Recovery drill passes from interruption to successful resume.
- Port policy defaults to minimum exposure with explicit opt-in LAN mode.
- Operator docs and runbooks are in sync with the deployed compose/runtime behavior.