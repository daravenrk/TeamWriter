# Development Next Steps & Process Guide

---

## 🎯 ML Model Selector Implementation (2026-03-22)

**COMPLETE**: Full ML infrastructure for learning optimal (model, context) assignments per profile.

**See**: [ML_IMPLEMENTATION_SUMMARY.md](ML_IMPLEMENTATION_SUMMARY.md) and [AGENT_ML_SELECTOR_GUIDE.md](AGENT_ML_SELECTOR_GUIDE.md)

**Status**: ✅ Core integration done; ⏳ Awaiting book_flow.py to call `orchestrator.record_ml_outcome()`

**Available Commands**:
```bash
agentctl ml-status         # Show training data count + readiness
agentctl ml-retrain        # Retrain from 50+ accumulated outcomes (weekly job)
```

**Next**: Add outcome recording to book_flow.py after each task execution. After 50+ tasks, run weekly retraining.

---

## 🚨 Book Run Incident + Correction Start (2026-03-22)

### Discoveries from live run (`the-sound`, `20260322-010610-ch01-first-sound`)

- Run progressed through `publisher_brief`, `research`, `architect_outline`, `chapter_planner`, and `canon`.
- `architect_outline` required one retry (missing `master_outline_markdown` on attempt 1, passed on attempt 2).
- `canon` required two retries for schema conformance (`open_loops` type mismatch, then `style_guide` type mismatch), then passed on attempt 3.
- After canon completion, run terminated with hard runtime exception:
  - `NameError: name 'build_section_consistency_sections' is not defined`
  - Call site: writer bootstrap section consistency initialization in `book_flow.py`.
- Non-fatal warnings observed:
  - Repeated permission-denied warnings for `book_project/cli_runtime_activity.json` updates.
  - `datetime.utcnow()` deprecation warnings.
  - `worldbuilding_enrichment` warning when model override is blocked by strict runtime preset mode.

### Correction started (implemented)

- Added missing helper `build_section_consistency_sections(...)` to `agent_stack/book_flow.py`.
- Function now builds deterministic `consistency_sections` payload with expected fields:
  - `chapter_number`, `chapter_title`, `generated_at`, `active_section_index`, `sections[]`, `ledger[]`
  - per-section expectations, situations, tracking targets, coverage placeholders, and timestamps
- Output shape aligns with previously successful historical artifact format.
- Validation: `python3 -m py_compile agent_stack/book_flow.py` passed.

### Suggested follow-up hardening

1. Add static lint gate (`ruff`/`pyflakes`) in CI to catch undefined names before runtime.
2. Add a focused preflight smoke test that executes run flow through writer bootstrap (post-canon) with mocked stage outputs.
3. Add run-level terminal event emission (`run_failure`) for uncaught exceptions in direct CLI runs so failures are always explicit in `run_journal.jsonl`.
4. Normalize file ownership/permissions for `book_project/cli_runtime_activity.json` and lockfile to restore reliable live telemetry updates.
5. Replace `datetime.utcnow()` usages with timezone-aware UTC calls to remove deprecation noise and future break risk.

### DR Validation Added (2026-03-22)

- Added run consistency DR validator script: `agent_stack/scripts/run_consistency_dr.py`
- Added wrapper command: `bin/run-consistency-dr`
- Purpose:
  - Scan historical and active run journals under `book_project/*/(runs|run_history)`
  - Enforce terminal-event consistency invariants
  - Detect sealed-then-progressing runs (events continue after `run_success` / `run_failure`)
  - Return non-zero exit on invariant violations for cron/automation use
- Examples:
  - `bin/run-consistency-dr --max-runs 50`
  - `bin/run-consistency-dr --json > book_project/drill_reports/run_consistency_latest.json`

### Session Update: Sparse WebUI + DR Findings (2026-03-22)

- WebUI sparse-input hardening is now implemented in API launch path:
  - `create_book_flow` now normalizes blank request fields via `_normalize_book_flow_request(...)` in `agent_stack/api_server.py`
  - empty `title/premise/chapter_title/section_title/section_goal/audience/genre/tone` are auto-filled with safe defaults
  - numeric fields are bounded (`chapter_number >= 1`, `writer_words >= 200`, `max_retries >= 0`)
  - objective: allow operator to submit minimal prompt context and force the stack to infer missing details from canon/framework
- Run-consistency DR validator was executed against current artifacts and detected real consistency violations:
  - `test-book-flight/runs/20260322-021933-ch01-opening`: `run_failure` was followed by further progress events (`stage_attempt_*`, `stage_complete`, `stage_instantiated`)
  - `test-book-flight/run_history/20260322-015949-ch01-opening`: missing `run_start` and progress events after terminal event
  - this confirms DR tooling is catching exactly the class of non-deterministic completion drift we want to prevent

### New TODOs From This Session

- [ ] Add hard runtime seal guard after terminal events in `book_flow` and/or API scheduler:
  - once `run_success|run_failure|forced_completion` is written, reject/ignore any subsequent stage-progress events for that run ID
  - emit explicit `sealed_run_progress_blocked` journal event when blocked
- [ ] Add automatic stale-active-run sanitizer on API startup/reconcile:
  - clear `cli_runtime_activity.active_runs` entries whose process/run dir is no longer valid
  - ensure task ledger status transitions are idempotent (`running -> cancelled|failed`) with reason field
- [ ] Add scheduled DR audit + report archiving:
  - run `bin/run-consistency-dr --json` on a schedule
  - write timestamped outputs under `book_project/drill_reports/`
  - include summary status marker file for automation (`pass|fail`)
- [ ] Add WebUI/API visibility for DR status:
  - expose latest run-consistency DR result in `/api/status` (or dedicated `/api/dr/run-consistency`)
  - show failing run count + top issues in Live View
- [ ] Add CI gate for synthetic run journal invariants:
  - include fixtures that assert no progress events after terminal events
  - fail CI when invariant checks regress


---

## Book Flow Risk Register (2026-03-20)

Full pipeline audit conducted. Issues ranked by severity.

### 🔴 FIXED — 13 tracked fixes applied across recent stabilization work

| # | Location | Fix Applied |
|---|----------|-------------|
| F1 | `book_flow.py` — `local_task_memory` | `recent_chapter_summaries` was the unfiltered full list, now capped to last 5. Prevented context window overflow after several chapters. |
| F2 | `book_flow.py` — `score_arc_consistency()` | Open loops window capped to 10 most recent. Character arc window capped to 5 most recent entries. Without this, later chapters would always fail arc consistency (full 30-loop accumulation required in a single session's notes). |
| F3 | `book-publisher-brief.agent.md` | `num_predict` raised from `700` → `1800`. 700 tokens cannot fit a valid JSON brief with 5+ constraints + 5+ acceptance_criteria + all required fields. Would cause systematic retries on every book run. |
| F4 | `book_flow.py` — `canon_contract build_contract` | Canon stage now receives `book_premise` and `book_details` (title, genre, tone, audience) from `context_store`. Previously only received the publisher brief output, which sometimes lacked premise context. |
| F5 | `orchestrator.py` — `_collect_fallback_routes` | Method was called inside `_build_ml_shadow_recommendations()` but never defined; every `POST /api/book-flow` request returned HTTP 500. Added method and rebuilt/redeployed container (2026-03-20). |
| F6 | `scripts/interruption_recovery_drill.sh`, `scripts/failure_path_integration_drill.sh` | `json_get()` helper used heredoc — consumed stdin so piped JSON was discarded; changed to `python3 -c '...' "$expr"`. `log()` was writing to stdout, corrupting command-substitution JSON captures; redirected to stderr. Python quoting bug in `list_active_tasks()` f-string inside single-quoted shell string; converted to `.format()` style. (2026-03-20) |
| F7 | NVIDIA route profiles — GPU layer 33/33 100% GPU achievement (book stages) | **FIXED (2026-03-20 COMPLETE)**: Root cause confirmed as VRAM overcommit from large context + output layer footprint. Final validated config: `qwen3.5:4b`, `num_ctx: 8192`, `num_gpu: 99` on GTX 1660 SUPER (6 GiB). Verified in Ollama logs: `offloaded 33/33 layers to GPU`. Peak observed VRAM: ~5596 MiB used, ~152 MiB free. |
| F8 | Profile GPU option drift (`num_gpus` vs `num_gpu`) + validator mismatch | **FIXED (2026-03-20)**: Standardized all profiles to `num_gpu` (singular, Ollama API key). Removed stale `num_gpus` entries. Updated validator allowlist + loader compatibility path. Profile lint now passes (`26 profiles, 0 errors`). |
| F9 | NVIDIA context cap enforcement — KvSize 16384 leak on architect/writer stages | **FIXED (2026-03-21)**: Missing `AGENT_NVIDIA_MAX_CTX_BY_MODEL` env var in `docker-compose.agent.yml` allowed context estimator to select higher-context presets without cap enforcement, causing `qwen3.5:4b` to load at `num_ctx=16384` (violates GPU-only policy: `32/33` layers on GPU). Root cause: when a preset lacks explicit `num_ctx`, clamping in `_resolve_profile_runtime_settings()` doesn't trigger. Added explicit env var: `AGENT_NVIDIA_MAX_CTX_BY_MODEL: '{"qwen3.5:4b":8192,"qwen3.5:2b":65536,"qwen3.5:0.8b":131072}'` with validated ceilings from matrix calibration. Rebuilt container (2026-03-21). |
| F10 | `orchestrator.py` — `_invoke_with_triage()` success path | **FIXED (2026-03-21)**: success return path was unreachable after `raise wrapped`, so successful model calls returned `None`. This caused early stages like research, architect outline, chapter planner, and canon to log `raw_output: null` and fail downstream schema gates. Added a proper `else:` success branch and validated compile. |
| F11 | `orchestrator.py` — retry progression + ML shadow corruption | **FIXED (2026-03-21)**: retry loop in `handle_request_with_overrides()` now increments `attempt`; `_build_ml_shadow_recommendations()` was restored after a malformed patch injected exception/retry lines into the method body and broke its `ml_enabled` early exit. |
| F12 | `book_flow.py` — research bootstrap grounding | **FIXED (2026-03-21)**: researcher now builds `source_packets.json` before the LLM call using Wikipedia OpenSearch + summaries, Free Dictionary API, DuckDuckGo HTML snippets when `beautifulsoup4` is available, and the local premise/chapter anchor. Prompt grounding improved from effectively empty packets to a validated smoke-test result of 7 non-empty packets for the Chapter 1 Mokeys Pay Day sample. |
| F13 | `book_flow.py` — fallback research dossier contamination | **FIXED (2026-03-21)**: removed stale hardcoded sci-fi fallback text (`signal-processing engineer`, `deep-space telescope array`) and replaced it with book-brief-driven fallback language plus evidence anchors from gathered source packets. |

### 🟠 KNOWN RISKS — Not yet fixed, monitor in next run

| # | Severity | Location | Description | Predicted Failure |
|---|----------|----------|-------------|-------------------|
| R1 | HIGH | `book_flow.py` publisher_brief contract | `inputs=context_store` passes the ENTIRE context_store to the publisher brief agent (24k ctx). As runs accumulate `rolling_memory`, this overflows and the model receives a truncated context with no indication it happened. | Silent truncation → missing brief fields → gate retry loop |
| R2 | HIGH | `score_arc_consistency()` | Open-loop substring match capped at `[:50]`. If the LLM rephrases a loop even slightly, the carry-forward match fails and arc score drops below 0.6. | Spurious `StageQualityGateError` on arc consistency for long-running books |
| R3 | MEDIUM | `book_flow.py` line 3733 | `forced_completion = True` is not surfaced in the API task response body or task status field. UI shows task as "completed" even when publisher never approved. | Operator doesn't know chapter was force-completed without inspecting `run_journal.jsonl` |
| R4 | MEDIUM | `story_architect_review` stage | No numeric quality gate on `concept_validation` or `structure_validation` scores (unlike developmental_editor which requires ≥4). Any score passes. | Low-quality structural validation silently passes, feeding bad structure to editors |
| R5 | LOW | AMD route concurrency=1 | All AMD stages queue: research → N×section_review → assembly_review → developmental_editor → continuity → publisher_qa. For a 4-section chapter = 9+ sequential AMD calls. NVIDIA route sits idle during AMD-only stages. | Long total wall time per chapter run (~30-60 min per chapter at current model speeds) |
| R6 | LOW | `rolling_memory.json` | Chapter summaries are appended per-run with no pruning. After 50 chapters this file could reach several MB and the full rolling_memory is written to `context_store.json` per run. | File I/O slowdown; risk of `context_store` exceeding serialization limits |
| R7 | INFO | `book-researcher` profile | Uses `qwen2.5-coder:14b` (a code model) for book research. Not broken but not ideal — coder training doesn't match research output format. | Occasional structured output failures from the research stage |
| R8 | HIGH | Task ledger reload / cancellation persistence | Previously cancelled long-running `book-flow` tasks reappeared as running after API restart, causing unexpected concurrent load and VRAM pressure. | Hidden background runs can trigger fallback, route starvation, and false diagnostics |
| R9 | HIGH | API image/runtime drift | Editing host files without image rebuild can leave container running stale validator/loader code. | Startup loops, profile validation regressions, and inconsistent route behavior |
| R10 | MEDIUM | Research bootstrap runtime parity | DuckDuckGo snippet scraping now works when `beautifulsoup4` is available in the executing Python environment. Host smoke tests pass, but containerized runs will only get DDG snippets after dependency parity or fetcher-service integration is completed. Wikipedia + dictionary fallback still works without it. | Host-only success can mask weaker container research grounding |
| R11 | FIXED (2026-03-21) | AMD context ceiling missing enforcement | `qwen2.5-coder:14b` has `n_ctx_train=32768`. Researcher profile was requesting 128000 which Ollama silently capped to 32768. Added `AGENT_AMD_MAX_CTX_BY_MODEL` env var + AMD ctx clamping in orchestrator (parallel to NVIDIA). Updated researcher preset to `amd-qwen25-coder-14b-32768`. Proofreader preset updated from 49152 → 65536 (validated ceiling for qwen3.5:2b). |

### Immediate Next Steps (2026-03-21 book-flow stabilization)

1. Run a full Chapter 1 end-to-end book flow and verify that `research`, `architect_outline`, `chapter_planner`, and `canon` all produce non-null `raw_output` and pass their stage gates.
2. Rebuild and redeploy the agent runtime image so research bootstrap dependencies match the code path actually used by API-triggered runs.
3. Decide between two durable scraping paths for the researcher:
  - add `beautifulsoup4` to the main runtime image and keep inline DDG snippet gathering in `book_flow.py`
  - or call the existing fetcher service from the research bootstrap so scraping stays isolated in its own service
4. Add a relevance filter for bootstrap packets so obviously off-topic Wikipedia summaries are dropped before they enter the research prompt.
5. Measure prompt growth from `source_packets` and trim packet count or packet text if the research prompt starts approaching prompt-size or context caps.

### Follow-up TODOs (Evaluation Queue)

- [ ] Add a hard-fail preset conformance check in orchestrator execution path:
  - verify final `(route, model, num_ctx, num_gpu)` matches an approved runtime preset tuple
  - reject request if any field drifts from approved tuple
- [ ] Add a post-run policy audit artifact for each book run:
  - write `policy_runtime_audit.json` under run directory with stage-by-stage resolved route/model/options
  - include pass/fail for GPU-only and context cap compliance
- [ ] Build a deterministic routing regression test suite:
  - fixed prompts (small/medium/large) per profile
  - assert selected `runtime_preset` is unchanged across prompt sizes
- [ ] Add a startup guard that compares profile `runtime_preset` names against runtime preset registry and fails fast with a concise diff report.
- [ ] Add AMD/NVIDIA option semantics checks:
  - enforce `num_gpu=-1` compatibility in preset loader and profile lint
  - flag `num_gpu=2` on AMD as invalid for full-offload policy
- [ ] Add a one-command verification script for approved matrix conformance:
  - runs smoke calls for critical profiles
  - captures offload lines from both Ollama logs
  - exits non-zero if any stage is not fully offloaded
- [ ] Update docs for semantic consistency:
  - replace stale references that imply AMD `num_gpu=2` means two cards
  - document that `num_gpu` is layer offload count/sentinel in Ollama
- [ ] Evaluate whether profile quality fallback should be constrained to approved preset tuples only (not just approved route/model).
- [ ] Add CI gate:
  - run profile lint + runtime preset load + deterministic planner check in pipeline
  - block merge on any preset drift or missing preset references
- [ ] Add weekly drift review task:
  - sample latest successful runs
  - compare observed route/model/options against standardized matrix
  - log deltas and corrective actions in this file

### 🔵 PIPELINE STAGE → ROUTE MAP (STANDARDIZED)

| Stage | Profile | Route | Model | num_ctx | num_gpu | num_predict |
|-------|---------|-------|-------|---------|---------|-------------|
| publisher_brief | book-publisher-brief | NVIDIA | qwen3.5:4b | 8192 | 99 | 1800 |
| research | book-researcher | AMD | qwen2.5-coder:14b | 32768 | -1 | 1800 |
| architect_outline | book-architect | NVIDIA | qwen3.5:4b | 8192 | 99 | 1800 |
| chapter_planner | book-chapter-planner | NVIDIA | qwen3.5:4b | 8192 | 99 | 1800 |
| canon | book-canon | NVIDIA | qwen3.5:4b | 8192 | 99 | 1800 |
| canon failover | book-canon-nvidia | NVIDIA | qwen3.5:4b | 8192 | 99 | 1800 |
| writer_section_N | book-writer | NVIDIA | qwen3.5:4b | 8192 | 99 | 2200 |
| section_review_N | book-continuity | AMD | qwen3.5:27b | 49152 | -1 | 1800 |
| story_architect_review | book-architect | NVIDIA | qwen3.5:4b | 8192 | 99 | 1800 |
| assembler | book-assembler | NVIDIA | qwen3.5:4b | 8192 | 99 | 1800 |
| assembly_review | book-continuity | AMD | qwen3.5:27b | 49152 | -1 | 1800 |
| developmental_editor | book-developmental-editor | AMD | qwen3.5:27b | 49152 | -1 | 1800 |
| line_editor | book-line-editor | NVIDIA | qwen3.5:4b | 8192 | 99 | 1800 |
| copy_editor | book-copy-editor | NVIDIA | qwen3.5:4b | 8192 | 99 | 1400 |
| proofreader | book-proofreader | NVIDIA | qwen3.5:2b | 65536 | 99 | 1200 |
| session_reviewer | book-continuity | AMD | qwen3.5:27b | 49152 | -1 | 1800 |
| continuity | book-continuity | AMD | qwen3.5:27b | 49152 | -1 | 1800 |
| publisher_qa | book-publisher | AMD | qwen3.5:9b | 49152 | -1 | 1400 |

### 🔵 GPU-Only Standardization Plan (All Available Models)

Hardware baseline:
- NVIDIA: GTX 1660 SUPER, 6144 MiB VRAM
- AMD: 2x Navi 21, 17163091968 B each (~16 GiB each)

Global policy:
- Enforce explicit full-offload `num_gpu` on every profile/runtime preset (`-1` sentinel in Ollama for all GPU layers).
- Keep `num_ctx` as high as possible while preserving full layer offload.
- Never accept CPU fallback silently.
- Promote only validated `(model, num_ctx)` pairs into production profiles.

NVIDIA available models (full-GPU viability classes):
- **Validated full-GPU**: `qwen3.5:4b` at `num_ctx=8192` (`33/33` verified).
- **Validated full-GPU with measured ceiling**: `qwen3.5:2b` at `num_ctx=65536` (`25/25` verified), `qwen3.5:0.8b` at `num_ctx=162816` (`25/25` verified).
- **Likely full-GPU with tuning**: `qwen2.5-coder:3b`, `qwen2.5-coder:1.5b`, `llama3.2:1b`, `llama3.2:3b`, `codegemma:2b`.
- **Likely not full-GPU on 6 GiB (route away or reduce model size)**: `qwen3.5:9b`, `dragonlair-book-nvidia:latest`, `dragonlair-book-nvidia-ctx:latest`, `codeqwen:7b`, `codellama:7b`, `codegemma:7b`, `qwen2.5-coder:7b`.

AMD available models (multi-GPU full-offload target):
- **Primary production set**: `qwen2.5-coder:14b`, `qwen3.5:9b`, `qwen3.5:27b`.
- **Secondary available set for calibration**: `deepseek-coder-v2:16b`, `starcoder2:15b`, `codellama:13b`, `dragonlair-active:latest`, `dragonlair-coding-amd:latest`, `dragonlair-book-amd:latest`.

Calibration workflow (required before final max-context lock):
1. Force clean model load on target route and run with `num_gpu` hard set.
2. Sweep `num_ctx` upward (binary search) until just before fallback.
3. Record max stable context with all layers on GPU.
4. Lock that `num_ctx` in profile and record rationale in run journal.
5. Re-run `load_validation.py` + interruption drill after each promoted change.

Latest NVIDIA calibration evidence:
- `qwen3.5:0.8b`: passes with full offload through `num_ctx=162816` (`25/25`), fails allocation at `163328` and above on `http://localhost:11434` with `num_gpu:99`.
- `qwen3.5:2b`: passes with full offload through `num_ctx=65536` (`25/25`), fails allocation at `98304` and above on `http://localhost:11434` with `num_gpu:99`.
- `qwen3.5:4b`: validated full-GPU at `num_ctx=8192` (F7 baseline: `33/33` offload + ~5596 MiB peak VRAM on 6 GiB card).

**Context Cap Enforcement (2026-03-21):**
- Explicit cap map added to `docker-compose.agent.yml` environment:
  ```
  AGENT_NVIDIA_MAX_CTX_BY_MODEL: '{"qwen3.5:4b":8192,"qwen3.5:2b":65536,"qwen3.5:0.8b":131072}'
  ```
- Prevents context estimator from selecting higher-context presets that exceed GPU memory limits
- Hardcoded default exists in `orchestrator.py` (line 174–183) but explicit env var locks policy at container startup
- Fix ensures selector ranks `nvidia-qwen35-4b-8192` preset first and caps any overrun attempts to 8192

### 🔵 QUALITY GATE THRESHOLD MAP

| Stage | Gate Type | Threshold | Blocks? |
|-------|-----------|-----------|---------|
| publisher_brief | field count | ≥5 constraints, ≥5 criteria | ✅ Yes |
| research | keyword presence | "facts"/"evidence" OR ≥120 words | ✅ Yes |
| chapter_planner | array length | ≥2 sections | ✅ Yes |
| canon | schema only | all 5 keys present | ✅ Yes |
| section_review | blocking_issues | array must be empty | ✅ Yes |
| story_architect_review | schema only | all 4 keys present — **NO SCORE GATE** | ⚠️ Soft |
| assembly_review | blocking_issues | array must be empty | ✅ Yes |
| developmental_editor | all scores | adaptive: effective min/avg thresholds (base floor default 3.0/3.0) | ✅ Yes |
| session_reviewer | all 10 rubric keys | adaptive: effective min/avg + reader_engagement/content floor (base default 2.5 content) | ✅ Yes |
| arc_consistency | loop/arc score | ≥0.6 | ✅ Yes |
| continuity | blocking_issues | array must be empty | ✅ Yes |
| publisher_qa | decision | must be "APPROVE" (or forced_completion) | ⚠️ Soft |
| word count (writer) | word count | ≥60% of writer_words per section | ✅ Yes |
| word count (edited/proofread) | word count | ≥70% of writer_words | ✅ Yes |
| word count (assembled) | word count | ≥35% of writer_words | ✅ Yes |



**Last Updated:** March 21, 2026
**Project:** Dragonlair Agent Stack — Multi-agent orchestration for book writing and code generation  
**Current Focus:** book-flow stabilization validation after orchestrator return-path repair, research grounding bootstrap, and GPU/context policy hardening

### Validation Snapshot (2026-03-21) — E2E Software-Path GPU Audit

**Run:** E2E Software Test, Chapter 1, pub→research→architect→canon→writer path
**Date/Time:** 2026-03-21 03:57–04:21 UTC
**Result:** Mixed compliance — policy audit discovered and patched KvSize leak

#### AMD Route — `qwen2.5-coder:14b` (2× RX 6800, research stage)
- ✅ **PASS** — `offloaded 49/49 layers to GPU` (both attempts)
- Split evenly: ROCm0 (25 layers) + ROCm1 (24 layers), output layer on GPU
- Policy status: **100% GPU. Compliant.**
- Note: Orchestrator requested `num_ctx=65536`; Ollama clamped to model training max 32768 with warning (acceptable).

#### NVIDIA Route — `qwen3.5:4b` (GTX 1660 Super, 6 GiB VRAM)
- **4 distinct loads across publisher_brief, architect_outline, chapter_planner, canon, writer_section_01**
- ❌ **FAIL** — Every load: `offloaded 32/33 layers to GPU` + `offloading output layer to CPU`
  - Happens at both KvSize 8192 and 16384
  - Root cause: F9 fix not yet active in container (rebuilt 2026-03-21 post-audit)
  - At KvSize=8192: VRAM insufficient for all 33 layers + KV cache on 6 GB GTX 1660 Super
  
**Expected behavior (F7 baseline):**
- `qwen3.5:4b` at `num_ctx=8192`: should load **33/33 layers**, not 32/33
- F7 documented: verified `offloaded 33/33 layers to GPU` with peak VRAM ~5596 MiB used

**Discrepancy investigation:**
- The 32/33 current result vs 33/33 F7 result warrants a targeted recheck post-rebuild
- Two loads escaped to `KvSize=16384` (architect, writer) due to missing env var — now fixed with F9
- Post-F9 rebuild: all NVIDIA contexts should clamp to 8192 max and achieve 33/33

**Status:** Awaiting validation run with F9 fix active to confirm 33/33 achievement on all NVIDIA loads.

---

### Validation Snapshot (2026-03-20)

- Runtime status validated with:
  - `docker compose -f agent_stack/docker-compose.agent.yml ps`
  - `curl -sS http://127.0.0.1:11888/api/health`
  - `curl -sS http://127.0.0.1:11888/api/ui-state`
  - `python3 -m py_compile agent_stack/api_server.py agent_stack/book_flow.py agent_stack/orchestrator.py`
- Deployment action required and executed:
  - `/home/daravenrk/dragonlair/bin/agent-stack-up`
- Post-deploy endpoint verification:
  - `GET /api/book-feedback` responds (returns `count/items/source` payload)
  - `POST /api/tasks/{task_id}/review-action` route responds (unknown task returns `task not found`)

---

## 1. Project Overview

**Model Storage Structure Policy (March 2026):**
- All Ollama model manifests and blobs must be stored in `/ai/ollama-nvidia/models` (NVIDIA) or `/ai/ollama-amd/models` (AMD).
- The `/home/daravenrk/dragonlair/model-sets` directory must not contain manifests or blobs—only text lists/configuration.
- Enforce this structure with regular audits and immediate cleanup if violations are found.

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

### ✅ Formal Product Direction Recorded (2026-03-20)

- Added explicit expectation that all major system behavior must be documented and traceable in markdown.
- Added explicit interaction principle: minimal user interaction does not reduce user control.
- Added architectural direction toward publisher-driven orchestration (book/code as pluggable publishers).
- Added requirement for an assistive conversational layer with persistent user memory.
- Added long-term trajectory toward assistant-first interface abstraction for broader Linux workflows.

### ✅ Completed Work (2026-03-19 Session 2 — GPU Layer Enforcement)

- **AMD container switched to `ollama/ollama:rocm`** — `ollama:latest` contains only CUDA; ROCm image required for AMD GPU inference.
  - `/dev/kfd` + `/dev/dri` passed as Docker devices; `group_add: ["video", "993"]` (render GID)
  - `HIP_VISIBLE_DEVICES=0,1`, `ROCR_VISIBLE_DEVICES=0,1`, `OLLAMA_SCHED_SPREAD=1` set in container env
  - Verified: `offloaded 33/33 layers to GPU` across both RX 6800 GPUs (32 GiB VRAM total)
- **NVIDIA `num_gpu=999` crash fixed** — changed to `-1` (Ollama sentinel for "all layers")
  - Previous value `999` caused `memory layout cannot be allocated` in CUDA backend
  - `_parse_env_json_int_map` now preserves `-1` without clamping
  - Verified: NVIDIA loads `32/33` layers on GPU (1 output layer CPU-side is normal for 6 GiB VRAM)
- **GPU execution policy injected in every orchestrator request** — new methods in `orchestrator.py`:
  - `_apply_gpu_execution_policy()` sets `num_gpu=-1` on all routes
  - AMD-only: `num_gpus=2`, `tensor_split=[0.5, 0.5]`, `main_gpu=0`
  - Configured via `AGENT_BLOCK_CPU_BACKEND`, `AGENT_AMD_GPU_COUNT`, `AGENT_AMD_TENSOR_SPLIT` env vars
- **`_save_ui_state_snapshot` NameError fixed** — `api_server.py` `/api/status` endpoint was calling a deleted function name; corrected to `_refresh_ui_state_snapshot(event_type="status")`
- **`args.debug` AttributeError fixed** — `book_flow.py` bare `args.debug` → `getattr(args, "debug", False)`

### ✅ Completed Work (2026-03-19 Session)

- **NVIDIA GPU enforcement**: All model layers must run on GPU — no CPU fallback permitted.
  - `orchestrator.py` `_enforce_profile_policy()` now hard-rejects any model not in `AGENT_NVIDIA_TINY_MODELS` when route is `ollama_nvidia`.
  - NVIDIA tiny-model allowlist: `qwen3.5:0.8b`, `qwen3.5:2b`, `qwen3.5:4b`, `qwen2.5-coder:1.5b`, `qwen2.5-coder:3b`, `llama3.2:1b`, `llama3.2:3b`, `codegemma:2b`
- **Profile route locking**: Added explicit `allowed_routes` frontmatter to all 8 NVIDIA profiles and all 8 large-model AMD-only profiles.
- **No model store changes**: Model files are kept on disk for both endpoints; enforcement is orchestration-layer only.
- Container rebuilt and redeployed with changes active.

### ⚠️ Current State (2026-03-19)

**Book-flow task status as of this session:**
- 9 failed `book-flow` tasks (all `qwen3.5:27b` on AMD) queued before 21:09
- 2 queued `book-flow` tasks awaiting execution (`qwen3.5:27b`, AMD route)
- Both agents (`ollama_amd`, `ollama_nvidia`) are currently **idle** — no active inference
- GPU is idle (0% utilization, 1MB VRAM used)
- Root failure cause of the 9 failed runs: **publisher stage returning empty outputs** (pre-existing issue, not caused by today's changes)

**How to validate the queued runs when they fire:**
```sh
# 1. Watch GPU activity
watch -n 2 nvidia-smi

# 2. Watch agent stack health live
watch -n 3 'curl -s http://127.0.0.1:11888/api/health | python3 -c "
import json,sys; h=json.load(sys.stdin)
q=h[\"resource_tracker\"][\"queue\"]
print(\"Q:\",q[\"status_counts\"])
agents=h[\"resource_tracker\"][\"agents\"][\"health\"][\"agents\"]
for k,v in agents.items():
    print(k,v[\"state\"],\"model=\"+str(v[\"current_model\"]))
"'

# 3. Tail the task ledger for updates
watch -n 5 'python3 -c "
import json
data=json.load(open(\"/home/daravenrk/dragonlair/book_project/task_ledger.json\"))
for t in data.get(\"tasks\",{}).values() if isinstance(data.get(\"tasks\"),dict) else data.get(\"tasks\",[]):
    print(str(t.get(\"status\"))[:10], str(t.get(\"profile\"))[:28], str(t.get(\"created_at\"))[:19])
" 2>&1 | tail -5'

# 4. Tail run journal for active run
find /home/daravenrk/dragonlair/book_project/runs -name run_journal.jsonl \
  -newer /home/daravenrk/dragonlair/book_project/task_ledger.json 2>/dev/null \
  | xargs tail -f 2>/dev/null
```

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

> **NAVIGATION**: Start with the 🚦 Work Queue table below — it lists the current priority order and what each item depends on. Completed items are marked `Completed (20XX-XX-XX)` or `✅ IMPLEMENTED` throughout — search that string to skip past them. Full changelog in [CHANGELOG.md](CHANGELOG.md).

---

### 🚦 Work Queue — Do This Next

| # | Todo | One-line description | Depends on | Status |
|---|------|----------------------|------------|--------|
| 0 | **204** | Design + prototype Code Manager Agent for JSON contract ownership | — | 🔴 **PRIORITY: TOMORROW** |
| 1 | **205** | Design nested BookPublisher → CodePublisher delegation for in-book software deliverables | 204 | 🔴 **NEXT AFTER 204** |
| 2 | **206** | Add machine-learned quality curriculum (auto-tighten writing/content thresholds over time) | 171/172 | 🟡 In progress (baseline adaptive live 2026-03-21) |
| 3 | **193** | Run clean end-to-end failure-path drill | 2 live tasks must finish | ⏳ Blocked |
| 4 | **197** | Fix NVIDIA GPU layer fallback (9 profiles → 24576 ctx) | — | ✅ Fixed 2026-03-20 |
| 5 | **194** | Archive drill run output with timestamp + metadata | 193 | Not started |
| 6 | **33** | Load validation — concurrent task pressure test | 193 | 🟡 Script ready; run `bin/load-validation` after drill |
| 7 | **195** | Fix context_store overflow in publisher brief (Risk R1) | — | ✅ Fixed 2026-03-20 |
| 8 | **196** | Fix arc consistency fuzzy-match instability (Risk R2) | — | ✅ Fixed 2026-03-20 |
| 9 | **198** | Define per-stage approved option menus; wire ML selector within them | 170 | Not started |
| 10 | **170** | Formalize strategy matrix + enforce at runtime (Stage A core) | — | Not started |
| 11 | **27** | Harden checkpoint persistence + pause/resume invariants | — | Partial |
| 12 | **73** | First clean end-to-end book run to terminal success | 193, 197 | Partial (7 attempts) |
| 13 | **28** | Failure-path drill — full end-to-end validation | 193 | Partial |
| 14 | **171/172** | ML shadow mode telemetry + recommendation logging | — | In progress |
| 15 | **29** | Expose logging metrics in WebUI | — | Not started |
| 16 | **37/38** | Agent handoff expectation contract + continuity dashboard | — | Not started |


**Stage gate**: Finish items 1–6 before starting Stage A foundation work (170+). Items 7–12 are Stage A. Items 13–14 run concurrent with Stage A.

---

### 🟢 **HIGH PRIORITY: Code Manager Agent Design (Tomorrow — Todo 204)**

**Objective**: Create a dedicated infrastructure layer that owns JSON schema compliance and structured output reliability across all agent stages. Replace ad-hoc JSON validation with coordinated coder-model-based formatting.

**Architecture Pattern**:
```
[Orchestrator/Parent Stage]
          ↓ (requirement + schema)
  [Code Manager Agent] ← Coordinates coder models (e.g., ollama qwen3.5:4b coder)
          ↓ (validated JSON)
  [Parent Stage receives guaranteed-valid structure]
```

**Design Specification**:
- **Ownership**: Code Manager Agent has singular responsibility for JSON contract validation + formatting
- **Input Protocol**: Receives `(requirement_text, json_schema, attempt_count, model_hint)` from parent stage
- **Execution Flow**:
  1. Coordinate coder model based on profile/route (NVIDIA → `qwen3.5:4b`, AMD → `qwen3.5:27b`)
  2. Prompt coder with requirement + schema to generate JSON
  3. Validate output against schema (use `jsonschema` library or equivalent)
  4. On **validation failure**: Retry with more explicit prompt or escalate to heavier coder model
  5. On **success**: Return validated JSON to parent stage
  6. On **repeated failures** (>3 retries): Escalate to human review or fallback template
- **Output Protocol**: `{ "json": <validated_dict>, "attempts": <count>, "timestamp": <iso_8601> }`
- **Failure Modes & Responses**:
  - **Malformed JSON**: Retryable — syntax error in generated JSON
  - **Missing required fields**: Retryable — schema validation failed on field presence
  - **Type mismatches**: Retryable — field type doesn't match schema (e.g., string instead of number)
  - **Fundamentally incompatible requirement**: Fatal — requirement conflicts with schema structure; escalate
  - **Silent truncation from context cap**: Log warning; escalate context requirements
- **Latency Tolerance**: **No constraint** — prioritize correctness over speed for structured output
- **Orchestrator Integration Points**:
  - Add `code_manager` profile to `agent_stack/agent_profiles/` (minimal resource footprint, single purpose)
  - Route orchestrator to invoke Code Manager Agent via new stage type `"code_manager"` in pipeline definition
  - Context clamping applies: NVIDIA routes clamped to `num_ctx=8192` for coder model (minimal context needed for formatting, not reasoning)
  - Quality gate recognizes Code Manager Agent as **trusted source of structured output** — skip downstream JSON re-validation if sourced from Code Manager

**Acceptance Criteria**:
- [ ] Code Manager Agent profile created with minimal resource footprint (NVIDIA: qwen3.5:4b, AMD: qwen3.5:27b)
- [ ] Coder model selection strategy defined (route-aware, fallback chain documented)
- [ ] Schema validation logic implemented (accepts JSON schema, validates output, detailed error reporting)
- [ ] Retry + escalation logic implemented (up to 3 retries with prompt refinement, then fallback)
- [ ] Integration test: orchestrator can invoke Code Manager Agent and receive validated JSON
- [ ] Quality gate updated to recognize Code Manager Agent output as trusted (no redundant schema check)
- [ ] Run artifacts include Code Manager Agent call telemetry (model used, attempts, validation outcome)

**Dependencies**:
- None (foundational design work; can proceed independently)
- Does not depend on Todo 170/198/204 strategy matrix work
- Can be deployed + tested in isolation, then integrated into book flow stages

**Related Risks Mitigated**:
- Quality gate failures due to malformed JSON from stage models (e.g., publisher brief returning broken structure)
- Individual model-specific prompt overfitting for JSON (outsources to coder-specialist models)
- No longer dependent on stage model's JSON coherency; separated concern from domain reasoning
- Reduces troubleshooting overhead: all JSON contract failures route to single Code Manager validation path

**Timeline & Workload Estimate**:
- Profile + coder model selection: **2 hours** (estimate, test, document approved models per route)
- Schema validation + retry logic: **3 hours** (implement `jsonschema` validator, retry loop, escalation logic)
- Orchestrator integration: **2 hours** (wire Code Manager Agent stage type, pass JSON schemas from config)
- Integration + E2E tests: **2 hours** (spawn code manager calls, validate outcomes, edge cases)
- **Total: ~9 hours** — can fit in one extended working session or split across two half-days

**First use case**: Wire into `publisher_brief` stage to eliminate recurring JSON schema failures + quality gate retries.

---

### 🟢 **HIGH PRIORITY: Nested Publisher Delegation (Todo 205)**

**Use case**: User launches **Book mode** for a programming book, for example a C++ book. During planning, outlining, or chapter execution, the book system detects that a concrete software artifact is required: sample program, library, CLI demo, test harness, or repository scaffold. At that point the book runtime must be able to open a linked **Code mode** project automatically instead of forcing the book agents to improvise code inline.

**Core principle**: The book publisher keeps ownership of the narrative and teaching objective. The code publisher owns the software deliverable. They are separate publishers in one governed runtime, not one mode pretending to be both.

**Proposed runtime shape**:
```
[User Prompt: "Write me a C++ programming book"]
       ↓
     [BookPublisher]
       ↓
   detects development deliverable need
       ↓
 [Code Project Request Contract emitted]
       ↓
     [CodePublisher child run]
       ↓
   produces repo/artifacts/tests/docs summary
       ↓
 [BookPublisher consumes result as source artifact]
       ↓
 chapter/book continues with grounded references
```

**What the BookPublisher must emit**:
- `development_goal`: what software must exist and why it matters to the book
- `language`: for this use case, `c++`
- `deliverable_type`: example program, library, CLI tool, exercise scaffold, benchmark harness, etc.
- `acceptance_contract`: required behavior, interfaces, constraints, tests, and educational purpose
- `book_linkage`: chapter number, section title, concept being taught, references expected back into manuscript
- `artifact_destination`: where the code project and resulting summary belong in the book run folder

**What the CodePublisher must return**:
- `project_summary.json` with goal, language, build/run instructions, test status, artifact paths
- concrete file outputs under a child project directory
- `integration_notes.md` written for the book system: what was built, what assumptions were made, what examples/snippets are safe to cite in the manuscript
- machine-readable pass/fail on the acceptance contract

**Required runtime changes**:
- Replace strict book-vs-code mutual exclusion with a **governed nested delegation exception**:
  - operator-started unrelated Book mode and Code mode runs can still remain mutually exclusive if desired
  - a CodePublisher child run launched by an active BookPublisher parent must be allowed
  - parent and child must share correlation metadata so this is auditable and not an ungoverned side channel
- Add parent/child task linkage fields to run artifacts and task ledger
- Add publisher-level handoff event in run journal, for example `publisher_child_project_requested` and `publisher_child_project_completed`
- Add approval checkpoint option: auto-launch child project vs request operator confirmation

**Detection points inside book flow**:
- publisher brief identifies that a chapter requires executable teaching artifacts
- architect/chapter planner defines a section whose acceptance criteria include working code
- writer or reviewer flags a missing program artifact needed to support accuracy of the manuscript

**Initial constraints**:
- First implementation should support **one-directional** delegation only: `BookPublisher -> CodePublisher`
- Child CodePublisher run should be **bounded**: one clearly scoped project with explicit acceptance tests
- The book run should not block forever waiting for exploratory coding; use timeout + resume contract
- Generated code snippets inserted into the manuscript should reference artifact paths, not be silently regenerated by book agents

**Acceptance criteria**:
- [ ] BookPublisher can emit a machine-readable code project request during a book run
- [ ] CodePublisher can execute as a linked child run while the parent book run remains governed and auditable
- [ ] Runtime distinguishes nested child code runs from unrelated operator code runs
- [ ] Child run artifacts are attached back to the parent book run with explicit references
- [ ] Book stages can cite child project outputs without re-inventing the code in prose prompts
- [ ] API and UI expose parent/child linkage clearly enough for operator inspection

**Immediate codebase implication**:
- Current backend behavior blocks this use case outright because `/api/tasks` rejects coding tasks when a `book-flow` task is queued or running, and `/api/book-flow` rejects book mode when coding tasks exist. That guard is correct for operator collision control, but it must be refactored into policy-aware nested delegation rather than global mode exclusion.

**First exemplar scenario**:
- Book prompt: "Write a book teaching modern C++ through building practical tools."
- Chapter goal: explain parsing, memory ownership, and CLI design.
- BookPublisher requests child project: `cpp-log-parser-demo`
- CodePublisher produces the C++ project, tests, and usage notes.
- BookPublisher then writes the chapter around the verified artifact instead of hallucinating the program.

### 🟢 **HIGH PRIORITY: Quality Curriculum Learning (Todo 206)**

**Objective**: Make quality expectations machine-learnable so the system starts permissive enough to complete runs, then tightens writing and content-management quality requirements as performance improves.

**Policy shape**:
- Start from conservative floor thresholds that allow completion
- Track rubric + content quality over time (per book and globally)
- Auto-adjust minimum required scores upward based on sustained quality performance
- Keep hard caps and safety rails so threshold jumps are gradual and reversible

**Acceptance criteria**:
- [x] Effective thresholds are data-driven, not static constants (baseline adaptive phase)
- [x] Threshold changes are logged in run artifacts (for audit and rollback) (baseline adaptive phase)
- [x] Quality curriculum can be tuned by environment/config (alpha, gain, max threshold, warmup) (baseline adaptive phase)
- [x] System can revert to baseline thresholds if learning state is invalid or missing (baseline adaptive phase)

**Note (2026-03-21 implementation status)**:
- Baseline adaptive implementation is now in `book_flow.py` using EMA-based learning state (`quality_learning_state.json`) and effective threshold snapshots in `run_journal` + `run_summary`.
- Runtime activation confirmed in `docker-compose.agent.yml` via `BOOK_QUALITY_ADAPTIVE_*` environment controls; stack rebuilt and running with adaptive thresholds enabled.
- Operator docs synced in second-pass consistency sweep: `USER_GUIDE.md` and `BOOK_RUN_DIAGNOSTIC_GUIDE.md` now include adaptive-threshold verification guidance and troubleshooting cues.
- Next phase for Todo 206: replace heuristic EMA adjustment with recommendation-policy learning that consumes ML shadow telemetry + user feedback outcomes.

**Manual operator-only review note (2026-03-21)**:
- Review fetcher `robots.txt` enforcement before wiring scraper-backed research into live book runs.
- Current check lives in `agent_stack/fetcher.py` inside `scrape_allowed(url, user_agent)` and is enforced from `process_url(...)` before `scrape_page(...)`.
- If an owned target ever requires a local bypass during maintenance, the decisive evaluation point to revisit is:
  `return parser.can_fetch(user_agent, url)`
- Keep this as a deliberate review point, not a silent global policy change.
- Bypassing `robots.txt` is not advised; doing so can expose the operator to policy and liability risk.

---

### Incident Note (2026-03-21): CPU Ollama visible, GPU activity not visible

Operator report:
- A server-side Ollama instance appears to be running on CPU.
- GPU-linked Ollama activity is not clearly visible during current smoke attempts.

Current evidence snapshot (2026-03-21):
- Host process list shows more than one `ollama serve` process and an active `ollama runner` child process.
- Docker services still report `ollama_nvidia` and `ollama_amd` as up.
- Book smoke run can stall at `publisher_brief`, and recent ledger entries include NVIDIA endpoint timeouts.

Add these todos now so the incident and mitigations are not lost:

- **Todo 199**: Add a one-command runtime ownership snapshot for Ollama process origin
  - New script should emit a single JSON artifact showing host processes, container processes, endpoint bindings, and GPU telemetry at the same timestamp.
  - Must explicitly tag each `ollama serve` and `ollama runner` as `host` or `container:<name>`.
  - Save artifact under `book_project/diagnostics/` for run-to-run comparison.

- **Todo 200**: Add strict non-orchestrated Ollama call guardrail mode
  - Add an env-controlled guard that blocks direct `/api/generate` calls from non-diagnostic runtime paths.
  - Keep explicit allowlist exemptions for calibration/probe scripts and orchestrator model-unload maintenance call.
  - Add clear error text explaining when a call was blocked for bypassing runtime preset governance.

- **Todo 201**: Add smoke-hang triage correlator
  - Build a small tool that joins `run_journal.jsonl`, `diagnostics/agent_diagnostics.jsonl`, and `book_project/ollama_run_ledger.jsonl` by correlation ID.
  - Output should identify the last successful stage event and whether the matching Ollama call succeeded, timed out, or never occurred.
  - Include a short "likely boundary" verdict: pre-call, in-call timeout, post-call parsing/gate failure.

- **Todo 202**: Add GPU-route liveness doctor check
  - Extend doctor checks so a GPU route is considered healthy only when all are true:
    - endpoint reachable,
    - model generate request returns,
    - accelerator telemetry shows active compute during request window,
    - no conflicting unmanaged host Ollama instance is bound to the same expected route.
  - Doctor must fail fast with remediation hints when smoke flow is likely routing to a non-validated runtime path.

- **Todo 203**: Capture GPU layer offload metrics in live agent telemetry
  - Add handler to scrape Ollama container logs for `offloaded N/M layers to GPU` lines during/after each model load.
  - Extract `(layers_on_gpu, total_layers, model, timestamp, route)` tuple and persist to `book_project/gpu_telemetry.jsonl`.
  - Expose current load status in `/api/health` response under `accelerators[route].last_load_offload_status = "N/M"`.
  - Add WebUI live gauge showing per-route offload percentage (green if N==M, yellow if N<M, red if N<1).
  - Wire into preflight validator: reject any candidate model/context pair if prior run on that route showed N < M.

---

### ✅ One-Page Lifecycle Checklist (Build Order + Acceptance Tests)

Use this as the single execution sheet for the long-term arc. Do not move to the next stage until all acceptance tests pass.

#### Stage A — Stabilize + Govern (Weeks 1-4)

**Build tasks:**
1. Implement strategy preflight validator for all run launches.
2. Stamp every run with `strategy_version` in `run_summary.json` and `run_journal.jsonl`.
3. Add drift detector for route/model/context mismatches vs policy.
4. Add explicit failure reasons + fix hints when policy is violated.
5. Add weekly report output (`policy_compliance_report.json`).

**Acceptance tests:**
1. A run with invalid route/model is blocked before stage execution starts.
2. Every successful and failed run contains `strategy_version` metadata.
3. Drift scan catches at least one synthetic mismatch in test data.
4. No GPU-policy violations pass preflight (`num_gpu=-1` required where policy mandates full GPU).
5. Report includes counts by profile, stage, violation type.

**Stage A Day-by-Day Sprint (First 10 Working Days):**
1. Day 1: Baseline strategy matrix and profile inventory freeze.
2. Day 2: Implement preflight validator command and JSON output.
3. Day 3: Wire strategy version stamping into run artifacts.
4. Day 4: Add explicit preflight failure reasons and fix hints.
5. Day 5: Add synthetic drift fixtures and drift detector pass/fail checks.
6. Day 6: Add weekly compliance report generator.
7. Day 7: Add operator CLI/API hook to run preflight before launch.
8. Day 8: Regression run across critical profiles and fallback paths.
9. Day 9: Documentation hardening (commands, examples, remediation playbooks).
10. Day 10: Gate review and sign-off; freeze Stage A as strategy-v1 baseline.

#### Stage B — Publisher Abstraction (Weeks 5-8)

**Build tasks:**
1. Define publisher interface (`analyze_intent`, `generate_options`, `validate_plan`, `execute_plan`, `stream_progress`).
2. Add publisher registry and dispatcher service.
3. Wrap current book flow as `BookPublisher` adapter (behavior parity required).
4. Implement independent `CodePublisher` (no dependency on book stages/artifacts).
5. Route existing API calls through publisher dispatcher instead of hardcoded flow paths.

**Acceptance tests:**
1. Book run output parity: legacy and adapter mode produce equivalent stage artifacts.
2. Code publisher runs end-to-end without invoking any book-flow stage IDs.
3. Dispatcher chooses publisher by explicit request and by intent classification.
4. Publisher failures are isolated (code publisher crash does not corrupt book publisher state).
5. Existing policy guards still apply uniformly across both publishers.

#### Stage C — Intent + Options + Approval (Weeks 9-12)

**Build tasks:**
1. Add intent analysis endpoint returning ranked project intents.
2. Add multi-option planner (`speed`, `quality`, `automation`, `resource_cost`).
3. Add approval checkpoint model: select option before full execution.
4. Add advanced override path (route/model/context visible and editable).
5. Record option rationale and user selection in run artifacts.

**Acceptance tests:**
1. For each prompt, API returns at least two valid options with tradeoffs.
2. Execution does not start until user approves an option.
3. Override path changes are logged with actor/time/reason.
4. Guided mode can complete with minimal inputs.
5. Advanced mode can inspect and modify all critical decisions.

#### Stage D — Assistive Layer + Memory (Weeks 13-18)

**Build tasks:**
1. Add conversational assistant entrypoint as primary UX path.
2. Add persistent user memory schema (preferences, goals, project history, interaction style).
3. Add memory-aware planning prompts.
4. Add continuity view showing why current suggestions were generated.
5. Add memory safety controls (view/edit/delete memory entries).

**Acceptance tests:**
1. Assistant recalls prior user preferences across sessions.
2. User can inspect and correct memory-derived assumptions.
3. Suggested plans show memory-influenced rationale.
4. Memory deletion requests fully remove selected entries.
5. Planning quality improves on repeated similar tasks (measured by reduced manual corrections).

#### Stage E — Adaptive Optimization (Weeks 19-24)

**Build tasks:**
1. Connect reward ledger + event stream to recommendation pipeline.
2. Train recommendation model for model/context selection per stage/profile.
3. Keep optimization constrained by allowlists and GPU policy.
4. Add token economics for unlock/penalty flows.
5. Add A/B strategy comparison and rollback.

**Acceptance tests:**
1. Recommended configs improve quality-per-resource over static baseline.
2. No recommendations violate policy guardrails.
3. Token spend/earn is auditable per profile.
4. Strategy rollback restores prior behavior in one deployment step.
5. Weekly KPI report shows trend lines for quality, latency, failures, compliance.

### Stage E.1 — Shadow Mode Learning From User Writing Feedback (Priority)

**Goal:** Start from a model/context guess, execute normally, and use user feedback on writing quality as the primary learning signal while keeping runtime policy guardrails hard.

**Build features:**
1. Add `ml_mode` runtime flag with states: `off`, `shadow`, `auto` (default `shadow` for rollout).
2. In `shadow` mode, log ML recommendation candidates but do not override actual selected route/model/context.
3. Add user feedback capture schema for section/chapter quality (`approved`, `needs_rewrite`, `score`, `comment`, `selected_text_range`).
4. Map feedback into reward events with explicit fields: `feedback_weight`, `human_acceptance`, `rewrite_required`.
5. Add per-stage confidence threshold and uncertainty logging for recommendation diagnostics.
6. Persist recommendation-vs-actual deltas into run artifacts for offline training review.

**Acceptance tests:**
1. Shadow recommendations are visible in run telemetry while actual execution remains unchanged.
2. User feedback events are persisted and linked to run/stage identifiers.
3. Feedback can be aggregated into training tuples without manual cleanup.
4. Policy guardrails still block non-compliant model/route choices even when ML recommends them.
5. Shadow-mode report shows at least top-3 candidate recommendations and confidence values per stage.

### Stage E.2 — Section Gap Detection and Retroactive Rewrite Planner (Hard Feature)

**Goal:** Allow operators/users to mark weak sections and trigger a backward-compatible rewrite plan where later accepted sections define required continuity acceptance criteria for earlier sections.

**Build features:**
1. Add `gap_review` artifact allowing selection of one or more underperforming sections (section IDs + rationale + highlighted ranges).
2. Build `reverse_acceptance_extractor` that scans downstream accepted sections and derives constraints the prior section must satisfy.
3. Add `retrofit_contract` generator that merges:
  - original section goal,
  - canon constraints,
  - downstream dependency constraints,
  - arc/element continuity requirements.
4. Add `section_rewrite_backfill` stage that rewrites flagged prior sections to close gaps while preserving accepted downstream continuity.
5. Add `dependency_impact_report` to identify which downstream sections need verification or minor patching after backfill.
6. Add staged reconcile flow: `rewrite flagged section -> continuity recheck -> selective downstream patch -> publisher QA re-evaluation`.

**Acceptance tests:**
1. User can select a weak section and create a structured gap review request.
2. System extracts at least one concrete downstream-derived acceptance criterion for each flagged section.
3. Rewritten prior section passes section continuity review with no new blocking issues.
4. Arc/element tracking remains consistent across rewritten prior section and downstream sections.
5. Final output includes provenance showing what was rewritten, why, and which downstream constraints were used.

### Stage E.3 — Living Section Expectations Ledger

**Goal:** Keep section-level expectations alive and updated after each accepted section so continuity is tracked scene-to-scene and arc-to-arc.

**Build features:**
1. Expand `consistency_sections.json` as the authoritative scene/section expectation ledger.
2. Add explicit `tracked_entities` blocks for characters, open loops, world elements, and unresolved commitments.
3. Add per-section coverage scoring (`coverage_ratio`, `missing_targets`, `risk_level`) and emit warnings before next section starts.
4. Add `cross_section_dependency_graph.json` to track forward and backward narrative dependencies.
5. Add auto-generated `repair_candidates` list when coverage or dependency checks fail threshold.
6. Add UI payload for operators to inspect missing continuity targets before approving a section.

**Acceptance tests:**
1. Each accepted section updates the ledger with coverage and missing-target diagnostics.
2. Missing tracked entities are surfaced before writing the next section.
3. Dependency graph identifies affected sections when a prior section is rewritten.
4. Repair candidates are generated automatically for low-coverage sections.
5. Ledger data is consumed by both writer prompts and continuity reviews.

#### Stage F — OS Interface Expansion (Post-Platform Maturity)

**Build tasks:**
1. Add permission-scoped Linux action bridge.
2. Add explicit consent prompts for system-level operations.
3. Add sandbox policy tiers (read-only, constrained-write, operator-approved).
4. Add audit ledger for all OS-mediated actions.

**Acceptance tests:**
1. No privileged action executes without explicit consent.
2. All OS actions are logged with command intent and outcome.
3. Permission tiers are enforced and testable.
4. Assistant can complete common Linux workflows with reversible steps.

#### Program-Level Exit Gates

1. **Control Gate:** Minimal interaction path works, advanced override path always available.
2. **Transparency Gate:** Every major decision is explainable and logged.
3. **Safety Gate:** Policy violations fail closed before execution.
4. **Reliability Gate:** Runs always terminate with explicit success/failure states.
5. **Adaptivity Gate:** Optimization improves outcomes without eroding compliance.

### 🔴 **NEW: Adaptive Creation Platform Foundation (Immediate Priority)**

**Goal:** Convert fixed-flow architecture into intent-driven, option-based publisher orchestration with an assistive user layer.

**Execution checklist:**
1. Define publisher contract and registry in backend runtime.
2. Wrap current book flow as `BookPublisher` adapter without changing behavior.
3. Implement a first-pass `CodePublisher` that runs independently of book pipeline assumptions.
4. Add intent analysis endpoint that returns multiple structured options.
5. Add approval checkpoints: option selection before execution, plus override path for advanced users.
6. Add strategy version stamping in run artifacts for traceability.
7. Add persistent user memory schema for preferences, project history, and interaction style.

**Control philosophy acceptance criteria:**
- Guided defaults available for low-friction usage.
- Advanced controls available for route/model/context inspection and override.
- Users can approve, pause, cancel, or redirect at key decision points.
- System automation remains transparent and auditable.

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

### 📋 **HIGH PRIORITY: Runtime Hardening** — ✅ All items complete (2026-03-18)

| ✅ | What was built |
|----|----------------|
| Todo 39 | Profile-signal routing scorer (weighted scoring + deterministic tie-break) |
| — | Profile execution policy: `timeout_seconds`, `retry_limit`, `allowed_routes`, `model_allowlist` — frontmatter + orchestrator enforcement |
| — | Removed duplicate `OrchestratorAgent.__init__` |
| — | Typed exception hierarchy throughout (`AgentStackError`, `Ollama*Error`, etc.) — no bare `except Exception` |
| — | Interruption recovery drill: `agent_stack/scripts/interruption_recovery_drill.sh` |
| — | Terminal-state integrity guard: `_ensure_run_journal_terminal()` — every run closes with `run_success` or `run_failure` |

---

### 🟡 **MEDIUM PRIORITY: Infrastructure Validation (Runtime-first alignment)**

These should be completed as part of the current mode-based runtime hardening path:

- ~~**Todo 26** — Clean duplicate orchestrator initialization~~ ✅ Done 2026-03-18

- **Todo 27**: Harden checkpoint persistence + pause/resume invariants
  - Strengthen runtime checkpoint persistence between stages (ledger + run artifacts + review-gate state)
  - Ensure pause/resume behavior remains deterministic across reconcile/restart paths
  - **Status (2026-03-20 triage): Partially developed**
    - Book-flow state is already persisted via task ledger (`task_ledger.json`) and run artifacts, and review-gate pause/resume is implemented (`book_flow.py`, `api_server.py`).
    - The old standalone "checkpoint save agent" requirement was removed to match runtime/API-driven persistence design.
    - **Implementation progress (2026-03-20):** startup bootstrap now normalizes persisted paused tasks, clears stale runtime-only fields, and requeues resume-requested review gates; review-action handling now detects orphaned paused tasks (`started_at is None`) and schedules deterministic resume instead of leaving them stuck in `running`; reconcile now requeues paused tasks when review-gate state already requests `continue` or `rewrite`.

- **Todo 28**: Add failure-path integration tests
  - Add one integrated scenario suite that validates retry logic, fallback handling, graceful degradation, cancellation, and reconcile/resume behavior end-to-end
  - **Status (2026-03-20 triage): Partially developed**
    - Interruption recovery drill exists (`agent_stack/scripts/interruption_recovery_drill.sh`).
    - Regression script covers status synthesis and includes cancellation behavior (`agent_stack/scripts/regression_status_synthesis.py`).
    - **Implementation progress (2026-03-20 session 1):** added `agent_stack/scripts/failure_path_integration_drill.sh`, an operator-facing suite that chains interruption recovery, pause/restart/review-continue/reconcile resume, queued/running task cancellation, and fallback-integrity checks into one repeatable validation entry point.
    - **Implementation progress (2026-03-20 session 2 — hardening and regression fix):**
      - **Orchestrator regression fixed**: `OrchestratorAgent._collect_fallback_routes()` was missing — the method was called inside `_build_ml_shadow_recommendations()` but never defined; every book-flow task creation returned HTTP 500. Method added and container rebuilt/redeployed.
      - **Drill script json_get fixed**: `python3 - "$expr" <<'PY'...PY` heredoc consumed stdin so piped JSON was discarded; changed to `python3 -c '...' "$expr"` in both scripts.
      - **Drill log/stdout separation fixed**: `log()` was printing to stdout, mangling command-substitution JSON captures; redirected to stderr in both scripts.
      - **Python quoting in embedded list_active_tasks fixed**: f-string single-quote conflict in nested shell functions; converted to `"{}|{}".format(...)` style.
      - **Live-stack safety preflight added**: `list_active_tasks()` and `require_idle_stack()` helpers added to both drill scripts; drill now exits cleanly with an informative task list when active operator work is running instead of disrupting it (subdrill 1 restarts the API container).
    - Remaining gap: run the suite in a clean maintenance window (no active operator book-flow tasks) to complete full end-to-end validation. See **Todo 193**.

- **Todo 193**: Complete first clean end-to-end failure-path drill run
  - **Blocker**: Two active operator book-flow tasks (`c295ae05` — The Hidden Archive / Opening Adventure, `20e8924cc6` — Chapter One / Opening) prevent subdrill 1 from running because it restarts the API container.
  - **Unblock options**: Wait for active tasks to reach terminal state, or cancel them with operator consent (`curl -X POST http://127.0.0.1:11888/api/tasks/{id}/cancel`), then run `bash agent_stack/scripts/failure_path_integration_drill.sh`.
  - **Acceptance criteria**:
    - All 4 subdrills exit 0: interruption recovery, pause/resume, cancellation, and fallback integrity.
    - No drill-created tasks left in a non-terminal state after the run.
    - Drill log (stderr) shows each subdrill passed; exit code 0.
  - **Post-drill**: Document any assertion flaws or timing issues found during real-latency execution so assertions can be tightened in the follow-up.

- **Todo 194**: Add drill output capture and archiving
  - Redirect `failure_path_integration_drill.sh` stderr to a timestamped log file under `book_project/drill_reports/` alongside an exit status summary.
  - Include run metadata: timestamp, drill version, API host, active route health at drill start, pass/fail per subdrill.
  - Acceptance: operator can replay drill outcomes from the archive without re-running; logs are gitignored by default but operator can opt-in to commit clean report artifacts.

- ~~**Todo 195** — R1 fixed: publisher brief now uses bounded `publisher_inputs` snapshot (book request, chapter metadata, previous brief summary, capped recent memory) instead of full `context_store`~~ ✅ Done 2026-03-20

- ~~**Todo 196** — R2 fixed: `score_arc_consistency()` now uses normalized fuzzy matching (sequence+jaccard), configurable thresholds, near-miss diagnostics, and optional warning-only gate mode (`ARC_CONSISTENCY_WARNING_ONLY`)~~ ✅ Done 2026-03-20

- ~~**Todo 197** — NVIDIA GPU layer fallback fixed: `num_ctx: 49152 → 24576` on 9 NVIDIA-route profiles; GTX 1660 SUPER 6 GiB cannot fit 33 layers + 49152-token KV cache; 24576 yields 32/33 GPU layers~~ ✅ Done 2026-03-20

- **Todo 29**: Expose logging metrics in UI
  - Expand in-product runtime metrics card for agent health, queue depth, retry/failure counts, and route pressure
  - Optional future phase: external metrics export endpoint (Prometheus-compatible)
  - **Status (2026-03-20 triage): Partially developed**
    - UI already shows health summary and queue/activity signals from `/api/ui-state`.
    - No dedicated external metrics endpoint (`/metrics`) is present yet.
    - No explicit error-rate metric card with trend-style counters exists yet.

- **Todo 33**: Validate FastAPI runtime environment
- **Todo 33**: Validate FastAPI runtime environment — **✅ SCRIPT IMPLEMENTED (2026-03-20); run pending idle stack**
  - Script: `agent_stack/scripts/load_validation.py` — submits 3 concurrent tasks (1 NVIDIA, 1 AMD, 1 queued NVIDIA), asserts: route_active_counts reflects simultaneous activity; queue_depth ≥ 1 under dual-route load; both routes complete without errors; VRAM headroom sampled at peak load. Exits 2 when stack is busy (safe for automation).
  - Host wrapper: `bin/load-validation` (runs inside container).
  - Report written to: `book_project/load_validation_report.json`.
  - **Sequencing**: wait for current running tasks and Todo 193 drill to complete, then run `bin/load-validation`.

- ~~**Todo 34** — Global agent output schema validator (`output_schemas.py`, wired into `run_stage`)~~ ✅ Done 2026-03-18
- ~~**Todo 35** — Framework integrity gate (`check_framework_integrity()`, fires before Canon, emits `framework_integrity_passed`)~~ ✅ Done 2026-03-18
- ~~**Todo 36** — Arc consistency scorer (`score_arc_consistency()`; open loops merged not replaced; `arc_consistency_score.json`)~~ ✅ Done 2026-03-18

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

- **Todo 198**: Define per-stage approved option menus and wire agent/ML selection within them
  - **Goal**: Agents (and the ML shadow recommender) choose from a finite pre-approved menu of `{model, num_ctx, num_predict}` tuples per stage/route — not arbitrary values. Guardrails become a menu, not just a blocklist.
  - **Define the menu format** in a versioned artifact (`strategy_option_menus.json`, co-located with strategy matrix from Todo 170):
    - Each entry: `{stage, route, options: [{model, num_ctx, num_predict, label, gpu_vram_budget_mb}]}`
    - Example for `publisher_brief` / `ollama_nvidia`: `[{model: "qwen3.5:2b", num_ctx: 16384, num_predict: 1800, label: "fast"}, {model: "qwen3.5:4b", num_ctx: 24576, num_predict: 1800, label: "standard"}, {model: "qwen3.5:4b", num_ctx: 49152, num_predict: 2400, label: "thorough"}]`
    - Label tiers: `fast`, `standard`, `thorough` — consistent across all stages so ML can learn tier-level preferences separately from model/context specifics.
  - **Wire into orchestrator**: `plan_request()` resolves the approved menu for the stage/route and passes the full set to the ML recommender; recommender scores and returns the selected tuple; the selected tuple is the only options that reach the Ollama call. No raw `num_ctx`/`model` values from profile frontmatter should bypass the menu.
  - **Wire into ML shadow mode (Todo 172)**: shadow recommendations must be drawn from the same menu tuples so training data aligns with the actual option space. Recommendations referencing non-menu options are invalid and should be flagged.
  - **Menu validation at startup**: extend profile-lint (Todo 99) to confirm every stage/profile has a complete menu entry for its configured route; fail startup with a clear error if any stage would have zero valid options.
  - **Operator override path**: allow a named label override per stage (`--option-label thorough` on CLI; `option_label` field in `BookFlowRequest`) that forces the tier while still drawing model/context from the approved menu — no raw value override.
  - **Acceptance criteria**:
    - Every stage/route combination has at least 2 approved menu options.
    - No Ollama call reaches a model or `num_ctx` value not present in the menu for that stage/route.
    - ML shadow recommender output references menu tuple IDs, not free-form model/context strings.
    - `stage_attempt_start` diagnostics include `selected_option_label` and `menu_size` for every call.
    - Startup fails (or emits lint errors) when any stage/route is missing from the menu.

- **Todo 167**: Enforce explicit per-model GPU layer maps for every run (no sentinels)
  - Define exact `num_gpu_layers` per model and per run profile for both AMD and NVIDIA routes; do not use sentinel values (`999` or `-1`).
  - Require explicit dual-AMD layer split mapping for each AMD run so layers are balanced evenly across both GPUs.
  - Enforce zero CPU-layer execution for targeted runs; fail fast if any layer is placed on CPU.
  - Add startup/runtime validation that blocks task launch when a model is missing an explicit GPU layer map.
  - Persist per-run layer mapping + actual offload results to diagnostics for operator verification.

- **Todo 42**: Add token-aware execution policy
  - Use reward token level to tighten validation, reduce creativity variance, and choose safer models
  - Trigger stricter JSON/contract checks automatically when profile tokens are low or depleted

- **Todo 168**: Add monetary reward model tied to execution policy and approved model definitions
  - Define a pricing and reward formula per run that combines: wall-clock speed, quality-gate pass rate, and least-interference behavior (no quarantines, no forced recoveries, no avoidable retries).
  - Restrict reward eligibility to approved model/profile configurations only (enforced by existing route/model allowlists and model-definition constraints such as `num_ctx`, `num_predict`, `temperature`, and GPU mapping rules).
  - Add per-profile baseline cost bands and per-model multipliers so faster, stable runs on approved configurations are rewarded predictably.
  - Add explicit penalties for policy violations (CPU-layer usage where disallowed, unsupported model selection, route override drift, excessive fallback hops).
  - Persist run-level reward ledger entries with pricing inputs, final reward/penalty, and audit trace fields for operator review and reconciliation.

- **Todo 169**: Prepare no-touch end-to-end book run automation (front-to-back untouched)
  - Implement an external operator watchdog loop (no core pipeline code edits) that monitors task status, run-journal freshness, and queue health.
  - Stall detection policy: if task is `running` and no new `run_journal.jsonl` event for a bounded window, trigger controlled recovery.
  - Recovery ladder policy:
    1. call `/api/recover-hung` with timeout + retry/backoff,
    2. if unresolved, soft-cancel stuck task,
    3. requeue using the last persisted `book_request` payload.
  - Polling resilience policy: require consecutive failed/empty API responses before corrective action (to avoid false positives during transient endpoint jitter).
  - Safety policy: single-instance lock for watchdog execution and append-only action audit log with timestamps, task IDs, and outcomes.
  - Run unchanged core stack for validation; use only existing public endpoints and file artifacts to recover progress.
  - Acceptance criteria for "untouched" prep:
    - at least one run reaches terminal success without manual stage intervention,
    - stalled runs are automatically recovered or requeued,
    - every corrective action is captured in automation audit logs for replay/debug.

- **Todo 170**: Formalize rigid-flexible run strategy standard and enforce it at runtime
  - Create a single source-of-truth strategy matrix for book-flow stages (stage -> profile -> route -> model -> allowed fallbacks).
  - Add startup preflight that validates strategy matrix coherence against profile frontmatter (`allowed_routes`, `model_allowlist`) and route-level model constraints.
  - Add runtime guardrails so every `stage_attempt_start` must include strategy-compliant route/model values; reject or auto-reroute non-compliant attempts with explicit journal events.
  - Add a strategy version stamp in run artifacts (`run_summary.json` + `run_journal.jsonl`) so operators can trace which policy governed each run.
  - Add strategy drift detection in diagnostics: flag when profile edits or environment overrides change effective route/model behavior without a strategy version bump.
  - Include a controlled override mode for future gamification unlocks: unlocked models/routes must still pass explicit allowlist + GPU policy checks and be recorded as policy exceptions.
  - Acceptance criteria:
    - all critical stage profiles resolve to approved route/model pairs before run start,
    - no silent model drift (for example, 9B requests on NVIDIA) reaches execution,
    - every strategy violation is visible in diagnostics and future dev notes.

- **Todo 171**: Add ML runtime mode controls (off/shadow/auto)
  - Add runtime flags and API-visible state for ML mode selection.
  - Default to `shadow` mode in production until acceptance criteria for learning quality are met.
  - Persist per-run mode in `run_summary.json` and `run_journal.jsonl`.
  - **Status (2026-03-20): In progress** — `AGENT_ML_MODE`, `AGENT_ML_MIN_CONFIDENCE`, `AGENT_ML_TOP_K` added in orchestrator; `ml_mode` now included in run summary and runtime status payload.
  - Acceptance criteria:
    - mode appears in runtime status and run artifacts,
    - switching modes does not break policy preflight,
    - shadow mode never overrides actual route/model selection.

- **Todo 172**: Add shadow recommendation telemetry
  - Generate top-3 ML candidate recommendations per stage/profile with confidence and expected score.
  - Log recommendation vs actual selected route/model/context for every stage attempt.
  - Persist recommendation deltas in diagnostics for offline review.
  - **Status (2026-03-20): In progress** — shadow recommendations now emitted by `plan_request()` and persisted into stage attempt events (`stage_attempt_start`) plus diagnostics. Dedicated shadow event stream path added (`ml_shadow_events.jsonl`).
  - Acceptance criteria:
    - every stage attempt has recommendation telemetry in shadow mode,
    - recommendation logging survives retries/failovers,
    - no recommendation bypasses policy allowlists.

- **Todo 173**: Add user writing feedback schema and ingestion
  - Add feedback artifact schema with fields: `approved`, `needs_rewrite`, `score`, `comment`, `selected_text_range`.
  - Link each feedback entry to run, chapter, section, and stage IDs.
  - Write feedback events to reward/event stream for ML training ingestion.
  - Add thumbs-driven quality signals with issue checkbox taxonomy for thumbs-down (`canon_violation`, `continuity_gap`, `tone_mismatch`, `pacing_problem`, `structure_problem`, `missing_world_detail`, `character_voice`, `clarity`, `other`).
  - Add rewrite scope preference (`ask_each_time`, `section_only`, `chapter_reflow`) and persist in feedback events.
  - **Status (2026-03-20): In progress** — feedback schema and API endpoints added in `api_server.py`: `POST /api/book-feedback` (validated ingestion + reward-event write-through) and `GET /api/book-feedback` (query by run/chapter/section/stage/rewrite). Events persist to `book_feedback_events.jsonl` and are linked to run metadata when available. Web UI panel now captures thumbs, issue checkboxes, and rewrite scope.
  - Acceptance criteria:
    - operator can submit section-level feedback,
    - feedback is queryable by run and section,
    - malformed feedback is rejected with explicit validation errors.

- **Todo 174**: Convert feedback into training reward signal
  - Add reward mapping from human feedback to structured learning targets (`human_acceptance`, `rewrite_required`, `feedback_weight`).
  - Merge feedback signal with latency/gate/failure metrics into unified training tuples.
  - Add strategy-version stamp to every tuple for rollback-safe training.
  - Acceptance criteria:
    - training tuples include both machine metrics and human feedback,
    - negative feedback produces measurable penalty in reward stream,
    - tuple generation is reproducible from artifacts.

- **Todo 175**: Add gap-review workflow for weak sections
  - Add a `gap_review` request artifact allowing users to mark underperforming sections and rationale.
  - Support text-range selection so feedback can target scene fragments, not only full sections.
  - Emit gap-review events into run journal and diagnostics.
  - Acceptance criteria:
    - user can mark one or multiple sections as weak,
    - gap review payload validates and persists,
    - marked sections are listed for repair planning.

- **Todo 176**: Build reverse acceptance extractor from downstream sections
  - Analyze accepted downstream sections to derive constraints that prior weak sections must satisfy.
  - Produce explicit acceptance criteria grouped by character arc, open loop, and important element.
  - Persist extracted constraints in a dedicated repair artifact.
  - Acceptance criteria:
    - each flagged section receives at least one downstream-derived constraint when dependencies exist,
    - extracted constraints are auditable and human-readable,
    - extractor output is consumed by rewrite planner.

- **Todo 177**: Add retroactive section rewrite backfill stage
  - Implement stage flow: `rewrite flagged prior section -> continuity recheck -> selective downstream patch`.
  - Ensure rewritten prior section preserves canon and does not break accepted downstream facts.
  - Add impact report identifying downstream sections requiring patch or revalidation.
  - Acceptance criteria:
    - rewritten section passes continuity gate,
    - dependency impact report is generated,
    - publisher QA can re-evaluate repaired run state.

- **Todo 178**: Promote consistency sections to living dependency ledger
  - Extend `consistency_sections.json` with dependency edges and per-section coverage risk.
  - Track unresolved character/element continuity gaps scene-to-scene and arc-to-arc.
  - Generate `repair_candidates` automatically when coverage drops below threshold.
  - Acceptance criteria:
    - ledger updates after each accepted section,
    - low-coverage sections are flagged before next section generation,
    - rewrite planner can consume repair candidates directly.

- **Todo 179**: Add ML promotion gate (shadow -> auto)
  - Define objective rollout thresholds before enabling `auto` mode (quality delta, retry delta, fallback delta, latency budget).
  - Require minimum sample size per stage/profile before eligibility.
  - Add automatic rollback trigger if post-promotion KPIs regress.
  - Acceptance criteria:
    - promotion can only occur when thresholds are met,
    - rollback triggers within one deployment cycle,
    - promotion/rollback decisions are written to audit artifacts.

- **Todo 180**: Add correlation IDs across recommendation and stage artifacts
  - Propagate a shared correlation ID from orchestrator plan to `stage_attempt_start`, diagnostics, and run summary.
  - Ensure `ml_shadow_events.jsonl` entries can be joined to run journal rows without fuzzy matching.
  - Add integrity check for missing correlation IDs.
  - **Status (2026-03-20): Completed** — correlation IDs now propagate through orchestrator plan/handle path into `stage_attempt_start`, `stage_attempt_result`, diagnostics payloads, and runtime status hints (`runtime_correlation_id`). Run summary includes `correlation_integrity` output, strict mode can fail artifact validation when IDs are missing (`AGENT_CORRELATION_INTEGRITY_STRICT=true`), and deterministic join verification is available via `agent_stack/scripts/verify_correlation_join.py`.
  - Acceptance criteria:
    - 100% of stage attempts have joinable recommendation records,
    - missing correlation IDs fail diagnostics checks,
    - training extractor can build deterministic tuples.

- **Todo 181**: Replace heuristic shadow scorer with trained recommender service
  - Introduce a versioned recommender artifact (`model_id`, `trained_at`, `feature_set_version`).
  - Keep heuristic scorer as fallback when model artifact is missing or stale.
  - Log model provenance in each recommendation event.
  - Acceptance criteria:
    - recommender output is versioned and reproducible,
    - fallback path activates automatically on load failure,
    - recommendation payload includes model provenance fields.

- **Todo 182**: Add feedback quality controls and anti-gaming weighting
  - Add confidence/credibility weighting for user feedback (recency, consistency, reviewer role).
  - Detect and down-weight contradictory or spam-like feedback bursts.
  - Add manual override marker for trusted editorial decisions.
  - Acceptance criteria:
    - noisy feedback does not dominate reward updates,
    - trusted editorial decisions can be explicitly weighted,
    - feedback weighting is visible in reward event logs.

- **Todo 183**: Add shadow mode UI and operator review panel
  - Surface top-3 recommendations, confidence, and chosen-vs-actual deltas in WebUI.
  - Add per-stage approve/reject controls for recommendation quality review.
  - Add filter views for low-confidence or high-regret recommendation events.
  - Acceptance criteria:
    - operator can inspect recommendation history per run,
    - operator decisions are persisted for training,
    - low-confidence recommendations are highlighted before execution.

- **Todo 184**: Add counterfactual evaluator for offline policy testing
  - Build offline evaluator that replays historical runs and estimates regret for alternative recommendations.
  - Compute stage/profile regret metrics for shadow recommendations.
  - Gate `auto` mode eligibility on counterfactual win-rate thresholds.
  - Acceptance criteria:
    - evaluator runs on existing artifacts without live execution,
    - outputs regret and win-rate by stage/profile,
    - promotion gate consumes evaluator outputs.

- **Todo 185**: Add feedback query and moderation controls
  - Add API filters for feedback by run/chapter/section/stage and rewrite flag.
  - Add moderation fields (`review_status`, `moderator_note`) for rejecting invalid feedback records.
  - Add endpoint-level auth hook placeholder for future multi-user environments.
  - Acceptance criteria:
    - operators can retrieve section-scoped feedback quickly,
    - moderated feedback is excluded from training export when rejected,
    - endpoint returns deterministic sorted output for reproducible exports.

- **Todo 186**: Add feedback-to-training export builder
  - Build an export script that joins `book_feedback_events.jsonl` with `run_journal.jsonl` and `ml_shadow_events.jsonl` via correlation IDs.
  - Emit stage-level training rows with both machine metrics and human feedback targets.
  - Add schema versioning for exported training rows.
  - **Status (2026-03-20): In progress** — export builder added as `agent_stack/scripts/export_feedback_training_rows.py` with deterministic outputs, join-rate statistics, schema versioning (`feedback-training-v1`), and explicit missing-join diagnostics.
  - Acceptance criteria:
    - export produces deterministic row counts for the same input set,
    - missing joins are reported with explicit diagnostics,
    - output is ready for Todo 181 recommender training input.

- **Todo 187**: Add feedback integrity and drift alerts
  - Add periodic checks for contradictory feedback patterns and missing linkage fields.
  - Alert when feedback volume skews heavily to one profile/stage (potential data bias).
  - Emit weekly summary into policy compliance diagnostics.
  - Acceptance criteria:
    - integrity report lists malformed/unlinked feedback events,
    - bias alert thresholds are configurable,
    - summary is visible in operator diagnostics artifacts.

- **Todo 188**: Add CI guardrail for training export quality
  - Add a CI/staging check that runs `export_feedback_training_rows.py --summary-only` and validates minimum join-rate thresholds.
  - Fail pipeline if `attempt_join_rate` or `plan_join_rate` drops below configured floor.
  - Persist failed-check diagnostics in build artifacts for operator investigation.
  - Acceptance criteria:
    - CI blocks merges on severe export quality regression,
    - threshold overrides are environment-configurable,
    - failed joins are linked to concrete examples in artifacts.

- **Todo 189**: Add accepted-writing pause and reader preview gate
  - Add a runtime checkpoint that pauses progression after accepted writing quality and exposes section/chapter preview context in Web UI.
  - Require explicit operator decision to continue, rewrite, or defer when pause gate is active.
  - Ensure rewrite requests cannot violate accepted downstream facts or canon prerequisites.
  - Acceptance criteria:
    - pause gate can be triggered from user feedback (`pause_before_continue=true`),
    - Web UI shows previewable context for the paused section/chapter,
    - resume/rewrite decisions are journaled and linked to correlation IDs.

- **Todo 190**: Implement backend pause gate for accepted writing
  - Add book-flow and API-server runtime handling so `pause_before_continue=true` can place a run into an explicit paused/awaiting-review state.
  - Prevent automatic progression into the next writing/review stage while a pause gate is active.
  - Add explicit continue, rewrite, and defer actions at the task/run level, with deterministic journal events.
  - **Status (2026-03-20): In progress** — feedback pause requests now write live review-gate state, running book tasks can enter `paused` state, and backend review actions (`continue`, `rewrite`, `defer`) are wired in API + book-flow at the post-section-review checkpoint.
  - Acceptance criteria:
    - a feedback event with pause enabled can halt forward progression before the next section/chapter stage begins,
    - paused state is visible in task status and UI-state payloads,
    - continue/rewrite/defer actions are persisted in `run_journal.jsonl` and linked to the active correlation ID.

- **Todo 191**: Add WebUI reader preview and resume controls
  - Add a dedicated preview pane in the Web UI for the currently paused section/chapter, including enough surrounding context for user review.
  - Surface operator controls to continue, request rewrite, or defer without leaving the Web UI.
  - Show relevant metadata alongside the preview: title, chapter, section, stage, latest feedback, and pause reason.
  - **Status (2026-03-20): In progress** — the Web UI now renders a paused-review card with section/review previews from review-gate state and can submit `continue`, `rewrite`, and `defer` actions back to the backend without a full reload.
  - Acceptance criteria:
    - operators can inspect paused writing in context from the Web UI,
    - resume/rewrite/defer controls are available next to the preview,
    - preview state refreshes correctly from API polling without requiring a full page reload.

- **Todo 192**: Wire end-to-end feedback pause workflow and invariant enforcement
  - Connect feedback submission, pause gate activation, preview rendering, and operator decision handling into one coherent flow.
  - Add invariant checks so assistant-mediated rewrite requests cannot break canon, accepted downstream facts, or explicit future requirements.
  - Add integration coverage for: submit feedback -> pause run -> preview section -> choose continue/rewrite/defer -> journaled outcome.
  - Acceptance criteria:
    - the full feedback-to-pause workflow works from the existing Web UI without manual file edits,
    - rewrite requests are blocked or flagged when they would violate established story constraints,
    - integration validation proves the pause workflow is recoverable across refreshes/restarts.

### Next Improvement Sprint (Recommended Order)

1. Implement Todo 171 + Todo 172 first (shadow mode plumbing and telemetry only, zero behavior change).
2. Implement Todo 173 + Todo 174 second (feedback ingestion and reward mapping).
3. Implement Todo 175 third (manual gap marking UI/API path).
4. Implement Todo 176 + Todo 177 fourth (hard backward rewrite feature).
5. Implement Todo 178 last (full dependency ledger automation and proactive repair candidates).

**Sprint-1 Exit Criteria:**
- Shadow mode emits reliable recommendation telemetry for all book-flow stages.
- User feedback events are persisted and linked to section IDs.
- No policy guardrail regressions (GPU-only + allowlist constraints remain hard-blocking).

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

- ~~**Todo 54** — Ollama run ledger with correlation IDs (`ollama_run_ledger.jsonl`; tok/s; `latest_ollama_call` in UI-state; WebUI voyeur panel)~~ ✅ Done 2026-03-18

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

- ~~**Todo 72** — Open-port audit + least-exposure hardening (127.0.0.1 bindings; `dragonlair-net`; internal service names)~~ ✅ Done 2026-03-18

- **Todo 73**: Run one full end-to-end book flow after schema enforcement changes
  - Execute a full chapter run with current retries/fallbacks and capture run journal + diagnostics
  - Confirm schema validation failures surface actionable gate messages and recover through retries when possible
  - Record pass/fail results and next fixes back into `DEV_NEXT_STEPS.md`
  - **Current run-dir behavior (verified in code on 2026-03-18)**:
    - `book_flow.py` does **not** clear prior run artifacts at run start.
    - It creates a stable per-book root at `book_project/<slug>/`, preserves `framework/` and `book_history.jsonl`, and creates a **new timestamped** run dir under `runs/<timestamp>-chNN-<section-slug>/` for each invocation.
    - This is useful for audit/history, but it means isolated validation directories like `book_project/todo73-e2e/` accumulate old attempts unless explicitly cleaned.
    - If operator intent is "fresh validation run with no prior artifacts in the target output dir", that must be implemented explicitly; it is not the current behavior.
  - **Execution result (2026-03-18, Attempt 1 / FAILED)**:
    - Command executed with isolated writable output dir: `--output-dir /home/daravenrk/dragonlair/book_project/todo73-e2e`
    - Run dir: `book_project/todo73-e2e/todo-73-e2e-validation/runs/20260318-174723-ch01-the-first-stable-loop`
    - Failure stage: `publisher_brief`
    - Gate message: `[AGENT_QUARANTINED] Agent ollama_nvidia is quarantined`
    - Journal evidence: two `stage_attempt_error` events (`AGENT_HUNG`), followed by `stage_recovery_error` (`AGENT_QUARANTINED`) and terminal `stage_failure`
    - Artifacts captured: `run_journal.jsonl`, `diagnostics/agent_diagnostics.jsonl`, `handoff/resource_references.json`
    - Missing terminal artifacts: no `06_final/manuscript_v1.md`, no `run_summary.json`, and no journal-level `run_success`/`run_failure` event
  - **Next fixes before Attempt 2**:
    - Ensure a clean, non-quarantined route/model pair for `book-publisher-brief` (or add fallback route/model for this profile)
    - Add run-level terminal event emission for CLI `book_flow.py` failures so every run closes with explicit `run_success` or `run_failure`
    - Re-run Todo 73 and require completion through `publisher_qa` with final manuscript artifacts present
  - **Execution result (2026-03-18, Attempt 2 / FAILED)**:
    - Failure stage remained `publisher_brief`, but the failure mode changed from quarantine to timeout/hung behavior on local CLI path.
    - Root cause found: local `book_flow.py` route timeouts and Ollama HTTP timeouts were not aligned with container defaults, and `ThreadPoolExecutor` shutdown blocked after timeout.
    - Fixes landed in `orchestrator.py` and `ollama_subagent.py` so both routes now default to `900s` on local CLI and timeout exits no longer block on executor shutdown.
  - **Execution result (2026-03-18, Attempts 3-5 / FAILED, but informative)**:
    - Timeout/quarantine blocker was removed; `publisher_brief` started returning model output within normal runtime windows.
    - New failures surfaced in order:
      - `cli_runtime_activity.json` and lock file ownership/permission mismatch could crash CLI runs during telemetry updates.
      - `publisher_brief` fallback repair logic could not run because strict schema validation aborted inside `run_stage(...)` first.
      - `research` repeatedly failed its quality gate with `research output missing facts section`.
    - Fixes landed in `book_flow.py` to:
      - make CLI telemetry write failures non-fatal,
      - allow `publisher_brief` local repair/fill logic to execute before hard schema rejection,
      - relax research gate heading strictness and enable substantive-output acceptance fallback.
  - **Execution result (2026-03-18, Attempt 6 / FAILED)**:
    - `publisher_brief` passed cleanly and the run advanced to `research`, confirming the prior timeout/publisher blockers were resolved.
    - `research` still failed after retries and recovery, indicating the remaining blocker is now stage contract quality rather than infra/runtime stability.
  - **Execution result (2026-03-18, Attempt 7 / IN PROGRESS / latest code path)**:
    - New default-on debug payload logging was added so every stage attempt/recovery can persist raw output to diagnostics for offline analysis.
    - Next evaluation should be performed from `run_journal.jsonl` + `diagnostics/agent_diagnostics.jsonl` rather than process watching.
  - **Current checkpoint (2026-03-18, before next todo pickup)**:
    - Pre-run cleanup and archival are now active and verified in live runs: old `runs/` content is copied into `run_history/` before the next run starts.
    - Post-publish cleanup is now active in `book_flow`: after successful export/publication, the completed run is archived immediately to `run_history/` with manuscript + evidence artifacts and the live `runs/` copy is removed.
    - Full debug payload logging is active by default, so stage attempt/recovery payloads are available in diagnostics without needing `--debug`.

- **Todo 74**: Add standard GPU execution proof for NVIDIA and AMD routes
  - Add a repeatable probe that runs a sustained generation load and captures live accelerator telemetry during inference, not just pre/post memory residency.
  - NVIDIA success criterion: sampled GPU utilization/power must show active compute during generation while backend_type remains `nvidia`.
  - AMD success criterion: sampled ROCm/AMD telemetry must show active compute during generation while backend_type remains `amd`.
  - Persist a short machine-readable summary artifact for each probe run so GPU execution evidence is auditable.

- **Todo 75**: Standardize accelerator telemetry availability across environments
  - Ensure NVIDIA and AMD environments both expose operator-usable runtime telemetry tools (`nvidia-smi` / `rocm-smi` or equivalent).
  - Add a doctor/self-check that fails when GPU-backed routes are configured but accelerator telemetry is unavailable.
  - Requirement: GPU-routed models are not considered fully validated until both layer residency and live compute activity are observable.
    - `publisher_brief` is now stable enough to pass on current validation runs.
    - `research` on AMD is still returning `raw_output: null` / empty output, but the pipeline now applies a deterministic fallback dossier and continues.
    - `architect_outline` fallback is now validated in the fresh run path (`operator_generated_architect_outline` fired and advanced downstream).
    - `chapter_planner` fallback is now validated in the fresh run path (`operator_generated_chapter_spec` fired and framework integrity passed).
    - Immediate next evaluation target: confirm whether `canon` is simply high-latency or a new silent-stop/stall boundary between `pre_agent_call` and `stage_attempt_result`.
    - Latest live-run finding (2026-03-19): canon-stage attempt can stall with no terminal stage event, leaving task status as `running` until operator soft-cancel.
    - Latest live-run finding (2026-03-19): `/api/recover-hung` may timeout under this stall condition, so recovery reliability needs hardening.
    - Latest live-run finding (2026-03-19): `/api/tasks/{id}` intermittently returns an empty response body during active polling; add response-integrity guard + retry/trace logging.
  - **Current Todo 73 analysis summary**:
    - Infra timeout mismatch: fixed.
    - Executor timeout shutdown blocking: fixed.
    - CLI telemetry permission crash: fixed by making updates warning-only.
    - Publisher brief schema rigidity: mitigated.
    - Research stage contract/gate robustness: still the active content-quality blocker.
    - Pre-run cleanup of isolated validation dirs: not implemented; currently a process/documentation gap.

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

- **Todo 78**: Isolate GPU compute regression from last-known-good branch state
  - Operator reports NVIDIA GPU compute was working correctly on this branch most or all of the time on 2026-03-18.
  - `main` is not a practical rollback target because it is significantly behind current agent-stack/runtime work.
  - Treat current GPU-compute doubt as a recent regression candidate in this branch, not as proof of host-level GPU failure.
  - Compare recent agent-stack/config/runtime changes against the last-known-good window and identify the smallest change set that altered observable GPU compute behavior.
  - Success criterion: either prove current branch still executes compute on GPU with live telemetry evidence, or identify the exact recent change that broke that property.

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

- ~~**Todo 96** — AMD live agent detail visibility bug fixed (`api_server.py` + `static/index.html`; `route_active_counts`, `current_profile`/`current_model` surface correctly)~~ ✅ Done 2026-03-18
- ~~**Todo 97** — Regression tests for live route/status synthesis (`scripts/regression_status_synthesis.py`)~~ ✅ Done 2026-03-18

- **Todo 101**: Commit the current working-tree batch before picking up new work
  - Staged changes (from this session): `DEV_NEXT_STEPS.md`, `agent_stack/api_server.py`, `agent_stack/static/index.html`, `agent_stack/agent_profiles/book-skeleton-updater.agent.md`, `agent_stack/scripts/regression_status_synthesis.py`.
  - Untracked runtime artifact `book_project/ollama_run_ledger.jsonl` — decide whether to add to `.gitignore` or commit as a tracked artifact.
  - Write a commit message that groups the status-synthesis telemetry fixes, profile lint fix, and regression script as one logical batch.

- **Todo 102**: Extend regression script to cover book-flow multi-stage `runtime_stage` field
  - The current regression check (`agent_stack/scripts/regression_status_synthesis.py`) validates direct `/api/tasks` profile tasks but does not submit a `book-flow` task and assert that `runtime_stage` becomes non-null for a known pipeline stage.
  - Add a third case with `profile=book-flow` that waits for `runtime_stage` to appear in the `/api/status` task payload and verifies route/model are sourced from stage-level hints (not top-level record defaults).

- **Todo 103**: Surface route quarantine status as an operator-visible UI warning
  - During AMD diagnostic sessions, the AMD agent silently entered quarantine state and the task failed without an obvious operator prompt. The WebUI should prominently show when a route is quarantined, the remaining quarantine duration, and the reason for the last quarantine event.
  - Add a warning banner or highlighted row in the Route Health card whenever `quarantine_active: true` and `quarantine_remaining_seconds > 0`, distinct from the standard `running`/`idle`/`hibernated` states.

- **Todo 104**: Add route hibernation telemetry to the WebUI agent health rows
  - The NVIDIA agent showed `display_state: hibernated` with a `hibernated_at` timestamp during the AMD validation run. The current UI treats hibernated the same as idle for display purposes. Show time-since-hibernation, the hibernation trigger (manual vs. auto) if known, and a quick-exit control to wake the route when appropriate.

- **Todo 105**: Add a `bin/regression` host-side wrapper for the regression suite
  - Add a thin shell script at `bin/regression` that `exec`s `python3 agent_stack/scripts/regression_status_synthesis.py` with the correct working directory so operators can run the full regression check from the workspace root without activating a virtualenv or navigating into the stack.
  - Extend to accept an optional `--profile` flag to narrow the test to a single route (e.g., `bin/regression --profile amd`).

  - Surface AMD and NVIDIA route activity as first-class UI fields instead of relying only on per-agent rows.
  - Show active count, effective stage route, effective model, and whether the information is queue-derived or agent-health-derived so the operator can see why a route is marked active.

- **Todo 106**: Review Live Agents intent vs. operator expectation for direct CLI book-flow runs
  - Clarify whether the Live Agents panel is intended to show only API-managed task activity or all route/model activity on the host, including direct `python -m agent_stack.book_flow` runs.
  - Current finding: `book_flow.py` instantiates its own `OrchestratorAgent()` while the WebUI reads health from the separate global orchestrator in `api_server.py`, so direct CLI runs can consume GPU and never appear in Live Agents.
  - Decide and document the contract explicitly: either keep Live Agents scoped to API/server-managed work, or add shared runtime state so CLI book-flow runs surface as first-class live activity.
  - If the intent is API-only, add an operator-facing note in the UI explaining that direct CLI runs are out-of-band and may not appear there.

- **Todo 107**: Unify orchestrator runtime telemetry between `api_server.py` and direct `book_flow.py` execution
  - Replace the split in-memory orchestrator state with shared runtime telemetry or a single service-owned execution path so route state, current profile/model, and quarantine/hung transitions are visible regardless of whether a run starts from WebUI/API or direct CLI.
  - Ensure Live Agents, route health, and task diagnostics read from the same source of truth.
  - Add a regression case that starts a direct CLI `book_flow` run and verifies the operator surfaces either show the activity or clearly label it as out-of-band.

- **Todo 108**: Add explicit pre-run cleanup policy for isolated validation/output dirs
  - Add a first-class cleanup mode for runs where operator intent is a fresh workspace, not historical accumulation.
  - Status update (2026-03-20): immediate post-publish cleanup is now implemented by default for successful exports via `book_flow`; operators can opt out with `--no-cleanup-after-publish` or `BOOK_FLOW_CLEANUP_AFTER_PUBLISH=false`.
  - Minimum behavior:
    - if `--clean-output-dir` is set, remove prior `runs/` contents and stale per-run artifacts under the selected book slug before creating the new run dir,
    - preserve or optionally reset long-lived state separately (`framework/`, `book_history.jsonl`, `progress_index.json`, `arc_tracker.json`),
    - emit a `run_cleanup_start` / `run_cleanup_complete` journal event naming what was removed.
  - Add a safe default policy for Todo 73-style validation runs so isolated test output dirs do not silently accumulate old attempts.

- **Todo 109**: Default full debug payload logging on all `book_flow` runs
  - Persist `raw_output` and parsed payloads for every stage attempt and recovery to `diagnostics/agent_diagnostics.jsonl` by default.
  - Keep `--no-debug` as an explicit opt-out rather than requiring `--debug` on every run.
  - Update docs/operator guidance so run diagnosis is log-first: `run_journal.jsonl` for stage flow, diagnostics JSONL for payload inspection.

- **Todo 110**: Add per-stage failed-output artifact snapshots for operator review
  - On any quality gate failure, write the failing raw output and parsed payload to stable files inside the run dir, for example:
    - `diagnostics/<stage>/attempt_01_raw.txt`
    - `diagnostics/<stage>/attempt_01_parsed.json`
    - `diagnostics/<stage>/attempt_01_gate.json`
  - This avoids needing to parse large JSONL logs to inspect a specific failed attempt.
  - Link these artifacts from UI/API run status where possible.

- **Todo 111**: Add Todo 73 validation wrapper with clean-run and post-run summary behavior
  - Add a host-side wrapper command/script for the canonical Todo 73 validation flow.
  - Responsibilities:
    - clean the isolated validation output dir before start,
    - launch `book_flow` with default debug logging,
    - print the final run dir and whether the terminal stage was success/failure,
    - surface the most relevant artifact paths (`run_journal.jsonl`, diagnostics JSONL, run summary).

- **Todo 112**: Add stage-fallback counters and terminal fallback summary to `run_summary.json`
  - Record per-stage fallback usage (`research`, `architect_outline`, and future stages) so operators can tell the difference between a clean pass and a fallback-assisted pass.
  - Include fallback reason, fallback type, and whether downstream stages still succeeded.
  - Surface the same summary in CLI output and `/api/status` so Todo 73 reruns do not require manual journal inspection for fallback accounting.

- **Todo 113**: Move or ignore generated Todo 73 validation artifacts outside the normal git working set
  - Current validation runs leave `book_project/todo73-e2e/` and `cli_runtime_activity.json` as persistent working-tree noise.
  - Decide whether these artifacts belong in a fully ignored runtime root, a separate archive path, or an operator-managed export location.
  - Goal: keep future validation reruns visible operationally without constantly polluting `git status`.

- **Todo 114**: Add canon-stage stall watchdog with bounded fallback policy
  - Canon now appears to be the next long-latency risk after chapter-planner fallback success.
  - Add explicit watchdog behavior for `canon` with route-aware timeout thresholds and a deterministic fallback path that preserves continuity constraints.
  - Emit `stage_watchdog_triggered` journal events with elapsed seconds, route/model, and selected mitigation.

- **Todo 115**: Emit per-stage elapsed-time and timeout-budget telemetry
  - Add elapsed seconds and configured timeout budget to each `stage_attempt_result` and `stage_recovery_result` event.
  - Surface the same numbers in `run_summary.json` and `/api/status` so operators can quickly distinguish slow progress from hangs.
  - Include p50/p95 per-stage latency rollups for validation runs.

- **Todo 116**: Add resume-from-checkpoint rerun mode for long pipelines
  - Add a guarded rerun mode that starts from the last successful stage artifact set (for example, resume at `canon` when upstream fallbacks are already locked).
  - Require checksum/manifest validation of upstream artifacts before resume to avoid drift.
  - Goal: reduce repetitive end-to-end reruns when validating fixes for a downstream stage.

- ~~**Todo 117** — Canon route failover when AMD is quarantined (`book_flow.py`; `book-canon-nvidia` profile; failover journaled)~~ ✅ Done 2026-03-18

- **Todo 118**: Introduce quarantine-aware retry backoff windows
  - Retries currently happen fast enough that quarantined agents are retried before cooldown expiry, causing deterministic repeated failure.
  - Add backoff that respects remaining quarantine duration before retrying the same route/agent.
  - Include `quarantine_remaining_seconds` in stage error events to explain retry behavior.
  - **Evaluation snapshot (2026-03-18):** Current canon failures confirm immediate retries hit quarantine repeatedly (`AGENT_QUARANTINED`) instead of waiting for cooldown expiry.
  - **Recommended retry rule:** `next_retry_delay_seconds = max(base_backoff_seconds, quarantine_remaining_seconds + jitter_seconds)` with bounded jitter to avoid synchronized retry storms.
  - **Acceptance checks:** no retry attempt should occur while quarantine is active; run journal should show computed delay and remaining quarantine; total repeated quarantine errors per stage should drop sharply in A/B validation.
  - **Implementation progress (2026-03-18):** `run_stage(...)` now applies quarantine-aware backoff on both stage attempts and recovery attempts when error code is `AGENT_QUARANTINED`, emits `stage_retry_backoff` journal/diagnostic events, and sleeps for computed delay (`remaining + bounded jitter`).
  - **Remaining validation:** run an end-to-end canon failure drill and confirm retries wait for cooldown instead of immediate re-fire.

- **Todo 119**: Add deterministic canon fallback artifact for chapter bootstrap
  - If canon cannot be generated after retries/recovery/failover, synthesize a minimal valid canon payload from `book_brief`, `outline_payload`, and `chapter_spec` to preserve pipeline continuity.
  - Persist it as a clearly tagged fallback artifact and record a `stage_fallback_applied` event for `canon`.
  - Keep fallback strict and auditable so downstream editorial stages can proceed without silent schema drift.
  - **Evaluation snapshot (2026-03-18):** Canon remains a critical choke point; even with route failover, both primary and fallback routes can fail under endpoint/model instability.
  - **Implementation progress (2026-03-18):** Added deterministic fallback path in `book_flow.py` after canon retries/recovery/failover exhaustion; writes `canon.json` plus `canon_fallback_metadata.json` and emits `stage_fallback_applied` for `canon`.
  - **Remaining validation:** run a forced canon-failure drill and verify downstream stages (`writer`, editorial chain) proceed with fallback artifacts and no schema break.

- **Todo 130**: Add fallback provenance and checksum verification for generated fallback artifacts
  - Record source inputs (`book_brief`, `outline_payload`, `chapter_spec`) fingerprints and fallback payload checksum in metadata.
  - Expose provenance in run summary and diagnostics so operators can audit exactly which inputs produced fallback state.
  - Add verification step before resume/reconcile to detect fallback artifact drift or manual corruption.
  - **Implementation progress (2026-03-18):** canon fallback metadata now includes deterministic source-input hashes and fallback payload checksum; run journal fallback event also records these hashes/checksum for audit.
  - **Remaining validation:** add resume/reconcile checksum verification pass and surface checksum mismatch warnings in diagnostics/UI.

- **Todo 131**: Add fallback parity contract checks for deterministic artifacts
  - Validate fallback artifacts preserve minimum required semantic anchors (chapter id/title, section goal, ending hook, constraints) before downstream stages consume them.
  - Add a contract report (`fallback_contract_report.json`) that marks each required anchor as present/missing.
  - Block resume if fallback parity checks fail, with actionable diagnostics for operator repair.
  - **Implementation progress (2026-03-18):** added `validate_fallback_canon_contract(...)` in `book_flow.py`, writes `fallback_contract_report.json`, logs `stage_fallback_contract_report`, and raises integrity error when required anchors are missing.
  - **Remaining validation:** ensure resume/reconcile paths consume the same contract report and prevent reuse of parity-failed fallback artifacts.

- **Todo 132**: Add reconcile-time verification for fallback metadata and parity reports
  - During reconcile/startup recovery, detect fallback artifacts and verify both checksum metadata and parity contract report before re-queue/resume.
  - Emit explicit recovery diagnostics for mismatch cases: `fallback_checksum_mismatch`, `fallback_contract_failed`, `fallback_metadata_missing`.
  - Route invalid fallback runs into operator-review state instead of auto-resuming with untrusted artifacts.
  - **Implementation progress (2026-03-18):** `api_server.py` now verifies canon fallback integrity (`canon_fallback_metadata.json`, `fallback_contract_report.json`, and checksum parity against `canon.json`) during both task-ledger startup bootstrap and `/api/book-jobs/reconcile`; invalid fallback runs are forced to `failed + hold` with `fallback_integrity_failed` run-journal events and are not auto-resumed.
  - **Remaining validation:** run two drills (valid fallback and tampered fallback) and confirm reconcile/startup behavior, `skipped.reason=fallback_integrity_failed`, and operator-visible error/issue payloads.

- **Todo 133**: Surface fallback-integrity state in UI/CLI production status summaries
  - Expose `fallback_integrity.checked/valid/issues` from production status in `/api/status` and UI cards so operators can identify blocked runs without log digging.
  - Add explicit operator guidance text for `failed + hold` fallback-integrity blocks (how to repair artifacts and safely retry).
  - **Implementation progress (2026-03-18):** `api_server.py` now emits per-task `fallback_integrity_summary` and top-level `fallback_integrity_blocks` in the status/UI payload, including repair guidance for checksum mismatch, missing metadata, and failed contract cases.
  - **Remaining validation:** restart the API, induce a blocked fallback-integrity run, and verify both `/api/status` and `/api/ui-state` expose the block without needing nested artifact inspection.

- **Todo 134**: Add a fallback-integrity drill for status/UI visibility
  - Create a lightweight operator drill that tampers with a canon fallback artifact, runs reconcile, and asserts the status payload surfaces `fallback_integrity_blocks` plus task-level guidance.
  - Include the clean-path control case where verified fallback artifacts remain resumable and no block is emitted.
  - **Implementation progress (2026-03-19):** `agent_stack/scripts/fallback_integrity_drill.py` written and passing 6/6: CLEAN, TAMPERED-CHECKSUM, FAILED-CONTRACT, NO-FALLBACK-EVENT artifact-level cases + live-API phantom-block guard. Also caught and fixed a checksum parity bug: `_stable_payload_sha256` in `api_server.py` was using `ensure_ascii=False` with no `default=str`, diverging from `book_flow.payload_sha256` (`ensure_ascii=True, default=str`); real-run checksums would never have verified. Fixed and compile-confirmed.
  - **Remaining validation:** run against a real forced canon-failure scenario (live book run, not synthetic temp dir) to confirm blocking and repair guidance surface in UI.

- **Todo 139**: Add manuscript attribution signature (pen name, publisher, copyright)
  - Every final manuscript export should carry ownership metadata: author pen name, publisher/company name, and copyright year, both prepended to the manuscript text and recorded in `run_summary.json`.
  - Support multiple pen names based on book type (e.g. `Demosthenes`, `Locke`, custom approved names) and the default ownership entity `DaRaVeNrK LLC`.
  - **Implementation progress (2026-03-19):** `BookFlowRequest` in `api_server.py` now has `pen_name` (default `DaRaVeNrK`) and `publisher_name` (default `DaRaVeNrK LLC`) fields. `book_flow.py` now carries both fields through `context_store["book"]`, prepends a title page (author / publisher / copyright / chapter header) to `manuscript_v1.md` and `manuscript_v2.md`, and records an `attribution` block in `run_summary.json`. CLI flags `--pen-name` and `--publisher-name` added to `build_parser()`. Compile-confirmed.
  - **Remaining:** add per-book attribution config persistence (Todo 140), surface in UI (Todo 141), expand to colophon/legal page (Todo 142).

- **Todo 140**: Add per-book attribution config file (`book_attribution.json`)
  - Store `pen_name`, `publisher_name`, and optional `isbn_placeholder` under `{book_root}/book_attribution.json` so operators set attribution once per book and it is automatically loaded for every subsequent chapter/run — no need to repeat flags or API fields.
  - Auto-create with defaults (`DaRaVeNrK` / `DaRaVeNrK LLC`) on first run if the file does not exist.
  - Allow override: `--pen-name` CLI flag and `pen_name` API field take precedence over the file, and on override, update the file for future runs.
  - Add a `pen_name_status` field (e.g. `provisional`, `publisher_approved`) so operators can track approval state; block final export if status is `provisional` and a strict-mode flag is set.

- **Todo 141**: Surface attribution in WebUI run-summary card and status payload
  - Expose `attribution.pen_name`, `attribution.publisher_name`, and `attribution.pen_name_status` (from Todo 140) in `/api/status` task records and in the UI run-summary cards.
  - Add a visual badge for pending/unconfirmed pen names so operators see at a glance when a run used an unapproved pen name.
  - Include attribution in the `/api/book-jobs/report` export and any downloadable run-summary artifacts.

- **Todo 142**: Expand title page to full legal colophon / front matter
  - Replace the current minimal title-page block with a proper legal front matter section suitable for EPUB/print-on-demand formatting: title, author pen name, publisher, copyright year, first-edition statement, all-rights-reserved language, ISBN/ASIN placeholder, and contact/rights-inquiry line for DaRaVeNrK LLC.
  - Write the colophon to a dedicated `06_final/colophon.md` in addition to prepending it to the manuscript, so it can be styled independently by downstream export tooling.
  - Add a colophon schema so future export stages can template it for different output formats (Markdown, EPUB OPF metadata, PDF cover-page injection).

- **Todo 135**: E2E canon fallback drill with live book run
  - Force a canon exhaustion in a real book-flow run (by triggering model/route failures) and verify the full pipeline: fallback artifact written → `stage_fallback_applied` event → downstream stages (writer, editorial) consume the fallback → quality gates reflect fallback provenance → final export marks "produced via canon fallback".
  - Confirm `_verify_canon_fallback_integrity` correctly blocks/allows that run at reconcile and that the operator sees the block in `/api/status` + `/api/ui-state` without log-diving.
  - Document the minimum recovery procedure: tamper fix → artifact re-verify → hold release → safe-resume.

- **Todo 136**: Generalize fallback integrity to sections_written and other fallback-able stages ✅ IMPLEMENTED
  - **Implementation complete** (api_server.py + fallback_integrity_drill.py, drill 6/6 passing):
    - Added `_FALLBACK_STAGE_CONFIGS` registry dict (currently: `"canon"` entry; add new stages here)
    - Added `_verify_stage_fallback_integrity(run_dir, stage, config)` — generic verifier replacing canon-specific logic
    - Added `_any_fallback_stage_failed(fallback_integrity: dict) -> (bool, issues, failed_stages)` helper
    - Added `_verify_all_stage_fallback_integrity(run_dir) -> {stage: result}` convenience wrapper
    - Kept `_verify_canon_fallback_integrity(run_dir)` as backward-compat thin wrapper
    - `fallback_integrity` in `production_status` is now `{"canon": {...}}` per-stage dict
    - `_fallback_integrity_summary()` now returns `blocked_stages`, `all_issues`, and per-stage `stages` dict
    - All 3 reconcile/startup guards updated to use `_any_fallback_stage_failed()`
    - **Guard logic tightened**: early-return now fires when `not fallback_event_seen` (journal is authoritative); a metadata file without a corresponding journal event is skipped, not checked
    - Drill updated to match new metadata schema (`"fallback": True, "stage": "canon"`)
  - Remaining: register additional stages (e.g. `sections_written`) when their fallback paths are built

- **Todo 137**: Add fallback artifact staleness/expiry detection ✅ IMPLEMENTED
  - **Implementation complete** (api_server.py + fallback_integrity_drill.py, drill 8/8 passing):
    - Added `_FALLBACK_STALE_HOURS: float` constant (default 72h, overridable via `FALLBACK_STALE_HOURS` env var) to `api_server.py`
    - `_verify_stage_fallback_integrity()` now parses `generated_at` from fallback metadata (accepts ISO-8601 or Unix float string) and emits:
      - `fallback_artifact_stale` — age > `_FALLBACK_STALE_HOURS`
      - `fallback_generated_at_missing` — field absent from metadata
      - `fallback_generated_at_unparseable` — field present but not parseable
    - `age_hours` (rounded to 2dp) and `generated_at` are surfaced in the per-stage result dict when present
    - These issues flow through `_any_fallback_stage_failed()` and block auto-resume via existing reconcile guards — no new guard wiring needed
    - Drill updated: added `FRESH_TIMESTAMP`/`STALE_TIMESTAMP` constants, `_build_stale_run_dir`, `_build_missing_generated_at_run_dir`, and `test_stale_artifact` / `test_missing_generated_at` test cases
  - Remaining: add stale-specific repair guidance hint in `_fallback_integrity_summary()`'s `guidance` field

- **Todo 138**: Add final-export fallback provenance annotation ✅ CORE IMPLEMENTED
  - **Core implementation complete** (`book_flow.py`): `run_summary.json` now includes top-level `used_fallbacks` and a `fallback_provenance` block containing `used_fallbacks`, `used_fallback_count`, `human_review_recommended`, and a provenance note.
  - Fallback stages are derived from `run_journal.jsonl` via `stage_fallback_applied` events (authoritative event-driven provenance).
  - Remaining: expose this provenance in `/api/status` + WebUI run-summary card and add configurable release veto policy requiring operator sign-off when `used_fallbacks` is non-empty.

- **Todo 143**: Surface fallback provenance in `/api/status` and `/api/ui-state` ✅ CORE IMPLEMENTED
  - **Core implementation complete** (`api_server.py`): task payloads now include `fallback_provenance_summary` (derived from `run_summary.json`) with `used_fallbacks`, `used_fallback_count`, `human_review_recommended`, and `note`.
  - `fallback_integrity_blocks` now include fallback provenance fields (`used_fallbacks`, `human_review_recommended`) so operator alerts carry both integrity and provenance context.
  - Remaining: consume/render this in the WebUI run-summary card for at-a-glance visibility.

- **Todo 144**: Add fallback sign-off release policy gate
  - Introduce a configurable policy that blocks `final_export` completion when `used_fallbacks` is non-empty and no explicit operator sign-off is present.
  - Record sign-off decision, actor, timestamp, and rationale in `run_journal.jsonl` and `run_summary.json` for auditability.

- **Todo 145**: Archive-time provenance manifest and checksum attestation
  - Generate `fallback_provenance_manifest.json` during archive/export containing fallback stages, artifact paths, metadata checksums, and integrity verdict snapshot.
  - Include manifest hash in export logs to support immutable provenance audits.

- **Todo 146**: Add provenance regression checks in `regression_status_synthesis.py` ✅ CORE IMPLEMENTED
  - **Core implementation complete** (`agent_stack/scripts/regression_status_synthesis.py`): added schema validators for task `fallback_provenance_summary` and `run_summary.json` fallback provenance fields.
  - Added synthetic fixture coverage for both fallback-used and no-fallback variants (`fixture_provenance_regression`) to prevent schema drift.
  - Added live provenance validation path (`live_provenance_regression`) that checks `/api/status` book-flow tasks and validates `run_summary.json` when available.
  - Remaining: wire this script into the standard regression runner wrapper so provenance checks are always executed in CI/operator smoke runs.

- **Todo 147**: Add `/api/status` filter for fallback-used runs ✅ CORE IMPLEMENTED
  - **Core implementation complete** (`api_server.py`): `/api/status` now accepts optional query params `fallback_used` (`true|false`) and `fallback_stage` (e.g. `canon`).
  - Filtering is applied in `_build_status_payload()` against task `fallback_provenance_summary.used_fallbacks`, and works across queued/running/completed/cancelled tasks in the status window.
  - Remaining: add explicit regression checks for the new query params and document examples in operator/API docs.

- **Todo 148**: Add fallback provenance badge + review CTA in WebUI
  - Display a clear provenance badge in the run list/card when `used_fallbacks` is non-empty.
  - Add a one-click “review fallback artifacts” action linking to run dir and integrity guidance.

- **Todo 149**: Add regression wrapper target for provenance checks
  - Add a `bin/regression-provenance` host-side wrapper that runs `regression_status_synthesis.py` in fixtures-only mode and reports pass/fail succinctly.
  - Integrate it into the existing operator regression checklist to ensure fallback provenance schema stability after API changes.

- **Todo 150**: Add explicit API contract docs for fallback provenance fields
  - Document `fallback_provenance_summary` and `fallback_integrity_blocks.used_fallbacks` in API docs/user guide with example payloads for fallback-used and clean runs.
  - Include backward-compat notes for clients expecting older status payloads.

- **Todo 151**: Add minimal UI contract test for fallback provenance rendering
  - Add a lightweight UI payload fixture test ensuring provenance badges and review CTA visibility rules map correctly from status payload fields.
  - Cover both empty and non-empty `used_fallbacks` states to prevent false-positive badges.

- **Todo 152**: Add regression assertions for `/api/status` fallback filters ✅ CORE IMPLEMENTED
  - **Core implementation complete** (`agent_stack/scripts/regression_status_synthesis.py`): added `live_status_filter_regression()` covering `/api/status?fallback_used=true`, `/api/status?fallback_used=false`, and `/api/status?fallback_stage=canon`.
  - Added assertions that filtered results satisfy semantics and consistency constraints (membership correctness, disjoint true/false sets, and subset-of-base invariants).
  - Remaining: add explicit mixed-stage fallback fixtures once additional fallback stages (beyond `canon`) are introduced.

- **Todo 153**: Add API examples for fallback filtering in `USER_GUIDE.md` ✅ CORE IMPLEMENTED
  - **Core implementation complete** (`USER_GUIDE.md`): added copy-paste `curl` + `jq` examples for `/api/status`, `fallback_used=true`, `fallback_used=false`, `fallback_stage=canon`, and combined filter usage.
  - Added interpretation notes for `fallback_provenance_summary` vs `fallback_integrity_summary.blocked`, plus status-window scope caveat.
  - Remaining: mirror these examples in any external API reference pages if they diverge from `USER_GUIDE.md`.

- **Todo 154**: Add UI filter controls for fallback-used/stage
  - Add WebUI toggles/dropdown that map directly to `/api/status` filter params.
  - Provide persistent filter state in UI session storage for operator workflows.

- **Todo 155**: Add `status_filter_regression` mode flag to synthesis script
  - Add a CLI switch to run only provenance/filter checks without route queue synthesis for fast operator smoke tests.
  - Keep full mode as default to preserve existing broad regression coverage.

- **Todo 156**: Add operator cheat sheet for fallback triage queries
  - Document high-signal query combinations (`fallback_used=true`, `fallback_stage=canon`, hold/blocked focus) and expected interpretation.
  - Include copy-paste curl examples and a short decision flow for hold-clear vs re-run.

- **Todo 157**: Add API pagination notes for filtered status windows
  - Clarify interaction between current status task window limits and fallback filters to prevent missed-run assumptions.
  - Define next-step design for stable pagination/cursor if filtered views become primary operator workflow.

- **Todo 158**: Add `--filters` shortcuts to `agentctl server-status`
  - Support CLI flags mapping to `/api/status` filter params (`--fallback-used`, `--fallback-stage`) for operator speed.
  - Add compact tabular output mode focused on provenance/integrity triage columns.

- **Todo 159**: Add fallback-stage enum validation in API layer ✅ CORE IMPLEMENTED
  - **Core implementation complete** (`api_server.py`): `/api/status` now validates `fallback_stage` against registered fallback stages from `_FALLBACK_STAGE_CONFIGS`.
  - Invalid values return HTTP 400 with explicit guidance including valid stage values.
  - Regression coverage extended (`regression_status_synthesis.py`) with invalid-stage checks and environment-aware fallback behavior.
  - Remaining: once the live API process is refreshed, promote the invalid-stage check from fallback/skip behavior to strict live-only assertion.

- **Todo 160**: Add status payload contract snapshot fixtures
  - Store representative JSON snapshots for unfiltered and filtered `/api/status` responses.
  - Use snapshot diffs as a guard against accidental breaking changes in operator-facing fields.

- **Todo 161**: Add strict-live mode for regression filter checks ✅ CORE IMPLEMENTED
  - **Implementation**: Added `--strict-live` CLI flag to `regression_status_synthesis.py` main()
  - `live_status_filter_invalid_stage_regression(strict_live=False)` now accepts parameter
  - Default (strict_live=False): Returns SKIP on ModuleNotFoundError (host-side tolerance for missing API deps)
  - Strict mode (strict_live=True): Returns FAIL if in-process validation unavailable (CI/container enforcement)
  - Usage: `python3 agent_stack/scripts/regression_status_synthesis.py --strict-live`
  - Validates that CI/container runs don't hide stale-server issues by requiring observable invalid-stage rejection

- **Todo 162**: Add stage-normalization policy for filter input ✅ CORE IMPLEMENTED
  - **Policy**: Case-insensitive (auto-lowercase), whitespace-trimmed matching for `fallback_stage` filter parameter
  - **Implementation**: Added `_normalize_fallback_stage(input_value)` helper in `api_server.py` with docstring and examples
  - **Behavior**: "CANON", "Canon", "  canon  " all normalize to "canon"; internal spaces rejected as invalid (e.g., "can on")
  - **Error handling**: HTTP 400 with guidance on valid values if normalized input doesn't match registered stages
  - **Documentation**: Added "Stage Normalization Policy" subsection in `USER_GUIDE.md` with URL-encoded examples showing equivalence
  - **Result**: Operators can use any case variation without memorizing exact stage names

- **Todo 163**: Add API error contract examples for invalid filters ✅ CORE IMPLEMENTED
  - **Added "API Error Contract" section in USER_GUIDE.md** with HTTP 400/422 error responses
  - **Invalid `fallback_stage`**: Example request/response showing HTTP 400 with valid-values guidance
  - **Invalid `fallback_used`**: Example showing HTTP 422 Validation Error for non-boolean values
  - **Malformed combinations**: Examples of multiple `fallback_stage` values (first used) and valid AND-logic filters
  - **Common error scenarios table**: 6 troubleshooting rows covering typos, spaces, case sensitivity, empty results, and server errors
  - **Key guidance**: Error messages list valid stage names; copy-paste ready; case-insensitive for stages but strict for booleans

### Suggested Next Todos (for evaluation)

- **Todo 164**: Add fallback artifact expiry policy enforcement
  - Extend Todo 137 staleness detection with automatic cleanup/archival of artifacts older than threshold
  - Add operator command: `book-flow --audit-fallbacks --archive-stale` to safely move old artifacts to archive without deleting
  - Document retention policy for production vs dev environments, with recovery procedures for archived artifacts
  - Add warning in diagnostics when a run uses an artifact approaching expiry (e.g., >90% of FALLBACK_STALE_HOURS elapsed)

- **Todo 165**: Add "repair fallback artifact" operator workflow and CLI commands
  - Implement `book-flow --repair-fallback-artifact <book> <stage>` to validate and repair corrupted fallback metadata
  - Add integrity recovery options: re-compute checksums, regenerate metadata from payload file, or restore from backup
  - Update USER_GUIDE.md with step-by-step repair procedure for common fallback corruption scenarios
  - Add dry-run mode to preview repairs before applying

- **Todo 166**: Add fallback integrity audit command for offline analysis
  - Implement `book-flow --audit-all-fallbacks` to scan all books and stages for stale/missing/corrupted artifacts
  - Produce audit report showing: deployment status, staleness, checksum validity, metadata health, and recommendations
  - Add filtering: `--stage=canon`, `--older-than=30d`, `--corrupted-only` for targeted diagnostics
  - Export audit results as JSON for programmatic processing and monitoring integration

- **Todo 99**: Add profile-lint gate before stack startup ⭐ QUICK WIN ✅ ENHANCED
  - **Core gate already existed**: `validate_agent_profiles.py` was already running lint validation at orchestrator startup
  - **Checks validate**: Required fields (name, route, model), type correctness (think=bool, temperature=0.0-2.0), integer ranges (num_ctx>0), duplicate names, unsupported routes, required markdown sections, system prompt size
  - **Enhancement added**: Startup event in `api_server.py` prints brief lint summary at startup:
    - `[PROFILE-LINT] ✓ PASS: 18 profiles, 0 errors, 0 warnings`
    - Friendly visibility for operators running `agent-stack-up`
    - Lists errors with profile name and specific issue for quick debugging
  - **Result**: Prevents bad configurations from silently taking the control plane offline; operators see immediate feedback

- **Todo 108**: Normalize runtime artifact ownership for `book_project/` outputs ✅ CORE IMPLEMENTED
  - **Policy documented**: Group-writable (0o775 dirs, 0o664 files) with user:docker ownership
  - **Module created**: `artifact_ownership.py` with diagnostic and repair functions
  - **Diagnostic functions**: Identifies 7 root-owned files, 3 with wrong permissions in test environment
  - **CLI commands added**:
    - `book-flow check-ownership`: Report status with exit code for scripting
    - `book-flow fix-ownership`: Repair issues with optional `--dry-run` mode
  - **Usage**: `python3 -m agent_stack.cli check-ownership` or `fix-ownership --dry-run`
  - **Result**: Operators can now diagnose and fix root-owned artifact issues without privilege escalation

- ~~**Todo 109** — Explicit journal closure for CLI `book_flow.py` failures (`run_failure` appended; `run_summary.json` on failure; CLI activity cleared)~~ ✅ Done 2026-03-19

- **Todo 120**: Evaluate stage-by-stage think mode policy and default profile settings
  - Build an evidence-based matrix for each major stage (`publisher_brief`, `research`, `architect_outline`, `chapter_planner`, `canon`, `writer`, editorial stages) indicating when `think: false` improves reliability/latency and when deeper reasoning is worth the cost.
  - Capture measurable criteria: schema pass rate, retry count, latency, hallucination/continuity regressions, and token economy impact.
  - Produce an implementation plan for profile updates (including safe defaults and rollback toggles) and document final policy in the operator guide.

  Suggestions / Open Questions:
  - All major agent profiles currently have `think: false`. Should any stages (e.g., research, canon, or writer) enable `think: true` for deeper reasoning?
  - Consider A/B testing `think: true` on research or outline stages to measure impact on schema pass rate and hallucination.
  - Add a CLI or config toggle to override `think` per run for rapid experimentation.
  - Document observed tradeoffs: does `think: true` increase latency or cost, and is the quality gain (if any) worth it?
  - Should “think” be adaptive (e.g., enabled only on retry or fallback) rather than static per profile?
  - Gather operator feedback on where “think” has been most/least valuable in production runs.

- **Todo 121**: Redesign WebUI with hardware-first system view (AMD + NVIDIA GPU pools)
  - Add a dedicated Hardware view that visually separates the two model environments: AMD/ROCm pool and NVIDIA pool, including per-route capacity and active model assignment.
  - Integrate ROCm SMI telemetry for AMD cards: GPU temperature, average board power, VRAM usage, and GPU utilization (with refresh cadence and stale-data indicators).
  - Add equivalent NVIDIA telemetry cards (temperature, power, VRAM, GPU utilization) so operators can compare both pools side by side and quickly identify thermal/power bottlenecks.
  - Define data contract and backend aggregator endpoint for hardware metrics, including unit normalization and safety thresholds for warning/critical UI states.
  - Include UX acceptance criteria: at-a-glance health summary, per-GPU detail drill-down, and clear routing context showing which stages/models are currently mapped to each hardware pool.

- **Todo 122**: Design gate-issued monetary reward economy for model selection and quality outcomes
  - Define a spend-and-settle reward economy where each stage attempt "spends" budget based on model size/cost and latency, then settles reward based on gate outcomes (pass/fail, retries, and downstream stability).
  - Make acceptance/quality-gate agents the reward authorities: they mint positive rewards for strong, first-pass, schema-valid outputs and apply penalties for failures, retries, regressions, or avoidable over-spend on oversized models.
  - Add model-to-task-size efficiency policy so agents are rewarded for choosing the smallest reliable model that passes quality, while escalating to larger models only when evidence shows reliability gain.
  - Add failure-learning loop: when a profile fails repeatedly, reduce its effective budget/priority and record recovery strategies that improved pass rate, then gradually restore budget after sustained success.
  - Define accounting artifacts: per-stage cost/reward ledger entries, profile wallet balances, gate-issued reward events, and operator dashboards showing ROI by profile/model/route.

- **Todo 123**: Analyze queue-depth-aware scheduling and liberal model swapping under multi-queue load
  - Evaluate whether higher queue concurrency should unlock more aggressive cross-route/model swapping, including temporary promotion to alternate models when multiple queues are active and latency pressure rises.
  - Define guardrails so liberal swapping improves throughput without destroying determinism: queue-depth thresholds, per-stage allowlists, VRAM headroom checks, and quarantine-aware exclusions.
  - Compare policies for single-queue vs multi-queue operation: conservative stable routing when the system is quiet, adaptive scheduling when backlog grows, and explicit rollback when quality gates regress.
  - Produce an analysis matrix covering throughput gain, quality impact, retry rate, cost/power impact, and fairness across AMD/NVIDIA pools.
  - Recommend whether this should be implemented as an orchestrator policy layer, a pressure-mode extension, or a route planner plugin with operator override controls.

- **Todo 124**: Run a dedicated LLM feature-dispatch review (`think`, tool-calling model, coding model)
  - Build a stage-and-task dispatch matrix defining when to use: standard no-think calls, `think: false`, `think: true`, tool-oriented model calls, and coding-oriented model calls.
  - Include hard decision criteria: output type required (strict JSON vs prose), expected tool use, code-generation depth, latency budget, retry risk, and continuity risk.
  - Define default and override rules per stage (`publisher_brief`, `research`, `architect_outline`, `chapter_planner`, `canon`, `writer`, editorial chain), including rollback toggles and safe fallbacks.
  - Add observability requirements so every call logs which dispatch rule fired and why (rule id + stage + selected profile/model/options).
  - Deliverable: operator-facing "LLM Feature Dispatch Playbook" with examples and anti-patterns.

- **Todo 125**: Extend OpenClaw technical analysis with an explicit Dragonlair feature-routing crosswalk
  - Add a required output to Todo 65: map OpenClaw control patterns to Dragonlair decisions for `think` usage, tool-LLM selection, and coding-LLM selection.
  - Identify what OpenClaw can answer directly, what Dragonlair must decide independently, and where book-flow constraints require stricter policy than OpenClaw defaults.
  - Produce a gap table: `Decision area`, `OpenClaw reference behavior`, `Current Dragonlair behavior`, `Recommended Dragonlair policy`, `Evidence required`.
  - Include an implementation sequence so policy work lands in this order: profile defaults -> orchestrator decision layer -> diagnostics fields -> UI visibility.
  - Success criterion: after Todo 65 closes, operators can explain and predict which LLM feature path is selected for each stage without inspecting source code.

- **Todo 126**: Add explicit `think` policy lint and enforcement across all stage profiles
  - Require every active stage profile to declare `think` explicitly (`true` or `false`) so behavior never depends on model defaults.
  - Add lint failure for missing `think` on critical production profiles (`book-*` stages) and emit a startup warning/error before runs begin.
  - Add a migration checklist to update existing profiles and verify resolved options in diagnostics.

- **Todo 127**: Handle "thinking present, final response empty" as a first-class failure mode
  - Add explicit error classification when payload contains non-empty `thinking` but empty final `response`.
  - Record this classification in diagnostics and quarantine events so operators can distinguish endpoint outage from model output-channel mismatch.
  - Define policy: retry with adjusted options/profile, fail over route/model, or force deterministic fallback artifact based on stage criticality.

- **Todo 128**: Add context-budget right-sizing analysis for writer and canon stages
  - Measure effective prompt footprint versus configured `num_ctx` using run diagnostics and prompt-length telemetry.
  - Quantify how much prior-run material (notes, canon excerpts, section summaries) is actually needed for quality gates to pass.
  - Produce bounded context-injection rules so prior-run memory is included when relevant without oversized context overhead.

- **Todo 129**: Add controlled experiment harness for feature-routing policy validation
  - Run fixed-seed A/B validation sets across policy variants (`think` off/on, tool-LLM path, coding-LLM path, model size changes).
  - Track comparable metrics: stage pass rate, retries, latency, fallback incidence, continuity score, and token/cost efficiency.
  - Require a signed decision report before promoting new routing defaults to production profiles.

- **Todo 100**: Add diagnostics submission lane when book mode is active
  - Add an explicit, operator-only diagnostics endpoint or flag that allows low-impact test submissions (for route visibility checks) without disabling active book mode.
  - Keep safeguards: rate-limit diagnostics tasks, tag them clearly in `/api/status`, and block content-writing jobs so diagnostic probing cannot interfere with production book runs.

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

- ~~**Todo 52** — Centralized exception-to-HTTP mapper (`AgentStackError.code` → status codes; uniform streaming/non-stream error payloads)~~ ✅ Done 2026-03-18

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
