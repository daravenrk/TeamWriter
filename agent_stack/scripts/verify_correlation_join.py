#!/usr/bin/env python3
"""Verify deterministic joins between stage attempts and ML shadow plan events.

Usage:
  PYTHONPATH=/home/daravenrk/dragonlair python3 -m agent_stack.scripts.verify_correlation_join \
    --run-journal /home/daravenrk/dragonlair/book_project/runs/<run>/run_journal.jsonl \
    --ml-events /home/daravenrk/dragonlair/book_project/ml_shadow_events.jsonl \
    --json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


def _load_jsonl(path: Path) -> List[dict]:
    rows: List[dict] = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _extract_stage_attempts(run_journal_rows: List[dict]) -> List[dict]:
    attempts: List[dict] = []
    for row in run_journal_rows:
        if str(row.get("event") or "") != "stage_attempt_start":
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        attempts.append(
            {
                "timestamp": row.get("timestamp"),
                "stage": details.get("stage"),
                "attempt": details.get("attempt"),
                "profile": details.get("profile"),
                "route": details.get("route"),
                "model": details.get("model"),
                "correlation_id": str(details.get("correlation_id") or "").strip(),
            }
        )
    return attempts


def _extract_ml_plan_events(ml_rows: List[dict]) -> Dict[str, dict]:
    by_correlation: Dict[str, dict] = {}
    for row in ml_rows:
        if str(row.get("event") or "") != "plan":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        correlation_id = str(payload.get("correlation_id") or "").strip()
        if not correlation_id:
            continue
        by_correlation[correlation_id] = {
            "timestamp": row.get("timestamp"),
            "profile": payload.get("profile"),
            "selected_route": payload.get("selected_route"),
            "selected_model": payload.get("selected_model"),
            "ml_shadow": payload.get("ml_shadow"),
        }
    return by_correlation


def build_report(run_journal: Path, ml_events: Path) -> dict:
    journal_rows = _load_jsonl(run_journal)
    ml_rows = _load_jsonl(ml_events)

    attempts = _extract_stage_attempts(journal_rows)
    ml_by_corr = _extract_ml_plan_events(ml_rows)

    missing_correlation = 0
    missing_ml_match = 0
    joined = 0
    missing_examples: List[dict] = []

    for attempt in attempts:
        correlation_id = attempt.get("correlation_id") or ""
        if not correlation_id:
            missing_correlation += 1
            if len(missing_examples) < 10:
                missing_examples.append({
                    "reason": "missing_correlation_id",
                    "stage": attempt.get("stage"),
                    "attempt": attempt.get("attempt"),
                })
            continue

        if correlation_id not in ml_by_corr:
            missing_ml_match += 1
            if len(missing_examples) < 10:
                missing_examples.append({
                    "reason": "missing_ml_plan_event",
                    "correlation_id": correlation_id,
                    "stage": attempt.get("stage"),
                    "attempt": attempt.get("attempt"),
                })
            continue

        joined += 1

    total = len(attempts)
    join_rate = (joined / total) if total else 0.0
    valid = (missing_correlation == 0 and missing_ml_match == 0)

    return {
        "valid": valid,
        "run_journal": str(run_journal),
        "ml_events": str(ml_events),
        "total_stage_attempts": total,
        "joined": joined,
        "join_rate": round(join_rate, 4),
        "missing_correlation_id": missing_correlation,
        "missing_ml_match": missing_ml_match,
        "missing_examples": missing_examples,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify correlation joins between run_journal and ml shadow events")
    parser.add_argument("--run-journal", required=True, help="Path to run_journal.jsonl")
    parser.add_argument(
        "--ml-events",
        default="/home/daravenrk/dragonlair/book_project/ml_shadow_events.jsonl",
        help="Path to ml_shadow_events.jsonl",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(Path(args.run_journal), Path(args.ml_events))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("Correlation Join Report")
        print(f"- run_journal: {report['run_journal']}")
        print(f"- ml_events: {report['ml_events']}")
        print(f"- total_stage_attempts: {report['total_stage_attempts']}")
        print(f"- joined: {report['joined']}")
        print(f"- join_rate: {report['join_rate']}")
        print(f"- missing_correlation_id: {report['missing_correlation_id']}")
        print(f"- missing_ml_match: {report['missing_ml_match']}")
        if report["missing_examples"]:
            print("- missing_examples:")
            for item in report["missing_examples"]:
                print(f"  - {item}")

    return 0 if report.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
