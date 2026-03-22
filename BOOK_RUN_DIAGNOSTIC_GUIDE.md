# Book Run Diagnostic Guide

This document provides a standard procedure for diagnosing failed book runs in the Dragonlair system. Use this as a checklist and reference for future troubleshooting.

---

## 1. Locate Output Directory
- Default: `/home/daravenrk/dragonlair/book_project/`
- New layout groups each request under a per-book folder:
  - `/home/daravenrk/dragonlair/book_project/<book-slug>/runs/<timestamp>-chNN-<section-slug>/`
- Per-book history log:
  - `/home/daravenrk/dragonlair/book_project/<book-slug>/book_history.jsonl`

## 2. Key Log and Output Files
- `changes.log`: Main agent and stage log (JSONL entries)
- `diagnostics/agent_diagnostics.jsonl`: Detailed diagnostics (if verbose mode enabled)
- Stage output files: e.g., `00_brief/book_brief.json`, `01_research/research_dossier.md`, etc.
- Research bootstrap artifacts: `01_research/source_packets.json` and any fetch/scrape outputs written under `book_project/01_research/`
- `run_summary.json`: Final run outcome, artifact validation, and key metadata

## 3. Useful Shell Commands
- List all run directories:
  ```sh
  find /home/daravenrk/dragonlair/book_project -type d -path "*/runs/*" | sort
  ```
- View recent per-run log entries:
  ```sh
  tail -n 50 /home/daravenrk/dragonlair/book_project/<book-slug>/runs/<run-name>/changes.log
  ```
- Search for errors or failures:
  ```sh
  grep -i 'fail\|error\|exception' /home/daravenrk/dragonlair/book_project/<book-slug>/runs/<run-name>/changes.log
  ```
- Check for incomplete or missing output files:
  ```sh
  find /home/daravenrk/dragonlair/book_project/ -type f -empty
  ```

## 4. Diagnostic Checklist
- [ ] Confirm run directory and presence of `changes.log`
- [ ] Check for diagnostics logs (if enabled)
- [ ] Identify last completed stage in `changes.log`
- [ ] Look for error, fail, or exception entries
- [ ] Review agent health and quarantine status if applicable
- [ ] Check for missing or empty output files in expected stage folders
- [ ] Inspect `01_research/source_packets.json` to confirm bootstrap gathered non-empty packets before the research LLM call
- [ ] If `raw_output` is `null` in early stages, inspect `diagnostics/agent_diagnostics.jsonl` and confirm the orchestrator runtime includes the 2026-03-21 `_invoke_with_triage()` success-path fix
- [ ] If API server is involved, check `/home/daravenrk/dragonlair/agent_stack/api_server.log`
- [ ] Verify stage route/model mapping in `run_journal.jsonl` matches current strategy standard
- [ ] Verify profile planning output (`route`, `model`) for critical stages before relaunch
- [ ] Verify fallback events are explicit (`stage_fallback_applied` or `stage_warning`) and not silent stops
- [ ] Verify adaptive quality events are present (`quality_thresholds_loaded`, `quality_learning_state_updated`)
- [ ] Verify `run_summary.json` contains `quality_thresholds` (base/effective/snapshot)
- [ ] Verify per-book `quality_learning_state.json` exists and updates after successful runs

## 4.1 Strategy Validation Commands
- Run preflight validator (recommended):
  ```sh
  cd /home/daravenrk/dragonlair && PYTHONPATH=/home/daravenrk/dragonlair \
    python3 -m agent_stack.scripts.validate_run_strategy --json
  ```
- Write compliance report artifact:
  ```sh
  cd /home/daravenrk/dragonlair && PYTHONPATH=/home/daravenrk/dragonlair \
    python3 -m agent_stack.scripts.validate_run_strategy \
    --report-path /home/daravenrk/dragonlair/book_project/policy_compliance_report.json --json
  ```
- Run synthetic drift self-test:
  ```sh
  cd /home/daravenrk/dragonlair && PYTHONPATH=/home/daravenrk/dragonlair \
    python3 -m agent_stack.scripts.validate_run_strategy --self-test-drift --json
  ```
- Confirm effective profile mapping:
  ```sh
  cd /home/daravenrk/dragonlair && python3 << 'PY'
  import sys
  sys.path.insert(0, '.')
  from agent_stack.orchestrator import OrchestratorAgent
  orch = OrchestratorAgent()
  for p in ("book-publisher-brief", "writing-assistant", "book-canon", "book-writer"):
      plan = orch.plan_request("diag", profile_name=p)
      print(p, "=>", plan["route"], plan["model"])
  PY
  ```
- Confirm last stage route/model events:
  ```sh
  rg -n 'stage_attempt_start|stage_fallback_applied|stage_warning|run_success|run_failure' /home/daravenrk/dragonlair/book_project/<book-slug>/runs/<run-name>/run_journal.jsonl
  ```
- Confirm adaptive quality threshold state:
  ```sh
  RUN_DIR=/home/daravenrk/dragonlair/book_project/<book-slug>/runs/<run-name>
  rg -n 'quality_thresholds_loaded|quality_learning_state_updated' "$RUN_DIR/run_journal.jsonl"
  jq '.quality_thresholds' "$RUN_DIR/run_summary.json"
  jq '.' /home/daravenrk/dragonlair/book_project/<book-slug>/quality_learning_state.json
  ```

## 4.2 DR Run Consistency Validation
- Run consistency DR audit over active + historical runs:
  ```sh
  cd /home/daravenrk/dragonlair
  bin/run-consistency-dr --max-runs 50
  ```
- JSON output for automation/reporting:
  ```sh
  cd /home/daravenrk/dragonlair
  bin/run-consistency-dr --json > /home/daravenrk/dragonlair/book_project/drill_reports/run_consistency_latest.json
  ```
- What this detects:
  - missing `run_start`
  - missing terminal event (`run_success|run_failure|forced_completion`) in archived runs
  - progress events after terminal seal (consistency drift)
  - stage attempt sequencing anomalies (warning-level)


## 5. Common Issues
- Early failure (empty drafts, short log): Likely input, config, or environment error
- `raw_output: null` on research/outline/planner/canon with no direct model exception: likely stale orchestrator runtime missing the `_invoke_with_triage()` return-path fix; rebuild/restart the active runtime and re-run diagnostics
- `source_packets.json` contains only `book:premise_anchor` or empty facts: bootstrap web lookups likely failed or the runtime lacks optional scraping dependencies; confirm outbound network access and runtime dependency parity
- Research prompt contains irrelevant public-web packets: query selection or result filtering needs tightening before relaunching long runs
- Run halts after canon with `NameError: build_section_consistency_sections is not defined`: runtime is missing the section consistency helper. Confirm function exists in `agent_stack/book_flow.py` and that the active runtime uses rebuilt code.
- Agent quarantine: Check orchestrator health report and error details
- Dependency errors: See API server logs for missing modules or import errors
- Output file missing: Stage may have failed or not run
- Quality gate regressions after early stable runs: effective adaptive thresholds may have tightened; compare `run_summary.json` `quality_thresholds.effective` with baseline floors
- Repeated warning `Skipping cli runtime activity update ... Permission denied`: telemetry writes are non-fatal but live status fidelity degrades. Fix file ownership/permissions on `book_project/cli_runtime_activity.json` and its lock file.
- Repeated `datetime.utcnow()` warnings: non-fatal now, but should be migrated to timezone-aware UTC calls before Python runtime upgrades.
- DR audit reports `progress events found after terminal event`: run was sealed but stage execution continued; treat as high-severity consistency defect and inspect scheduler/reconcile behavior.

## 5.1 Research Bootstrap Validation
- Expected bootstrap result:
  - `source_packets.json` exists before `research_dossier.md` is generated
  - packet count is usually greater than 1
  - packets should include a mix of `wikipedia:*`, `dictionary:*`, optional `web:*`, and `book:premise_anchor`
- Quick inspection commands:
  ```sh
  RUN_DIR=/home/daravenrk/dragonlair/book_project/<book-slug>/runs/<run-name>
  jq '.query_terms, .wikipedia_queries, (.packets | length)' "$RUN_DIR/01_research/source_packets.json"
  jq -r '.packets[] | [.id, .source_type, (.facts | length)] | @tsv' "$RUN_DIR/01_research/source_packets.json"
  ```
- If packet count is low:
  - verify network access from the executing runtime
  - verify the runtime has optional scraping support if DDG snippets are expected
  - confirm chapter metadata includes usable `chapter_title` and `purpose` text for query generation

## 6. Next Steps
- Summarize findings and error messages
- Suggest targeted fixes (config, input, dependency, or code changes)
- Document the resolution for future reference

## 6.1 Immediate Incident Playbook (2026-03-22 class)
- Confirm terminal exception in CLI output and `run_journal.jsonl` tail.
- Verify whether failure occurred before or after `stage_complete canon`.
- If error is missing helper/function symbol:
  - patch `agent_stack/book_flow.py`
  - run `python3 -m py_compile agent_stack/book_flow.py`
  - relaunch the book flow and confirm progression into `writer_section_01` events.
- If CLI telemetry permissions are broken:
  - repair ownership/permissions for `book_project/cli_runtime_activity.json*`
  - re-run and verify warnings are gone.

---

_Keep this guide updated as the system evolves._
