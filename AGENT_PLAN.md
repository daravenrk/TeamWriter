## Ollama AMD/NVIDIA Service Mirroring (March 2026)
- Each Ollama instance (AMD/NVIDIA) must have its own container, port, and model/manifest directory.
- NVIDIA: `/ai/ollama-nvidia/models` (port 11434)
- AMD: `/ai/ollama-amd/models` (port 11435)
- No cross-mounting or sharing of model data.
- All GPU runs must be enforced per container/device.
- See `docker-compose.ollama.yml` for the canonical service configuration.
# AGENT_PLAN.md
# Documentation Requirement

## Model Storage Enforcement (March 2026)
- All model manifests and blobs must be stored in `/ai/ollama-nvidia/models` (NVIDIA) or `/ai/ollama-amd/models` (AMD) only.
- The `/home/daravenrk/dragonlair/model-sets` directory is for text lists/config only—no manifests or blobs allowed.
- Periodically audit and delete any model data found in `model-sets` to prevent confusion and ensure correct operation.
All procedures, operational steps, troubleshooting, and recovery workflows must be fully documented in the markdown files. This includes:
- How to start, stop, and restart all agent services/containers (with and without Docker Compose)
- How to trigger, monitor, and debug book runs
- How to interpret logs and error messages
- How to perform backup, restore, and recovery
- How to update, patch, and validate the system
- How to escalate or trace persistent errors
No operational knowledge should be left undocumented. Every step must be reproducible by following the markdown documentation alone.

## Objective
Build a fully autonomous backend agent control system for Dragonlair using md-defined behavior profiles, lock/triage safeguards, and an operator CLI with streaming support.

## Adaptive Creation Platform Expectations (March 2026)

### Core Principles
- Abstraction over hardcoding
- Options over single-path execution
- User control over automation
- Documentation over assumption
- Conversation over configuration
- Assistive intelligence over static UI

### Control Philosophy
- Minimal interaction is a usability goal, not a control reduction goal.
- The system must provide both:
	- a guided default path for low-friction execution
	- a detailed control path for inspection, overrides, and operator intervention

### Architectural Direction
- Move from fixed flow branching to publisher-driven orchestration.
- Publishers must be modular and pluggable (book publisher, code publisher, future publishers).
- Each publisher defines its own execution model and artifacts while using shared runtime guardrails.
- Intent analysis selects publisher and plan strategy; user approval gates execution.

### Assistive Intelligence Layer Requirement
- Introduce a conversational mediation layer between user and execution runtime.
- The layer must maintain persistent user memory (preferences, prior projects, tendencies, goals).
- The layer must produce multiple structured options and request user selection before full execution.
- Long-term: this layer becomes the primary interface for Dragonlair and expands toward Linux/Ubuntu interaction mediation.

## Current Completed Baseline
- [x] Python class-based orchestrator + subagent stack
- [x] md profile behavior files with hot reload
- [x] Profile-driven route/model/options selection
- [x] Lock manager for edit safety + endpoint anti-spam
- [x] Hung/failure triage with quarantine + fallback
- [x] Context estimation preflight command
- [x] CLI control layer (`agentctl`) with streaming

## Phase 1: Control Plane Hardening
- [x] Add profile lint command and strict schema checks
- [x] Add max system-prompt size guardrails
- [x] Add per-profile timeout and retry settings
- [x] Add profile policy fields for route/model allowlists

## Phase 2: Autonomous Execution Engine
- [ ] Add persistent task queue (SQLite)
- [ ] Add worker loop for queued requests
- [ ] Add checkpointed task state transitions
- [ ] Add retry strategy and dead-letter handling

## Debug Attribute Error Blocker (March 2026)
- [x] Workspace-wide audit and patch for SimpleNamespace debug/no_debug attributes
- [x] Patched all entrypoints (API, CLI, error handling)
- [ ] Persistent error remains: likely stale process or external code path
- [ ] Next: Restart all agent containers/services to ensure latest code is running
- [ ] If error persists: Add runtime logging to trace faulty args construction
- [ ] Escalate for deeper codebase review if error persists after restart

## Phase 3: Self-Healing
- [ ] Add endpoint watchdog probes and health scoring
- [ ] Add circuit breaker logic by route/profile
- [ ] Add dynamic fallback chains from policy
- [ ] Add auto cooldown expiry and requalification checks

## Phase 4: Operations + Observability
- [ ] Add metrics endpoint (latency/failure/quarantine/queue)
- [ ] Add CLI dashboard mode for live status
- [ ] Add structured run logs and trace IDs
- [ ] Add alert hooks for repeated failures

## Phase 5: Recovery + Release Discipline
- [ ] Add scripted restore validation workflow
- [ ] Add regression test suite for routing/stream/triage
- [ ] Add chaos tests for hangs/timeouts/failover
- [ ] Version and freeze profile bundles per release

## Phase 6: Publisher Abstraction + Assistive Layer
- [ ] Add `publisher` contract interface (analyze_intent, generate_options, validate_plan, execute_plan, stream_progress)
- [ ] Add publisher registry and runtime selection service
- [ ] Wrap existing book flow as `BookPublisher` adapter (no behavior change first)
- [ ] Implement independent `CodePublisher` flow with structured output contract
- [ ] Add governed nested delegation: `BookPublisher` can launch a linked `CodePublisher` child project when the manuscript requires a real software artifact
- [ ] Replace hard book-vs-code global exclusion with policy-aware parent/child run controls and audit trail
- [ ] Add intent-to-options API endpoint and approval checkpoint model
- [ ] Add strategy version stamping for every approved plan and run artifact

## Phase 6.5: Adaptive Quality Curriculum
- [x] Add baseline adaptive threshold framework for book quality gates (effective thresholds vs base floors)
- [x] Persist per-book quality learning state and threshold snapshots in run artifacts
- [ ] Replace baseline EMA curriculum with ML recommendation-policy updates using shadow telemetry + user feedback
- [ ] Add curriculum safety controller (bounded ramp, rollback trigger, stagnation detector)
- [ ] Add operator dashboard view for threshold progression and quality trend lines

## Phase 7: Persistent Memory + Conversational Front-End
- [ ] Add user memory profile schema (preferences, history, goals, interaction style)
- [ ] Add memory-aware planning prompts and retrieval layer
- [ ] Add assistant-facing conversation API as primary entrypoint
- [ ] Add advanced control panel for route/model/context overrides without exposing low-level complexity by default
- [ ] Add OS integration roadmap for Ubuntu/Linux task mediation (phased, permission-scoped)

## Operator CLI Deliverables

## March 2026: Research Agent Update
- book-researcher: qwen3.5:14b, 128k context, internet/news/Wikipedia research, structured output
- [x] `agentctl profiles`
- [x] `agentctl plan "..."`
- [x] `agentctl once "..."`
- [x] `agentctl --stream once "..."`
- [x] `agentctl --stream chat`
- [x] `agentctl health`
- [ ] `agentctl queue` and `agentctl dashboard`

## Near-Term Execution Order
1. Implement profile lint + guardrails.
2. Implement persistent queue worker mode.
3. Implement watchdog + circuit breaker.
4. Add queue/dashboard CLI subcommands.
5. Run full backup/restore drill and document sign-off.
