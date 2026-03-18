# Changelog

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
