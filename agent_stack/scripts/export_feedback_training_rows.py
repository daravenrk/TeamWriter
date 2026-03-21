#!/usr/bin/env python3
"""Export stage-level ML training rows by joining feedback + run journal + shadow events.

This script builds deterministic training rows keyed by correlation IDs when present,
with section/stage fallback linkage when correlation IDs are unavailable in feedback.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA_VERSION = "feedback-training-v1"


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


def _discover_run_journals(runs_root: Path) -> List[Path]:
    if not runs_root.exists():
        return []
    journals = sorted(runs_root.glob("*/run_journal.jsonl"))
    return [p for p in journals if p.is_file()]


def _extract_stage_attempts(run_journal_path: Path) -> List[dict]:
    rows = _load_jsonl(run_journal_path)
    out: List[dict] = []
    run_id = run_journal_path.parent.name
    for row in rows:
        if str(row.get("event") or "") != "stage_attempt_start":
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        out.append(
            {
                "run_id": run_id,
                "run_dir": str(run_journal_path.parent),
                "timestamp": row.get("timestamp"),
                "stage": details.get("stage"),
                "attempt": details.get("attempt"),
                "profile": details.get("profile"),
                "route": details.get("route"),
                "model": details.get("model"),
                "correlation_id": str(details.get("correlation_id") or "").strip(),
            }
        )
    return out


def _index_stage_attempts(journals: List[Path]):
    by_correlation: Dict[str, dict] = {}
    by_run_section_stage: Dict[str, dict] = {}
    total_attempts = 0

    for journal in journals:
        attempts = _extract_stage_attempts(journal)
        for attempt in attempts:
            total_attempts += 1
            cid = str(attempt.get("correlation_id") or "").strip()
            if cid:
                by_correlation[cid] = attempt

            run_id = str(attempt.get("run_id") or "")
            stage = str(attempt.get("stage") or "")
            # section index can often be inferred from stage naming like section_review_02
            section_index = None
            if stage.startswith("section_review_") or stage.startswith("writer_section_"):
                suffix = stage.rsplit("_", 1)[-1]
                if suffix.isdigit():
                    section_index = int(suffix)
            key = f"{run_id}|{section_index}|{stage}"
            by_run_section_stage[key] = attempt

    return {
        "by_correlation": by_correlation,
        "by_run_section_stage": by_run_section_stage,
        "total_attempts": total_attempts,
    }


def _index_ml_plans(ml_events_path: Path) -> Dict[str, dict]:
    rows = _load_jsonl(ml_events_path)
    out: Dict[str, dict] = {}
    for row in rows:
        if str(row.get("event") or "") != "plan":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        cid = str(payload.get("correlation_id") or "").strip()
        if not cid:
            continue
        out[cid] = {
            "timestamp": row.get("timestamp"),
            "profile": payload.get("profile"),
            "selected_route": payload.get("selected_route"),
            "selected_model": payload.get("selected_model"),
            "ml_shadow": payload.get("ml_shadow"),
        }
    return out


def _build_feedback_training_row(
    feedback: dict,
    attempt: Optional[dict],
    plan: Optional[dict],
) -> dict:
    selected_range = feedback.get("selected_text_range") if isinstance(feedback.get("selected_text_range"), dict) else None
    return {
        "schema_version": SCHEMA_VERSION,
        "feedback_id": feedback.get("feedback_id"),
        "run_id": feedback.get("run_id"),
        "run_dir": feedback.get("run_dir"),
        "task_id": feedback.get("task_id"),
        "chapter_number": feedback.get("chapter_number"),
        "section_index": feedback.get("section_index"),
        "section_title": feedback.get("section_title"),
        "stage_id": feedback.get("stage_id"),
        "feedback_timestamp": feedback.get("timestamp"),
        "reviewer": feedback.get("reviewer"),
        "approved": bool(feedback.get("approved")),
        "needs_rewrite": bool(feedback.get("needs_rewrite")),
        "score": float(feedback.get("score") or 0.0),
        "comment": feedback.get("comment"),
        "selected_text_range": selected_range,
        "attempt": attempt,
        "plan": plan,
        "has_attempt_join": bool(attempt),
        "has_plan_join": bool(plan),
    }


def build_export(
    feedback_path: Path,
    runs_root: Path,
    ml_events_path: Path,
) -> dict:
    feedback_rows = _load_jsonl(feedback_path)
    journals = _discover_run_journals(runs_root)
    attempts_index = _index_stage_attempts(journals)
    ml_plans_by_correlation = _index_ml_plans(ml_events_path)

    rows: List[dict] = []
    missing_attempt_join = []
    missing_plan_join = []

    for fb in feedback_rows:
        if not isinstance(fb, dict):
            continue

        run_id = str(fb.get("run_id") or "")
        section_index = fb.get("section_index")
        stage_id = str(fb.get("stage_id") or "")

        attempt = None
        plan = None

        # First attempt join: exact by stage tuple key if stage is provided.
        if run_id and section_index is not None and stage_id:
            key = f"{run_id}|{int(section_index)}|{stage_id}"
            attempt = attempts_index["by_run_section_stage"].get(key)

        # Fallback join: if stage is omitted, use a likely section stage.
        if attempt is None and run_id and section_index is not None:
            for stage_guess in [f"section_review_{int(section_index):02d}", f"writer_section_{int(section_index):02d}"]:
                key = f"{run_id}|{int(section_index)}|{stage_guess}"
                if key in attempts_index["by_run_section_stage"]:
                    attempt = attempts_index["by_run_section_stage"][key]
                    break

        if attempt is None and len(missing_attempt_join) < 25:
            missing_attempt_join.append(
                {
                    "feedback_id": fb.get("feedback_id"),
                    "run_id": run_id,
                    "section_index": section_index,
                    "stage_id": stage_id or None,
                }
            )

        correlation_id = str((attempt or {}).get("correlation_id") or "").strip()
        if correlation_id:
            plan = ml_plans_by_correlation.get(correlation_id)

        if attempt is not None and plan is None and len(missing_plan_join) < 25:
            missing_plan_join.append(
                {
                    "feedback_id": fb.get("feedback_id"),
                    "correlation_id": correlation_id or None,
                    "run_id": run_id,
                    "stage": (attempt or {}).get("stage"),
                }
            )

        rows.append(_build_feedback_training_row(fb, attempt, plan))

    total = len(rows)
    attempt_joined = sum(1 for row in rows if row.get("has_attempt_join"))
    plan_joined = sum(1 for row in rows if row.get("has_plan_join"))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": __import__("time").time(),
        "sources": {
            "feedback": str(feedback_path),
            "runs_root": str(runs_root),
            "ml_events": str(ml_events_path),
            "run_journals_discovered": len(journals),
            "stage_attempts_discovered": attempts_index["total_attempts"],
        },
        "stats": {
            "rows": total,
            "attempt_joined": attempt_joined,
            "plan_joined": plan_joined,
            "attempt_join_rate": round((attempt_joined / total), 4) if total else 0.0,
            "plan_join_rate": round((plan_joined / total), 4) if total else 0.0,
            "missing_attempt_join": total - attempt_joined,
            "missing_plan_join": total - plan_joined,
        },
        "missing_attempt_join_examples": missing_attempt_join,
        "missing_plan_join_examples": missing_plan_join,
        "rows": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export ML training rows from feedback + stage attempts + shadow plans")
    parser.add_argument(
        "--feedback",
        default="/home/daravenrk/dragonlair/book_project/book_feedback_events.jsonl",
        help="Path to feedback events JSONL",
    )
    parser.add_argument(
        "--runs-root",
        default="/home/daravenrk/dragonlair/book_project/runs",
        help="Path to book runs root",
    )
    parser.add_argument(
        "--ml-events",
        default="/home/daravenrk/dragonlair/book_project/ml_shadow_events.jsonl",
        help="Path to ml shadow events JSONL",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output JSON path for exported training rows",
    )
    parser.add_argument("--summary-only", action="store_true", help="Write only metadata + stats, no rows")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_export(Path(args.feedback), Path(args.runs_root), Path(args.ml_events))
    if args.summary_only:
        payload = {
            "schema_version": payload.get("schema_version"),
            "generated_at": payload.get("generated_at"),
            "sources": payload.get("sources"),
            "stats": payload.get("stats"),
            "missing_attempt_join_examples": payload.get("missing_attempt_join_examples"),
            "missing_plan_join_examples": payload.get("missing_plan_join_examples"),
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    stats = payload.get("stats") if isinstance(payload, dict) else {}
    print(json.dumps({
        "ok": True,
        "out": str(out_path),
        "rows": (stats or {}).get("rows"),
        "attempt_join_rate": (stats or {}).get("attempt_join_rate"),
        "plan_join_rate": (stats or {}).get("plan_join_rate"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
