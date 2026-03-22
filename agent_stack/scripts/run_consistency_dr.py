#!/usr/bin/env python3
"""Run consistency DR validator.

Scans historical book-flow run directories and validates that each run follows
expected terminal semantics and journal structure.

Primary goal:
- Catch inconsistent runs where terminal events are missing, duplicated, or
  followed by further progress events.

Usage:
  python3 -m agent_stack.scripts.run_consistency_dr
  python3 -m agent_stack.scripts.run_consistency_dr --book-root /path/to/book_project --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

TERMINAL_EVENTS = {"run_success", "run_failure", "forced_completion"}
PROGRESS_EVENTS = {
    "stage_instantiated",
    "stage_attempt_start",
    "stage_attempt_result",
    "stage_complete",
    "stage_recovery_start",
    "stage_recovery_result",
}


@dataclass
class RunCheckResult:
    run_dir: str
    in_history: bool
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    terminal_event: Optional[str] = None
    terminal_index: Optional[int] = None
    event_count: int = 0

    @property
    def ok(self) -> bool:
        return len(self.issues) == 0


def _read_jsonl(path: Path) -> List[dict]:
    rows: List[dict] = []
    if not path.exists():
        return rows
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
            if isinstance(row, dict):
                rows.append(row)
            else:
                rows.append({"event": "_non_object_row", "line": idx})
        except Exception as exc:  # noqa: BLE001
            rows.append({"event": "_json_parse_error", "line": idx, "error": str(exc)})
    return rows


def _iter_run_dirs(book_root: Path, include_history: bool = True) -> Iterable[tuple[Path, bool]]:
    if not book_root.exists():
        return
    for slug_dir in sorted([p for p in book_root.iterdir() if p.is_dir()]):
        runs_root = slug_dir / "runs"
        if runs_root.exists():
            for run_dir in sorted([p for p in runs_root.iterdir() if p.is_dir()]):
                yield run_dir, False
        if include_history:
            hist_root = slug_dir / "run_history"
            if hist_root.exists():
                for run_dir in sorted([p for p in hist_root.iterdir() if p.is_dir()]):
                    yield run_dir, True


def _validate_single_run(run_dir: Path, in_history: bool) -> RunCheckResult:
    result = RunCheckResult(run_dir=str(run_dir), in_history=in_history)
    journal_path = run_dir / "run_journal.jsonl"

    rows = _read_jsonl(journal_path)
    result.event_count = len(rows)

    if not journal_path.exists():
        result.issues.append("missing run_journal.jsonl")
        return result

    if not rows:
        result.issues.append("empty run_journal.jsonl")
        return result

    events: List[str] = [str((row or {}).get("event") or "") for row in rows]

    if "_json_parse_error" in events:
        result.issues.append("journal contains JSON parse errors")

    if "run_start" not in events:
        result.issues.append("missing run_start event")

    terminal_positions = [i for i, ev in enumerate(events) if ev in TERMINAL_EVENTS]
    if not terminal_positions:
        if in_history:
            result.issues.append("missing terminal event in run_history entry")
        else:
            result.warnings.append("no terminal event yet (possibly active run)")
    else:
        first_terminal_idx = terminal_positions[0]
        result.terminal_index = first_terminal_idx
        result.terminal_event = events[first_terminal_idx]

        if len(terminal_positions) > 1:
            result.issues.append(f"multiple terminal events: {len(terminal_positions)}")

        # DR critical invariant: no progress event should occur after terminal seal.
        tail = events[first_terminal_idx + 1 :]
        leaked_progress = [ev for ev in tail if ev in PROGRESS_EVENTS]
        if leaked_progress:
            uniq = sorted(set(leaked_progress))
            result.issues.append(
                "progress events found after terminal event: " + ", ".join(uniq)
            )

    # Lightweight stage attempt sanity: each result should have a preceding start key.
    seen_attempts = set()
    for row in rows:
        ev = str((row or {}).get("event") or "")
        details = (row or {}).get("details") if isinstance((row or {}).get("details"), dict) else {}
        stage = str((details or {}).get("stage") or "").strip()
        attempt = (details or {}).get("attempt")
        key = (stage, attempt)
        if ev == "stage_attempt_start" and stage and attempt is not None:
            seen_attempts.add(key)
        if ev == "stage_attempt_result" and stage and attempt is not None and key not in seen_attempts:
            result.warnings.append(
                f"stage_attempt_result without prior start: stage={stage} attempt={attempt}"
            )

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run consistency DR validator")
    parser.add_argument(
        "--book-root",
        default="/home/daravenrk/dragonlair/book_project",
        help="Book project root containing per-book runs/run_history",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="Skip run_history directories and validate only active runs directories",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="Limit number of runs checked (0 = all, newest-first)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    return parser.parse_args()


def _sort_newest_first(items: List[tuple[Path, bool]]) -> List[tuple[Path, bool]]:
    return sorted(
        items,
        key=lambda pair: pair[0].stat().st_mtime if pair[0].exists() else 0.0,
        reverse=True,
    )


def main() -> int:
    args = parse_args()
    root = Path(args.book_root).expanduser()

    runs = list(_iter_run_dirs(root, include_history=not args.no_history))
    runs = _sort_newest_first(runs)
    if args.max_runs > 0:
        runs = runs[: args.max_runs]

    checked: List[RunCheckResult] = []
    for run_dir, in_history in runs:
        checked.append(_validate_single_run(run_dir, in_history))

    issues_total = sum(len(item.issues) for item in checked)
    warnings_total = sum(len(item.warnings) for item in checked)
    failing_runs = [item for item in checked if not item.ok]

    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "book_root": str(root),
        "run_count": len(checked),
        "failing_run_count": len(failing_runs),
        "issues_total": issues_total,
        "warnings_total": warnings_total,
        "status": "pass" if len(failing_runs) == 0 else "fail",
        "runs": [
            {
                "run_dir": item.run_dir,
                "in_history": item.in_history,
                "ok": item.ok,
                "terminal_event": item.terminal_event,
                "terminal_index": item.terminal_index,
                "event_count": item.event_count,
                "issues": item.issues,
                "warnings": item.warnings,
            }
            for item in checked
        ],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("=== Run Consistency DR ===")
        print(f"book_root: {payload['book_root']}")
        print(f"runs_checked: {payload['run_count']}")
        print(f"failing_runs: {payload['failing_run_count']}")
        print(f"issues_total: {payload['issues_total']}")
        print(f"warnings_total: {payload['warnings_total']}")
        if failing_runs:
            print("-- failing runs --")
            for item in failing_runs:
                print(f"* {item.run_dir}")
                for issue in item.issues:
                    print(f"  - ISSUE: {issue}")
                for warning in item.warnings:
                    print(f"  - WARN: {warning}")
        else:
            print("All checked runs satisfy DR consistency invariants.")

    return 0 if len(failing_runs) == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
