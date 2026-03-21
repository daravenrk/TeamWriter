# Changelog

## [2026-03-21]
- Added adaptive, machine-learnable quality curriculum baseline for book flow:
	- quality gates now support effective thresholds that can tighten over time per book run history
	- persisted learning state in `quality_learning_state.json` (EMA-based baseline)
	- run artifacts now log threshold snapshots and learning updates in `run_journal.jsonl` + `run_summary.json`
	- runtime controls added: `BOOK_QUALITY_ADAPTIVE_*` and `BOOK_QUALITY_MIN_*`
- Added planning/docs updates for cross-publisher delegation + quality curriculum:
	- nested `BookPublisher -> CodePublisher` delegation use case documented
	- quality curriculum roadmap item (Todo 206) promoted and marked baseline in-progress
- Added runtime preset abstraction as the required execution path for agent profiles:
	- profiles must declare `runtime_preset`
	- `agent_stack/runtime_presets.json` now owns route/model/core runtime options
	- profile inline overrides for `route`, `model`, `num_ctx`, and `num_gpu` are rejected by lint/runtime enforcement
- Added orchestrator-side protected resolution for preset-driven runs:
	- `OrchestratorAgent._resolve_profile_runtime_settings(...)` now resolves route/model from presets only
	- NVIDIA preset context is clamped to the configured cap (`AGENT_NVIDIA_MAX_CTX`, default `49152`)
- Clarified diagnostic boundary between orchestrated runs and direct Ollama probes:
	- book-flow and profile-driven runs must be diagnosed from `run_journal.jsonl`, `diagnostics/agent_diagnostics.jsonl`, and `book_project/ollama_run_ledger.jsonl`
	- calibration/probe utilities that call `/api/generate` directly intentionally bypass runtime preset abstraction and are not evidence that book-flow itself bypassed preset governance

## [2026-03-18]
- Added strict profile linting + runtime guardrails:
	- `agentctl profile-lint`
	- max rendered system prompt size enforcement
	- fail-fast profile validation at startup/reload
- Added per-profile execution policy support and enforcement:
	- `timeout_seconds`, `retry_limit`, `allowed_routes`, `model_allowlist`
- Added structured stage-output schema validation for book flow:
	- new shared validator in `agent_stack/output_schemas.py`
	- schema checks integrated into `run_stage(...)` before custom quality gates
- Updated roadmap/backlog/docs to reflect completed hardening work and new follow-up todos (71/72/73)
- Added consolidated state review document:
	- `PROJECT_STATE_REVIEW_2026-03-18.md`

## [2026-03-17]
- book-researcher agent updated to qwen3.5:14b (128k context window)
- Research agent now uses internet, news, and Wikipedia for research
- Research output is structured, data-driven, and includes dossiers/fact cards
- Documentation updated to reflect new research agent behavior and model
