# Changelog

## [2026-03-22]
- Added sparse WebUI book-flow request normalization in API:
	- `/api/book-flow` now normalizes blank fields via `_normalize_book_flow_request(...)`
	- empty title/premise/chapter/section metadata now auto-fills to safe defaults
	- numeric guardrails added for chapter number, writer word target, and retries
	- allows intentional minimal-input launches where downstream stages infer missing world details
- Added run consistency DR validator and command wrapper:
	- new script `agent_stack/scripts/run_consistency_dr.py`
	- new command `bin/run-consistency-dr`
	- validates run-journal invariants, including no progress after terminal run events
	- initial live execution surfaced real inconsistencies (terminal seal followed by stage progress)
- Fixed writer-bootstrap crash in book flow after canon stage:
	- added missing `build_section_consistency_sections(...)` in `agent_stack/book_flow.py`
	- run was previously terminating with `NameError` immediately after `stage_complete canon`
	- helper now emits deterministic `consistency_sections` payload used by writer/reviewer stages
	- validated with `python3 -m py_compile agent_stack/book_flow.py`
- Documented live-run incident findings and operational recommendations:
	- recorded stage retry behavior (`architect_outline` and `canon` retries before success)
	- recorded non-fatal telemetry permission warnings on `cli_runtime_activity.json`
	- recorded `datetime.utcnow()` deprecation warning as follow-up hardening item

## [2026-03-21]
- Stabilized early book-flow stages after a control-flow failure in the orchestrator:
	- fixed `OrchestratorAgent._invoke_with_triage()` so successful agent calls return their actual model output instead of falling through to `None`
	- fixed retry-loop progression in `handle_request_with_overrides()` by incrementing `attempt`
	- repaired `_build_ml_shadow_recommendations()` after a bad auto-patch corrupted its early-return path
	- validated `agent_stack/orchestrator.py` with `python3 -m py_compile`
- Reworked research-stage bootstrap so the researcher can produce grounded input before synthesis:
	- `book_flow.py` now gathers `source_packets.json` before the research LLM call
	- sources now include Wikipedia OpenSearch + summary fetches, Free Dictionary API lookups, DuckDuckGo HTML snippet scraping when `beautifulsoup4` is available, and a local premise anchor
	- research prompt now includes structured source packets and rendered source-packet markdown for grounding
	- fallback research dossier was corrected to remove stale hardcoded sci-fi content and now reflects the actual book brief, chapter goal, and gathered source packet anchors
	- smoke test for `bootstrap_simple_research_packets()` returned 7 non-empty packets for the Chapter 1 Mokeys Pay Day sample input
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
