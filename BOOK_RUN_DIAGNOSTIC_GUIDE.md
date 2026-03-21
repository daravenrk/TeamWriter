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

## 5. Common Issues
- Early failure (empty drafts, short log): Likely input, config, or environment error
- Agent quarantine: Check orchestrator health report and error details
- Dependency errors: See API server logs for missing modules or import errors
- Output file missing: Stage may have failed or not run
- Quality gate regressions after early stable runs: effective adaptive thresholds may have tightened; compare `run_summary.json` `quality_thresholds.effective` with baseline floors

## 6. Next Steps
- Summarize findings and error messages
- Suggest targeted fixes (config, input, dependency, or code changes)
- Document the resolution for future reference

---

_Keep this guide updated as the system evolves._
