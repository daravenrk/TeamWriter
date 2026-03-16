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

## 5. Common Issues
- Early failure (empty drafts, short log): Likely input, config, or environment error
- Agent quarantine: Check orchestrator health report and error details
- Dependency errors: See API server logs for missing modules or import errors
- Output file missing: Stage may have failed or not run

## 6. Next Steps
- Summarize findings and error messages
- Suggest targeted fixes (config, input, dependency, or code changes)
- Document the resolution for future reference

---

_Keep this guide updated as the system evolves._
