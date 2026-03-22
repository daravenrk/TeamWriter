# TODO: Agent Work and Response Logging
# - Add detailed logging for agent work, responses, and lifecycle events
# - Log all major stage transitions, returns, and errors
import argparse
import copy
import difflib
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .lock_manager import AgentLockManager
from .exceptions import AgentStackError, AgentUnexpectedError, BookExportError, ChapterSpecValidationError, FrameworkIntegrityError, StageQualityGateError
from .orchestrator import OrchestratorAgent
from .output_schemas import validate_stage_payload
from .writing_assistant import generate_names, generate_technology, generate_personalities, generate_dates_history
from .living_skeleton import run_living_skeleton_update, load_law_context, get_future_frame


RUBRIC_KEYS = [
    "concept_validation",
    "structure_validation",
    "chapter_coherence",
    "sentence_clarity",
    "grammar_correction",
    "continuity_tracking",
    "fact_verification",
    "tone_consistency",
    "genre_compliance",
    "reader_engagement_score",
]

DEFAULT_STRATEGY_VERSION = str(
    os.environ.get("AGENT_STRATEGY_VERSION", "2026-03-20-strategy-v1")
).strip() or "2026-03-20-strategy-v1"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return float(default)


BOOK_QUALITY_MIN_SCORE = _env_float("BOOK_QUALITY_MIN_SCORE", 3.0)
BOOK_QUALITY_MIN_AVG_SCORE = _env_float("BOOK_QUALITY_MIN_AVG_SCORE", BOOK_QUALITY_MIN_SCORE)
BOOK_QUALITY_MIN_CONTENT_SCORE = _env_float("BOOK_QUALITY_MIN_CONTENT_SCORE", BOOK_QUALITY_MIN_SCORE)

BOOK_QUALITY_ADAPTIVE_ENABLED = str(
    os.environ.get("BOOK_QUALITY_ADAPTIVE_ENABLED", "true")
).lower() in {"1", "true", "yes", "on"}
BOOK_QUALITY_ADAPTIVE_ALPHA = _env_float("BOOK_QUALITY_ADAPTIVE_ALPHA", 0.2)
BOOK_QUALITY_ADAPTIVE_GAIN = _env_float("BOOK_QUALITY_ADAPTIVE_GAIN", 0.5)
BOOK_QUALITY_ADAPTIVE_WARMUP_RUNS = max(int(_env_float("BOOK_QUALITY_ADAPTIVE_WARMUP_RUNS", 3)), 0)
BOOK_QUALITY_ADAPTIVE_MAX_SCORE = _env_float("BOOK_QUALITY_ADAPTIVE_MAX_SCORE", 4.5)
BOOK_QUALITY_ADAPTIVE_MAX_CONTENT_SCORE = _env_float("BOOK_QUALITY_ADAPTIVE_MAX_CONTENT_SCORE", 4.0)

# Effective thresholds are mutable so each book can learn and tighten over time.
BOOK_QUALITY_EFFECTIVE_MIN_SCORE = BOOK_QUALITY_MIN_SCORE
BOOK_QUALITY_EFFECTIVE_MIN_AVG_SCORE = BOOK_QUALITY_MIN_AVG_SCORE
BOOK_QUALITY_EFFECTIVE_MIN_CONTENT_SCORE = BOOK_QUALITY_MIN_CONTENT_SCORE


def _quality_learning_state_path(book_root: Path) -> Path:
    return book_root / "quality_learning_state.json"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _compute_effective_quality_thresholds(state: dict) -> dict:
    runs_observed = int(state.get("runs_observed") or 0)
    ema_avg = _safe_float(state.get("ema_rubric_avg"), BOOK_QUALITY_MIN_AVG_SCORE)
    ema_content = _safe_float(state.get("ema_content_score"), BOOK_QUALITY_MIN_CONTENT_SCORE)

    min_score = float(BOOK_QUALITY_MIN_SCORE)
    min_avg = float(BOOK_QUALITY_MIN_AVG_SCORE)
    min_content = float(BOOK_QUALITY_MIN_CONTENT_SCORE)

    if not BOOK_QUALITY_ADAPTIVE_ENABLED or runs_observed < BOOK_QUALITY_ADAPTIVE_WARMUP_RUNS:
        return {
            "min_score": min_score,
            "min_avg_score": min_avg,
            "min_content_score": min_content,
            "adaptive_enabled": BOOK_QUALITY_ADAPTIVE_ENABLED,
            "runs_observed": runs_observed,
            "warmup_runs": BOOK_QUALITY_ADAPTIVE_WARMUP_RUNS,
            "quality_signal": min(ema_avg, ema_content),
        }

    quality_signal = min(ema_avg, ema_content)
    uplift = max(0.0, (quality_signal - min_avg) * max(0.0, BOOK_QUALITY_ADAPTIVE_GAIN))

    effective_min = min(BOOK_QUALITY_ADAPTIVE_MAX_SCORE, min_score + uplift)
    effective_avg = min(BOOK_QUALITY_ADAPTIVE_MAX_SCORE, min_avg + uplift)
    effective_content = min(BOOK_QUALITY_ADAPTIVE_MAX_CONTENT_SCORE, min_content + uplift)

    return {
        "min_score": round(effective_min, 3),
        "min_avg_score": round(effective_avg, 3),
        "min_content_score": round(effective_content, 3),
        "adaptive_enabled": BOOK_QUALITY_ADAPTIVE_ENABLED,
        "runs_observed": runs_observed,
        "warmup_runs": BOOK_QUALITY_ADAPTIVE_WARMUP_RUNS,
        "quality_signal": round(quality_signal, 3),
        "uplift": round(uplift, 3),
    }


def configure_effective_quality_thresholds(book_root: Path) -> dict:
    global BOOK_QUALITY_EFFECTIVE_MIN_SCORE
    global BOOK_QUALITY_EFFECTIVE_MIN_AVG_SCORE
    global BOOK_QUALITY_EFFECTIVE_MIN_CONTENT_SCORE

    state = read_json(_quality_learning_state_path(book_root), default={})
    effective = _compute_effective_quality_thresholds(state if isinstance(state, dict) else {})
    BOOK_QUALITY_EFFECTIVE_MIN_SCORE = _safe_float(effective.get("min_score"), BOOK_QUALITY_MIN_SCORE)
    BOOK_QUALITY_EFFECTIVE_MIN_AVG_SCORE = _safe_float(effective.get("min_avg_score"), BOOK_QUALITY_MIN_AVG_SCORE)
    BOOK_QUALITY_EFFECTIVE_MIN_CONTENT_SCORE = _safe_float(effective.get("min_content_score"), BOOK_QUALITY_MIN_CONTENT_SCORE)
    return {
        "state_path": str(_quality_learning_state_path(book_root)),
        "state": state if isinstance(state, dict) else {},
        "effective_thresholds": effective,
    }


def update_quality_learning_state(book_root: Path, rubric_report: dict, effective_snapshot: dict) -> dict:
    if not isinstance(rubric_report, dict):
        return {"updated": False, "reason": "invalid_rubric_report"}

    scores = rubric_report.get("scores") or {}
    if not isinstance(scores, dict) or not scores:
        return {"updated": False, "reason": "missing_scores"}

    numeric_values = [float(v) for v in scores.values() if isinstance(v, (int, float))]
    if not numeric_values:
        return {"updated": False, "reason": "scores_not_numeric"}

    avg_score = sum(numeric_values) / len(numeric_values)
    content_score = _safe_float(scores.get("reader_engagement_score"), avg_score)

    state_path = _quality_learning_state_path(book_root)
    state = read_json(state_path, default={})
    if not isinstance(state, dict):
        state = {}

    runs_observed = int(state.get("runs_observed") or 0) + 1
    prev_ema_avg = _safe_float(state.get("ema_rubric_avg"), avg_score)
    prev_ema_content = _safe_float(state.get("ema_content_score"), content_score)
    alpha = min(1.0, max(0.01, BOOK_QUALITY_ADAPTIVE_ALPHA))

    ema_avg = (alpha * avg_score) + ((1.0 - alpha) * prev_ema_avg)
    ema_content = (alpha * content_score) + ((1.0 - alpha) * prev_ema_content)

    updated_state = {
        "updated_at": datetime.utcnow().isoformat(),
        "runs_observed": runs_observed,
        "ema_rubric_avg": round(ema_avg, 3),
        "ema_content_score": round(ema_content, 3),
        "last_run": {
            "rubric_avg": round(avg_score, 3),
            "content_score": round(content_score, 3),
            "effective_thresholds_used": effective_snapshot,
        },
        "adaptive": {
            "enabled": BOOK_QUALITY_ADAPTIVE_ENABLED,
            "alpha": alpha,
            "gain": BOOK_QUALITY_ADAPTIVE_GAIN,
            "warmup_runs": BOOK_QUALITY_ADAPTIVE_WARMUP_RUNS,
            "max_score": BOOK_QUALITY_ADAPTIVE_MAX_SCORE,
            "max_content_score": BOOK_QUALITY_ADAPTIVE_MAX_CONTENT_SCORE,
        },
    }
    write_json(state_path, updated_state)

    return {
        "updated": True,
        "state_path": str(state_path),
        "runs_observed": runs_observed,
        "rubric_avg": round(avg_score, 3),
        "content_score": round(content_score, 3),
        "ema_rubric_avg": round(ema_avg, 3),
        "ema_content_score": round(ema_content, 3),
    }


def slugify(text: str) -> str:
    lowered = text.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = re.sub(r"-+", "-", lowered).strip("-")
    return lowered or "book-run"


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


@contextmanager
def file_lock(path: Path, timeout_seconds: float = 20.0, poll_seconds: float = 0.1):
    lock_path = Path(f"{path}.lock")
    ensure_dir(lock_path.parent)
    with open(lock_path, "w", encoding="utf-8") as handle:
        deadline = datetime.utcnow().timestamp() + timeout_seconds
        while True:
            try:
                flock(handle.fileno(), LOCK_EX | LOCK_NB)
                break
            except BlockingIOError:
                if datetime.utcnow().timestamp() >= deadline:
                    raise TimeoutError(f"Timed out acquiring file lock for {path}")
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            flock(handle.fileno(), LOCK_UN)


def write_text(path: Path, content: str):
    ensure_dir(path.parent)
    with file_lock(path):
        path.write_text(content, encoding="utf-8")


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    with file_lock(path):
        return path.read_text(encoding="utf-8")


def read_json(path: Path, default=None):
    if default is None:
        default = {}
    raw = read_text(path, "")
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload):
    write_text(path, json.dumps(payload, indent=2))


def payload_sha256(payload) -> str:
    """Deterministic sha256 for JSON-serializable payload provenance checks."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


RUN_ARCHIVE_ARTIFACT_SPECS = [
    ("run_journal.jsonl", "run_journal.jsonl"),
    ("run_summary.json", "run_summary.json"),
    ("changes.log", "changes.log"),
    ("05_reviews/publisher_report.json", "05_reviews/publisher_report.json"),
    ("06_final/manuscript_v1.md", "06_final/manuscript_v1.md"),
    ("06_final/manuscript_v2.md", "06_final/manuscript_v2.md"),
    ("diagnostics/agent_diagnostics.jsonl", "diagnostics/agent_diagnostics.jsonl"),
    ("07_retro/retrospective.json", "07_retro/retrospective.json"),
    ("07_retro/retrospective.md", "07_retro/retrospective.md"),
    ("07_retro/quality_failures_review.json", "07_retro/quality_failures_review.json"),
    ("07_retro/quality_failures_review.md", "07_retro/quality_failures_review.md"),
]


def archive_run_directory(
    run_dir: Path,
    history_root: Path,
    cleanup_reason: str,
    extra_manifest: dict | None = None,
):
    ensure_dir(history_root)

    archive_dir = history_root / run_dir.name
    if archive_dir.exists():
        shutil.rmtree(archive_dir)
    ensure_dir(archive_dir)

    copied = []
    for relative_src, relative_dest in RUN_ARCHIVE_ARTIFACT_SPECS:
        src = run_dir / relative_src
        if not src.exists() or not src.is_file():
            continue
        dest = archive_dir / relative_dest
        ensure_dir(dest.parent)
        shutil.copy2(src, dest)
        copied.append(relative_dest)

    manifest = {
        "archived_at": datetime.utcnow().isoformat(),
        "source_run_dir": str(run_dir),
        "cleanup_reason": cleanup_reason,
        "archived_files": copied,
    }
    if extra_manifest:
        manifest["details"] = extra_manifest
    write_json(archive_dir / "archive_manifest.json", manifest)

    shutil.rmtree(run_dir)
    return {
        "run_name": run_dir.name,
        "archive_dir": str(archive_dir),
        "cleanup_reason": cleanup_reason,
        "archived_files": copied,
        "deleted_run_count": 1,
        "deleted_file_count": 0,
    }


def archive_and_prune_old_runs(runs_root: Path, history_root: Path):
    ensure_dir(runs_root)
    ensure_dir(history_root)

    summary = {
        "archived_run_count": 0,
        "deleted_run_count": 0,
        "deleted_file_count": 0,
        "skipped_entries": [],
        "archived_runs": [],
        "deleted_files": [],
    }

    for item in sorted(runs_root.iterdir()):
        if item.name.startswith("."):
            continue

        if item.is_dir():
            try:
                archived = archive_run_directory(
                    item,
                    history_root,
                    cleanup_reason="pre_run_cleanup",
                )
                summary["archived_run_count"] += 1
                summary["deleted_run_count"] += archived.get("deleted_run_count", 0)
                summary["archived_runs"].append(archived)
            except PermissionError as err:
                summary["skipped_entries"].append(
                    {
                        "path": str(item),
                        "reason": f"permission_error: {err}",
                    }
                )
            continue

        if item.is_file():
            try:
                item.unlink()
                summary["deleted_file_count"] += 1
                summary["deleted_files"].append(str(item))
            except PermissionError as err:
                summary["skipped_entries"].append(
                    {
                        "path": str(item),
                        "reason": f"permission_error: {err}",
                    }
                )

    return summary


def update_cli_runtime_activity(path: Path, run_id: str, patch: dict, clear: bool = False):
    try:
        ensure_dir(path.parent)
        with file_lock(path):
            payload = {}
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    payload = {}
            runs = payload.get("active_runs") if isinstance(payload, dict) else None
            if not isinstance(runs, list):
                runs = []
            existing = None
            retained = []
            for item in runs:
                if not isinstance(item, dict):
                    continue
                if str(item.get("run_id") or "") == run_id:
                    existing = dict(item)
                    continue
                retained.append(item)
            if not clear:
                merged = existing or {"run_id": run_id}
                merged.update(patch)
                merged["run_id"] = run_id
                merged["updated_at_epoch"] = time.time()
                retained.append(merged)
            path.write_text(json.dumps({"active_runs": retained}, indent=2), encoding="utf-8")
    except (OSError, PermissionError, TimeoutError) as exc:
        print(f"[WARN] Skipping cli runtime activity update for {path}: {exc}")


def update_cli_runtime_activity_from_context(context_store: dict, patch: dict, clear: bool = False):
    run_id = str(context_store.get("_cli_run_id") or "").strip()
    path_raw = str(context_store.get("_cli_activity_path") or "").strip()
    if not run_id or not path_raw:
        return
    update_cli_runtime_activity(Path(path_raw), run_id, patch, clear=clear)

def append_analytics(path: Path, event: dict):
    ensure_dir(path.parent)
    with file_lock(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")


def append_jsonl(path: Path, payload):
    ensure_dir(path.parent)
    with file_lock(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


def append_run_event(path: Path, event: str, details: dict):
    if path is None:
        return
    append_jsonl(
        path,
        {
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "details": details,
        },
    )


BOOK_REVIEW_GATE_FILENAME = "review_gate_state.json"
BOOK_REVIEW_GATE_POLL_SECONDS = max(
    1.0,
    float(os.environ.get("BOOK_REVIEW_GATE_POLL_SECONDS", "2") or 2),
)
BOOK_REVIEW_MAX_REWRITE_CYCLES = max(
    1,
    int(os.environ.get("BOOK_REVIEW_MAX_REWRITE_CYCLES", "2") or 2),
)


def review_gate_state_path(run_dir: Path) -> Path:
    return run_dir / "handoff" / BOOK_REVIEW_GATE_FILENAME


def read_review_gate_state(run_dir: Path) -> dict:
    return read_json(review_gate_state_path(run_dir), default={})


def write_review_gate_state(run_dir: Path, payload: dict) -> dict:
    path = review_gate_state_path(run_dir)
    existing = read_json(path, default={})
    merged = existing if isinstance(existing, dict) else {}
    merged.update(payload or {})
    merged["updated_at_epoch"] = time.time()
    write_json(path, merged)
    return merged


def await_review_gate_decision(
    *,
    run_dir: Path,
    run_journal: Path,
    context_store: dict,
    section_index: int,
    section_title: str,
    stage_id: str,
    section_path: Path,
    section_review_path: Path,
):
    state = read_review_gate_state(run_dir)
    status = str(state.get("status") or "idle").strip().lower()
    target_section = state.get("section_index")
    if target_section not in {None, ""}:
        try:
            if int(target_section) != int(section_index):
                return {"action": "continue", "paused": False, "state": state}
        except (TypeError, ValueError):
            pass

    if status not in {"pause_requested", "paused", "deferred", "continue_requested", "rewrite_requested"}:
        return {"action": "continue", "paused": False, "state": state}

    if status in {"pause_requested", "continue_requested", "rewrite_requested"}:
        write_review_gate_state(
            run_dir,
            {
                "status": "paused" if status == "pause_requested" else status,
                "checkpoint_stage": stage_id,
                "checkpoint_section_index": section_index,
                "checkpoint_section_title": section_title,
                "section_path": str(section_path),
                "section_review_path": str(section_review_path),
                "paused_at_epoch": time.time(),
                "task_id": context_store.get("_task_id"),
            },
        )
        append_run_event(
            run_journal,
            "human_review_pause_activated",
            {
                "task_id": context_store.get("_task_id"),
                "stage": stage_id,
                "section_index": section_index,
                "section_title": section_title,
                "correlation_id": state.get("correlation_id"),
            },
        )
        update_cli_runtime_activity_from_context(
            context_store,
            {
                "state": "paused",
                "status_detail": f"paused for human review: {stage_id}",
                "stage": stage_id,
                "section_title": section_title,
                "updated_at_epoch": time.time(),
            },
        )

    defer_logged = False
    while True:
        state = read_review_gate_state(run_dir)
        status = str(state.get("status") or "paused").strip().lower()
        if status == "continue_requested":
            append_run_event(
                run_journal,
                "human_review_continue_acknowledged",
                {
                    "task_id": context_store.get("_task_id"),
                    "stage": stage_id,
                    "section_index": section_index,
                    "section_title": section_title,
                    "correlation_id": state.get("correlation_id"),
                },
            )
            write_review_gate_state(
                run_dir,
                {
                    "status": "idle",
                    "resume_action": "continue",
                    "resumed_at_epoch": time.time(),
                },
            )
            update_cli_runtime_activity_from_context(
                context_store,
                {
                    "state": "running",
                    "status_detail": f"resumed after human review: {stage_id}",
                    "updated_at_epoch": time.time(),
                },
            )
            return {"action": "continue", "paused": True, "state": state}
        if status == "rewrite_requested":
            append_run_event(
                run_journal,
                "human_review_rewrite_acknowledged",
                {
                    "task_id": context_store.get("_task_id"),
                    "stage": stage_id,
                    "section_index": section_index,
                    "section_title": section_title,
                    "correlation_id": state.get("correlation_id"),
                },
            )
            write_review_gate_state(
                run_dir,
                {
                    "status": "idle",
                    "resume_action": "rewrite",
                    "resumed_at_epoch": time.time(),
                },
            )
            update_cli_runtime_activity_from_context(
                context_store,
                {
                    "state": "running",
                    "status_detail": f"rewrite requested after review: {stage_id}",
                    "updated_at_epoch": time.time(),
                },
            )
            return {"action": "rewrite", "paused": True, "state": state}
        if status == "deferred":
            if not defer_logged:
                append_run_event(
                    run_journal,
                    "human_review_deferred",
                    {
                        "task_id": context_store.get("_task_id"),
                        "stage": stage_id,
                        "section_index": section_index,
                        "section_title": section_title,
                        "note": state.get("note"),
                        "correlation_id": state.get("correlation_id"),
                    },
                )
                defer_logged = True
            time.sleep(BOOK_REVIEW_GATE_POLL_SECONDS)
            continue
        time.sleep(BOOK_REVIEW_GATE_POLL_SECONDS)


def parse_json_block(raw: str, fallback=None):
    fallback = fallback if fallback is not None else {}
    text = (raw or "").strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return fallback
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return fallback


def build_contract(role: str, objective: str, constraints, inputs, output_format: str, failure_conditions) -> str:
    constraints_block = "\n".join(f"- {item}" for item in constraints)
    failures_block = "\n".join(f"- {item}" for item in failure_conditions)
    return (
        f"ROLE:\n{role}\n\n"
        f"OBJECTIVE:\n{objective}\n\n"
        f"CONSTRAINTS:\n{constraints_block}\n\n"
        f"INPUTS:\n{json.dumps(inputs, indent=2)}\n\n"
        f"OUTPUT FORMAT:\n{output_format}\n\n"
        f"FAILURE CONDITIONS:\n{failures_block}\n"
    ).strip()


def build_resource_reference_block(context_store: dict) -> str:
    refs = context_store.get("_resource_refs") if isinstance(context_store, dict) else None
    framework_refs = context_store.get("_framework_refs") if isinstance(context_store, dict) else None
    snapshot = context_store.get("_resource_snapshot") if isinstance(context_store, dict) else None
    if not refs and not framework_refs:
        return ""

    lines = [
        "RESOURCE REFERENCES:",
        "- These files are shared state for publisher + agents.",
        f"- tracker_json: {refs.get('resource_tracker', '')}",
        f"- events_jsonl: {refs.get('resource_events', '')}",
        f"- ui_state_json: {refs.get('ui_state', '')}",
        f"- ui_events_jsonl: {refs.get('ui_events', '')}",
        "- If you cite resource state, reference these paths explicitly in your output.",
    ]

    if isinstance(snapshot, dict) and snapshot:
        compact = {
            "mode": snapshot.get("mode"),
            "pressure_mode": snapshot.get("pressure_mode"),
            "queue": (snapshot.get("queue") or {}).get("status_counts"),
            "agents": ((snapshot.get("agents") or {}).get("summary") or {}),
        }
        lines.append("- Current snapshot:")
        lines.append(json.dumps(compact, indent=2))

    if isinstance(framework_refs, dict) and framework_refs:
        lines.extend(
            [
                "BOOK FRAMEWORK REFERENCES:",
                f"- framework_skeleton_json: {framework_refs.get('framework_skeleton', '')}",
                f"- arc_tracker_json: {framework_refs.get('arc_tracker', '')}",
                f"- progress_index_json: {framework_refs.get('progress_index', '')}",
                f"- agent_context_status_jsonl: {framework_refs.get('agent_context_status', '')}",
                "- Use these references to keep continuity, pacing, and unresolved arc state aligned.",
            ]
        )

    return "\n".join(lines)


def _normalize_list(value):
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def build_framework_skeleton(brief: dict, outline_payload: dict, chapter_spec: dict, chapter_number: int) -> dict:
    structure = outline_payload.get("book_structure") if isinstance(outline_payload, dict) else {}
    sections = chapter_spec.get("sections") if isinstance(chapter_spec, dict) else []
    section_entries = []
    for idx, item in enumerate(sections or [], start=1):
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or f"Section {idx}")
            goal = str(item.get("objective") or item.get("goal") or "")
        else:
            title = str(item or f"Section {idx}")
            goal = ""
        section_entries.append({"index": idx, "title": title, "goal": goal})

    return {
        "book_identity": {
            "title_working": brief.get("title_working"),
            "genre": brief.get("genre"),
            "audience": brief.get("audience"),
            "tone": brief.get("tone"),
            "target_word_count": brief.get("target_word_count"),
            "page_target": brief.get("page_target"),
        },
        "design_framework": {
            "constraints": _normalize_list(brief.get("constraints")),
            "acceptance_criteria": _normalize_list(brief.get("acceptance_criteria")),
            "book_structure": structure,
            "master_outline_markdown": str(outline_payload.get("master_outline_markdown") or ""),
        },
        "chapter_skeleton": {
            "chapter_number": chapter_number,
            "chapter_title": chapter_spec.get("chapter_title"),
            "purpose": chapter_spec.get("purpose"),
            "ending_hook": chapter_spec.get("ending_hook"),
            "target_words": chapter_spec.get("target_words"),
            "sections": section_entries,
            "must_include": _normalize_list(chapter_spec.get("must_include")),
            "must_avoid": _normalize_list(chapter_spec.get("must_avoid")),
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


def update_arc_tracker(existing: dict, *, chapter_number: int, chapter_title: str, section_title: str, next_writer_notes: dict, continuity_state: dict, canon_payload: dict, rubric_report: dict):
    tracker = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    tracker.setdefault("story_arcs", [])
    tracker.setdefault("character_arcs", [])
    tracker.setdefault("open_loops", [])
    tracker.setdefault("chapter_progress", [])

    # Merge open loops — never replace. Open loops are persistent story features
    # that must be tracked until explicitly resolved. Loops from the current
    # chapter's canon output are added to the set; loops already tracked from
    # prior chapters are preserved so they are never silently dropped.
    new_loops = _normalize_list(canon_payload.get("open_loops") if isinstance(canon_payload, dict) else [])
    existing_loops = _normalize_list(tracker.get("open_loops"))
    merged_loops_set: dict[str, str] = {str(l).lower(): str(l) for l in existing_loops}
    for loop in new_loops:
        merged_loops_set.setdefault(str(loop).lower(), str(loop))
    tracker["open_loops"] = list(merged_loops_set.values())

    notes = next_writer_notes if isinstance(next_writer_notes, dict) else {}
    state_updates = continuity_state.get("section_updates") if isinstance(continuity_state, dict) else []
    state_updates = state_updates if isinstance(state_updates, list) else []

    timeline_events = _normalize_list(notes.get("timeline_events"))
    unresolved_questions = _normalize_list(notes.get("unresolved_questions"))
    character_updates = _normalize_list(notes.get("character_state_updates"))

    if timeline_events:
        tracker["story_arcs"].append(
            {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "events": timeline_events,
            }
        )
    if character_updates:
        tracker["character_arcs"].append(
            {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "updates": character_updates,
            }
        )

    tracker["chapter_progress"].append(
        {
            "chapter_number": chapter_number,
            "chapter_title": chapter_title,
            "section_title": section_title,
            "continuity_updates_count": len(state_updates),
            "timeline_events": timeline_events,
            "unresolved_questions": unresolved_questions,
            "rubric_scores": (rubric_report or {}).get("scores") if isinstance(rubric_report, dict) else {},
            "updated_at": datetime.utcnow().isoformat(),
        }
    )
    tracker["last_updated"] = datetime.utcnow().isoformat()
    return tracker


# ---------------------------------------------------------------------------
# Framework integrity gate (Todo 35)
# ---------------------------------------------------------------------------
_FRAMEWORK_REQUIRED: dict[str, list[str]] = {
    "book_identity": ["title_working", "genre"],
    "design_framework": ["acceptance_criteria"],
    "chapter_skeleton": ["chapter_number", "chapter_title", "sections"],
}

_ARC_REQUIRED: list[str] = ["story_arcs", "character_arcs", "open_loops", "chapter_progress"]
_PROGRESS_REQUIRED: list[str] = ["book", "completed_chapters"]


def check_framework_integrity(
    skeleton: dict,
    arc_tracker: dict,
    progress_index: dict,
) -> None:
    """Raise FrameworkIntegrityError if any critical framework fields are absent or empty.

    Called after build_framework_skeleton() writes the skeleton so that the
    pipeline does not proceed into expensive writing stages with a broken
    design contract.
    """
    diagnostics: list[str] = []

    # Validate framework skeleton sections
    if not isinstance(skeleton, dict):
        raise FrameworkIntegrityError(
            "framework_skeleton is not a dict — cannot validate integrity.",
            details={"skeleton_type": type(skeleton).__name__},
        )

    for section, fields in _FRAMEWORK_REQUIRED.items():
        section_data = skeleton.get(section)
        if not isinstance(section_data, dict):
            diagnostics.append(f"framework_skeleton.{section}: missing or not a dict")
            continue
        for field in fields:
            value = section_data.get(field)
            if value is None or value == "" or value == [] or value == {}:
                diagnostics.append(f"framework_skeleton.{section}.{field}: empty or missing")

    # Validate arc tracker
    if not isinstance(arc_tracker, dict):
        diagnostics.append("arc_tracker: missing or not a dict")
    else:
        for field in _ARC_REQUIRED:
            if field not in arc_tracker:
                diagnostics.append(f"arc_tracker.{field}: missing key")

    # Validate progress index
    if not isinstance(progress_index, dict):
        diagnostics.append("progress_index: missing or not a dict")
    else:
        for field in _PROGRESS_REQUIRED:
            if field not in progress_index:
                diagnostics.append(f"progress_index.{field}: missing key")

    if diagnostics:
        raise FrameworkIntegrityError(
            f"Framework integrity gate failed with {len(diagnostics)} issue(s): "
            + "; ".join(diagnostics),
            details={"issues": diagnostics},
        )


# ---------------------------------------------------------------------------

def write_agent_context_status(path: Path, payload: dict):
    append_jsonl(
        path,
        {
            "timestamp": datetime.utcnow().isoformat(),
            "event": "agent_context_status",
            "details": payload,
        },
    )


def gate_chapter_spec(spec):
    sections = spec.get("sections") or []
    if not isinstance(sections, list) or len(sections) < 2:
        return False, "chapter spec requires at least 2 sections"
    return True, "ok"


def gate_developmental(report):
    if not isinstance(report, dict):
        return False, "invalid developmental report"
    scores = report.get("scores") or {}
    if not isinstance(scores, dict):
        return False, "scores missing"
    values = [v for v in scores.values() if isinstance(v, (int, float))]
    if not values:
        return False, "scores empty"
    if min(values) < BOOK_QUALITY_EFFECTIVE_MIN_SCORE:
        return False, f"one or more scores below {BOOK_QUALITY_EFFECTIVE_MIN_SCORE:g}"
    if (sum(values) / len(values)) < BOOK_QUALITY_EFFECTIVE_MIN_AVG_SCORE:
        return False, f"average score below {BOOK_QUALITY_EFFECTIVE_MIN_AVG_SCORE:g}"
    return True, "pass"


def gate_publisher(report):
    if not isinstance(report, dict):
        return False, "invalid publisher report"
    decision = str(report.get("decision", "")).upper()
    if decision == "APPROVE":
        return True, "approve"
    return False, "publisher requested revision"


def gate_publisher_brief(report):
    if not isinstance(report, dict):
        return False, "invalid brief payload"

    constraints = report.get("constraints")
    acceptance = report.get("acceptance_criteria")
    if not isinstance(constraints, list) or len(constraints) < 5:
        return False, "constraints must be a list with at least 5 items"
    if not isinstance(acceptance, list) or len(acceptance) < 5:
        return False, "acceptance_criteria must be a list with at least 5 items"

    return True, "ok"


def gate_research_dossier(text):
    content = str(text or "").strip()
    if not content:
        return False, "research output empty"

    lowered = content.lower()
    has_facts = any(
        marker in lowered
        for marker in (
            "facts",
            "findings",
            "key findings",
            "evidence",
            "observations",
        )
    )
    if has_facts:
        return True, "ok"

    # Fallback acceptance for substantial dossiers that use different heading names.
    if len(content.split()) >= 120:
        return True, "ok (lenient: substantive research without explicit facts heading)"

    return False, "research output missing facts section"


def build_fallback_research_dossier(brief: dict, chapter: dict, premise: str, source_packets: list[dict] | None = None) -> str:
    title = str((chapter or {}).get("title") or "Untitled Chapter")
    section = str((chapter or {}).get("section_title") or "Untitled Section")
    goal = str((chapter or {}).get("section_goal") or "")
    genre = str((brief or {}).get("genre") or "speculative fiction")
    audience = str((brief or {}).get("audience") or "adult")
    tone = str((brief or {}).get("tone") or "clear")
    constraints = _normalize_list((brief or {}).get("constraints"))
    acceptance = _normalize_list((brief or {}).get("acceptance_criteria"))

    constraint_lines = "\n".join(f"- {item}" for item in (constraints[:5] or ["Maintain internal consistency."]))
    acceptance_lines = "\n".join(f"- {item}" for item in (acceptance[:5] or ["Keep outputs stage-ready."]))
    packet_lines = []
    for packet in source_packets or []:
        packet_id = str(packet.get("id") or "source:unknown")
        facts = packet.get("facts") if isinstance(packet.get("facts"), list) else []
        first_fact = str(facts[0] or "").strip() if facts else ""
        if first_fact:
            packet_lines.append(f"- [{packet_id}] {first_fact}")
    packet_block = "\n".join(packet_lines) if packet_lines else "- No external packets available for this run."

    chapter_number = (chapter or {}).get("chapter_number") or (chapter or {}).get("number") or 1
    return (
        f"# Overview\n"
        f"This is an operator-generated fallback research dossier for chapter {chapter_number}: '{title}' / '{section}'. "
        f"It preserves pipeline continuity when the research agent returns empty output. "
        f"The narrative must remain in {genre} mode for an {audience} audience with a {tone} tone.\n\n"
        f"Section goal: {goal}\n\n"
        f"Premise anchor: {premise}\n\n"
        f"# Facts\n"
        f"- This is a {genre} story with a {tone} tone written for a {audience} audience.\n"
        f"- Chapter goal: {goal or 'Advance the narrative and develop characters as described in the chapter spec.'}\n"
        f"- The chapter must establish scene, characters, and conflict relevant to the chapter goal before moving to action.\n"
        f"- Maintain internal consistency with the book premise throughout the chapter.\n"
        f"- Operational constraints from the publishing brief remain binding and must drive scene-level decisions.\n"
        f"- Evidence anchors from bootstrap source packets:\n"
        f"{packet_block}\n\n"
        f"# Worldbuilding Notes\n"
        f"- Ground the scene in concrete, specific details that match the {genre} genre and {tone} tone.\n"
        f"- Avoid exposition dumps; reveal world details through character action and dialogue.\n"
        f"- Maintain consistency with any established character traits, setting details, or prior canon.\n"
        f"- Leave continuity hooks for downstream outline and canon stages to build upon.\n"
        f"- Brief constraints to enforce:\n"
        f"{constraint_lines}\n\n"
        f"# Do-Not-Claim-Without-Review\n"
        f"- Do not introduce irreversible canon changes without evidence established in the scene.\n"
        f"- Do not contradict explicit acceptance criteria from the publishing brief.\n"
        f"- Do not drift from the premise anchor or chapter goal without deliberate narrative cause.\n"
        f"- Required acceptance anchors for downstream stages:\n"
        f"{acceptance_lines}\n"
    )


def build_fallback_architect_outline(brief: dict, chapter: dict, research_md: str) -> dict:
    chapter_title = str((chapter or {}).get("title") or "Untitled Chapter")
    section_title = str((chapter or {}).get("section_title") or "Untitled Section")
    chapter_goal = str((chapter or {}).get("section_goal") or "Advance core narrative intent.")
    title_working = str((brief or {}).get("title_working") or "Working Title")
    genre = str((brief or {}).get("genre") or "speculative fiction")
    tone = str((brief or {}).get("tone") or "clear")
    research_excerpt = str(research_md or "").strip().splitlines()
    research_hint = research_excerpt[0] if research_excerpt else "Research dossier fallback applied"

    return {
        "master_outline_markdown": (
            f"# {title_working} Master Outline\n\n"
            f"## Chapter 1: {chapter_title}\n"
            f"- Section focus: {section_title}\n"
            f"- Goal: {chapter_goal}\n"
            f"- Genre/Tone: {genre} / {tone}\n"
            f"- Research anchor: {research_hint}\n"
        ),
        "book_structure": {
            "acts": [
                {
                    "name": "Act I",
                    "purpose": "Establish protagonist, context, and inciting anomaly.",
                    "chapters": [1],
                }
            ],
            "chapter_frames": [
                {
                    "chapter_number": int((chapter or {}).get("number") or 1),
                    "chapter_title": chapter_title,
                    "focus": section_title,
                    "goal": chapter_goal,
                }
            ],
        },
        "pacing_notes": "Fallback outline generated due to missing architect payload keys; proceed conservatively and refine in chapter planning.",
    }


def build_fallback_chapter_spec(brief: dict, chapter: dict, outline_payload: dict) -> dict:
    chapter_number = int((chapter or {}).get("number") or 1)
    chapter_title = str((chapter or {}).get("title") or f"Chapter {chapter_number}")
    section_title = str((chapter or {}).get("section_title") or "Core Section")
    section_goal = str((chapter or {}).get("section_goal") or "Advance the chapter with clear forward motion.")
    writer_words = int((chapter or {}).get("writer_words") or 700)
    genre = str((brief or {}).get("genre") or "speculative fiction")
    tone = str((brief or {}).get("tone") or "clear")
    structure = (outline_payload or {}).get("book_structure") if isinstance(outline_payload, dict) else {}
    chapter_frames = structure.get("chapter_frames") if isinstance(structure, dict) else []
    frame_goal = ""
    if isinstance(chapter_frames, list):
        for item in chapter_frames:
            if not isinstance(item, dict):
                continue
            if int(item.get("chapter_number") or 0) == chapter_number:
                frame_goal = str(item.get("goal") or "")
                break
    goal = frame_goal or section_goal

    return {
        "chapter_number": chapter_number,
        "chapter_title": chapter_title,
        "purpose": goal,
        "target_words": writer_words,
        "sections": [
            {
                "title": "Baseline System",
                "goal": f"Establish the operating context, constraints, and tone for {chapter_title}.",
            },
            {
                "title": section_title,
                "goal": goal,
            },
        ],
        "must_include": [
            f"Maintain {genre} tone with {tone} delivery.",
            "Show a clear escalation from observation to actionable concern.",
        ],
        "must_avoid": [
            "Do not resolve the anomaly too early.",
            "Do not contradict the fallback outline or publishing brief.",
        ],
        "ending_hook": "End with evidence strong enough that the protagonist must continue the investigation.",
    }


def build_fallback_canon_payload(brief: dict, chapter: dict, chapter_spec: dict, outline_payload: dict) -> dict:
    chapter_number = int((chapter or {}).get("number") or 1)
    chapter_title = str((chapter or {}).get("title") or f"Chapter {chapter_number}")
    section_title = str((chapter or {}).get("section_title") or "Core Section")
    section_goal = str((chapter or {}).get("section_goal") or "Advance core narrative intent.")

    ending_hook = str((chapter_spec or {}).get("ending_hook") or "")
    must_include = _normalize_list((chapter_spec or {}).get("must_include"))
    must_avoid = _normalize_list((chapter_spec or {}).get("must_avoid"))

    return {
        "canon": {
            "book_identity": {
                "title_working": str((brief or {}).get("title_working") or "Working Title"),
                "genre": str((brief or {}).get("genre") or "speculative fiction"),
                "audience": str((brief or {}).get("audience") or "adult"),
                "tone": str((brief or {}).get("tone") or "clear"),
            },
            "chapter_anchor": {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "section_title": section_title,
                "section_goal": section_goal,
            },
            "constraints": _normalize_list((brief or {}).get("constraints")),
            "acceptance_criteria": _normalize_list((brief or {}).get("acceptance_criteria")),
            "must_include": must_include,
            "must_avoid": must_avoid,
            "fallback": {
                "active": True,
                "reason": "canon_generation_failed_after_retries_recovery_and_failover",
            },
        },
        "timeline": {
            "chapter_events": [
                {
                    "chapter_number": chapter_number,
                    "chapter_title": chapter_title,
                    "section_title": section_title,
                    "event": "Canon bootstrap fallback initialized.",
                }
            ],
            "source": "deterministic_canon_fallback",
        },
        "character_bible": {
            "characters": [],
            "notes": [
                "Character details pending downstream continuity/editorial refinement.",
            ],
            "source": "deterministic_canon_fallback",
        },
        "open_loops": [
            item for item in [ending_hook, section_goal] if str(item).strip()
        ],
        "style_guide": (
            "# Style Guide (Fallback Canon)\n\n"
            "- Preserve continuity with chapter goal and brief constraints.\n"
            "- Prefer conservative canon updates until full canon regeneration succeeds.\n"
            "- Treat this artifact as bootstrap canon and refine in the next successful canon stage.\n"
        ),
        "fallback_meta": {
            "active": True,
            "type": "deterministic_canon_bootstrap",
            "generated_at": datetime.utcnow().isoformat(),
            "chapter_number": chapter_number,
            "chapter_title": chapter_title,
            "outline_present": bool((outline_payload or {}).get("book_structure")),
        },
    }


def validate_fallback_canon_contract(canon_payload: dict, brief: dict, chapter: dict, chapter_spec: dict) -> dict:
    """Validate deterministic canon fallback carries minimum semantic anchors."""
    payload = canon_payload if isinstance(canon_payload, dict) else {}
    canon = payload.get("canon") if isinstance(payload.get("canon"), dict) else {}
    chapter_anchor = canon.get("chapter_anchor") if isinstance(canon.get("chapter_anchor"), dict) else {}
    book_identity = canon.get("book_identity") if isinstance(canon.get("book_identity"), dict) else {}

    expected_chapter_number = int((chapter or {}).get("number") or 1)
    expected_ending_hook = str((chapter_spec or {}).get("ending_hook") or "").strip()

    checks = [
        {
            "name": "chapter_number_anchor",
            "passed": int(chapter_anchor.get("chapter_number") or 0) == expected_chapter_number,
            "detail": "canon.chapter_anchor.chapter_number matches requested chapter",
        },
        {
            "name": "chapter_title_anchor",
            "passed": bool(str(chapter_anchor.get("chapter_title") or "").strip()),
            "detail": "canon.chapter_anchor.chapter_title is present",
        },
        {
            "name": "section_goal_anchor",
            "passed": bool(str(chapter_anchor.get("section_goal") or "").strip()),
            "detail": "canon.chapter_anchor.section_goal is present",
        },
        {
            "name": "constraints_anchor",
            "passed": bool(_normalize_list(canon.get("constraints"))),
            "detail": "canon.constraints contains at least one constraint",
        },
        {
            "name": "book_identity_anchor",
            "passed": bool(str(book_identity.get("title_working") or "").strip()),
            "detail": "canon.book_identity.title_working is present",
        },
        {
            "name": "style_guide_anchor",
            "passed": bool(str(payload.get("style_guide") or "").strip()),
            "detail": "style_guide is present",
        },
        {
            "name": "open_loops_anchor",
            "passed": bool(_normalize_list(payload.get("open_loops"))),
            "detail": "open_loops contains at least one carry-forward item",
        },
        {
            "name": "ending_hook_anchor",
            "passed": (not expected_ending_hook) or (expected_ending_hook in _normalize_list(payload.get("open_loops"))),
            "detail": "chapter_spec ending_hook is preserved in open_loops when provided",
        },
        {
            "name": "fallback_meta_anchor",
            "passed": bool(((payload.get("fallback_meta") or {}).get("active") is True)),
            "detail": "fallback_meta.active is true",
        },
    ]

    missing = [item["name"] for item in checks if not item["passed"]]
    return {
        "contract": "canon_fallback_parity_v1",
        "generated_at": datetime.utcnow().isoformat(),
        "all_passed": not missing,
        "missing": missing,
        "checks": checks,
        "expected": {
            "chapter_number": expected_chapter_number,
            "ending_hook": expected_ending_hook,
            "brief_title": str((brief or {}).get("title_working") or ""),
        },
    }


def gate_architect_outline(payload):
    if not isinstance(payload, dict):
        return False, "invalid architect payload"

    # Normalize common alias keys so valid responses are not rejected for naming variance.
    alias_keys = [
        "master_outline_markdown",
        "master_outline",
        "outline_markdown",
        "master_outline_md",
    ]
    for key in alias_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            payload["master_outline_markdown"] = value
            break

    return True, "ok"


def gate_rubric_report(report):
    if not isinstance(report, dict):
        return False, "invalid rubric report"
    scores = report.get("scores") or {}

    values = []
    for key in RUBRIC_KEYS:
        value = scores.get(key)
        if not isinstance(value, (int, float)):
            return False, f"score for {key} is not numeric"
        values.append(float(value))

    if min(values) < BOOK_QUALITY_EFFECTIVE_MIN_SCORE:
        return False, f"one or more rubric scores below {BOOK_QUALITY_EFFECTIVE_MIN_SCORE:g}"
    if (sum(values) / len(values)) < BOOK_QUALITY_EFFECTIVE_MIN_AVG_SCORE:
        return False, f"rubric average below {BOOK_QUALITY_EFFECTIVE_MIN_AVG_SCORE:g}"

    content_score = scores.get("reader_engagement_score")
    if not isinstance(content_score, (int, float)):
        return False, "score for reader_engagement_score is not numeric"
    if float(content_score) < BOOK_QUALITY_EFFECTIVE_MIN_CONTENT_SCORE:
        return False, f"reader_engagement_score below {BOOK_QUALITY_EFFECTIVE_MIN_CONTENT_SCORE:g}"

    next_notes = report.get("next_writer_notes") or {}
    if not isinstance(next_notes, dict):
        return False, "next_writer_notes missing"
    for required_key in [
        "focus_topics",
        "continuity_watch",
        "must_carry_forward",
        "character_state_updates",
        "timeline_events",
        "unresolved_questions",
    ]:
        if required_key not in next_notes:
            return False, f"next_writer_notes missing {required_key}"

    for list_key in ["focus_topics", "continuity_watch", "must_carry_forward"]:
        value = next_notes.get(list_key)
        if not isinstance(value, list) or not value:
            return False, f"next_writer_notes.{list_key} must be a non-empty list"

    return True, "pass"


def gate_no_blocking_issues(report):
    if not isinstance(report, dict):
        return False, "invalid consistency report"
    issues = report.get("blocking_issues") or []
    if not isinstance(issues, list):
        return False, "blocking_issues must be a list"
    if issues:
        return False, "consistency review found blocking issues"
    return True, "pass"


# ---------------------------------------------------------------------------
# Arc consistency scorer (Todo 36)
# ---------------------------------------------------------------------------
ARC_CONSISTENCY_THRESHOLD = 0.6  # Minimum score; ensures a complete failure on either open-loop persistence or character-arc acknowledgement fails the gate
ARC_LOOP_MATCH_THRESHOLD = float(os.environ.get("ARC_LOOP_MATCH_THRESHOLD", "0.58"))
ARC_LOOP_WARNING_THRESHOLD = float(os.environ.get("ARC_LOOP_WARNING_THRESHOLD", "0.45"))
ARC_CONSISTENCY_WARNING_ONLY = str(os.environ.get("ARC_CONSISTENCY_WARNING_ONLY", "false")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _normalize_similarity_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _text_similarity(a: str, b: str) -> float:
    left = _normalize_similarity_text(a)
    right = _normalize_similarity_text(b)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0

    seq = difflib.SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    union = left_tokens | right_tokens
    jaccard = (len(left_tokens & right_tokens) / len(union)) if union else 1.0
    return round(max(seq, jaccard), 3)


def score_arc_consistency(
    arc_tracker: dict,
    rubric_report: dict,
) -> tuple[float, list[str], list[str], list[dict]]:
    """Score arc persistence (0.0–1.0) across two narrative tracking factors.

    Open loops are **intentional story features** that carry across chapters and
    must be resolved before the end of the story or series — they are NOT
    failures. The scorer checks that loops survive the chapter handoff:

    Factor 1: **Open-loop persistence** — are tracked loops from arc_tracker
      present in rubric next_writer_notes.must_carry_forward? A loop absent
      from carry_forward risks being forgotten in the next chapter.

    Factor 2: **Character-arc acknowledgement** — are tracked character arcs
      (from prior chapter entries) referenced in character_state_updates?
      This ensures characters are not silently abandoned.

    Blocking continuity issues are gated separately by gate_no_blocking_issues
    on the continuity stage and are not part of this score.

    Returns:
        (score, issues, untracked_loops)
          score          — 0.0–1.0
          issues         — human-readable diagnostic strings for failed factors
          untracked_loops — loops missing from must_carry_forward (for the
                            run journal annotation)
    """
    issues: list[str] = []
    untracked_loops: list[str] = []
    near_miss_scores: list[dict] = []
    factors: list[float] = []

    arc = arc_tracker if isinstance(arc_tracker, dict) else {}
    rubric = rubric_report if isinstance(rubric_report, dict) else {}
    next_notes = rubric.get("next_writer_notes") or {}
    next_notes = next_notes if isinstance(next_notes, dict) else {}

    # Factor 1: open-loop persistence into must_carry_forward
    # Cap to the 10 most recently added loops — arc_tracker accumulates ALL loops
    # across all chapters; requiring 60% of a 30-loop accumulation from chapter 15
    # is impossible and would always fail. Recency window keeps the check relevant.
    open_loops = _normalize_list(arc.get("open_loops"))
    open_loops = open_loops[-10:] if len(open_loops) > 10 else open_loops
    if open_loops:
        carry_forward_raw = _normalize_list(next_notes.get("must_carry_forward"))
        carry_corpus = [str(x) for x in carry_forward_raw]
        persisted = []
        dropped = []
        for loop in open_loops:
            best_score = 0.0
            best_match = ""
            for candidate in carry_corpus:
                score = _text_similarity(loop, candidate)
                if score > best_score:
                    best_score = score
                    best_match = candidate

            if best_score >= ARC_LOOP_MATCH_THRESHOLD:
                persisted.append(loop)
            elif best_score >= ARC_LOOP_WARNING_THRESHOLD:
                persisted.append(loop)
                near_miss_scores.append(
                    {
                        "open_loop": str(loop),
                        "best_match": best_match,
                        "score": best_score,
                        "classification": "near_miss_pass",
                    }
                )
            else:
                dropped.append(loop)
                near_miss_scores.append(
                    {
                        "open_loop": str(loop),
                        "best_match": best_match,
                        "score": best_score,
                        "classification": "untracked",
                    }
                )
        loop_score = len(persisted) / len(open_loops)
        factors.append(loop_score)
        if near_miss_scores:
            issues.append(
                "open-loop fuzzy matching used; inspect near_miss_scores in arc_consistency_score.json "
                f"(match>={ARC_LOOP_MATCH_THRESHOLD}, warning>={ARC_LOOP_WARNING_THRESHOLD})"
            )
        if dropped:
            untracked_loops = dropped
            issues.append(
                f"open-loop persistence {loop_score:.0%}: {len(dropped)}/{len(open_loops)} "
                f"loop(s) missing from must_carry_forward — risk of being lost next chapter: "
                + ", ".join(repr(l) for l in dropped[:5])
            )
    else:
        factors.append(1.0)  # No established loops yet — always passes

    # Factor 2: character-arc acknowledgement (arcs from prior chapter entries)
    # Cap to the 5 most recent character_arc entries to avoid accumulation failures.
    char_arc_entries = arc.get("character_arcs") or []
    char_arc_entries = [a for a in char_arc_entries if isinstance(a, dict)]
    char_arc_entries = char_arc_entries[-5:] if len(char_arc_entries) > 5 else char_arc_entries
    # Collect unique character names from recent chapter entries
    char_names: list[str] = []
    seen_names: set[str] = set()
    for entry in char_arc_entries:
        for update in _normalize_list(entry.get("updates")):
            key = str(update).lower()[:30]
            if key not in seen_names:
                seen_names.add(key)
                char_names.append(str(update).lower())
    if char_names:
        state_updates_raw = [str(u).lower() for u in _normalize_list(next_notes.get("character_state_updates"))]
        acknowledged = sum(
            1 for name in char_names
            if any(name[:30] in update for update in state_updates_raw)
        )
        char_score = acknowledged / len(char_names)
        factors.append(char_score)
        unacknowledged = len(char_names) - acknowledged
        if unacknowledged:
            issues.append(
                f"character-arc acknowledgement {char_score:.0%}: "
                f"{unacknowledged}/{len(char_names)} character updates not referenced in character_state_updates"
            )
    else:
        factors.append(1.0)  # No prior character arcs to track yet

    score = round(sum(factors) / len(factors), 3) if factors else 1.0
    return score, issues, untracked_loops, near_miss_scores


# ---------------------------------------------------------------------------

def validate_required_artifacts(run_dir: Path, expected_section_count: int):
    required = [
        run_dir / "00_brief/book_brief.json",
        run_dir / "01_research/research_dossier.md",
        run_dir / "02_outline/master_outline.md",
        run_dir / "02_outline/chapter_specs/chapter_01.json",
        run_dir / "03_canon/canon.json",
        run_dir / "03_canon/consistency_sections.json",
        run_dir / "03_canon/context_store.json",
        run_dir / "03_canon/session_handoffs.jsonl",
        run_dir / "03_canon/continuity_state.json",
        run_dir / "04_drafts/chapter_01/assembled.md",
        run_dir / "04_drafts/chapter_01/edited.md",
        run_dir / "04_drafts/chapter_01/copy_edited.md",
        run_dir / "04_drafts/chapter_01/proofread.md",
        run_dir / "05_reviews/assembly_reviews/chapter_assembly_review.json",
        run_dir / "05_reviews/developmental_report.json",
        run_dir / "05_reviews/rubric_report.json",
        run_dir / "05_reviews/continuity_report.json",
        run_dir / "05_reviews/next_writer_notes.json",
        run_dir / "06_final/manuscript_v1.md",
    ]

    for idx in range(1, expected_section_count + 1):
        required.append(run_dir / f"04_drafts/chapter_01/section_{idx:02d}.md")
        required.append(run_dir / f"05_reviews/section_reviews/section_{idx:02d}_review.json")

    missing = [str(path) for path in required if not path.exists()]
    return {"valid": len(missing) == 0, "missing": missing}


def validate_stage_correlation_integrity(run_journal_path: Path):
    total_stage_attempts = 0
    missing_correlation = 0
    missing_examples = []

    if not run_journal_path.exists():
        return {
            "valid": False,
            "total_stage_attempts": 0,
            "missing_correlation": 0,
            "missing_examples": [],
            "note": "run_journal_missing",
        }

    for raw_line in run_journal_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parsed = parse_json_block(line, fallback=None)
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("event") or "") != "stage_attempt_start":
            continue
        total_stage_attempts += 1
        details = parsed.get("details") if isinstance(parsed.get("details"), dict) else {}
        correlation_id = str(details.get("correlation_id") or "").strip()
        if not correlation_id:
            missing_correlation += 1
            if len(missing_examples) < 10:
                missing_examples.append(
                    {
                        "stage": details.get("stage"),
                        "attempt": details.get("attempt"),
                        "timestamp": parsed.get("timestamp"),
                    }
                )

    return {
        "valid": missing_correlation == 0,
        "total_stage_attempts": total_stage_attempts,
        "missing_correlation": missing_correlation,
        "missing_examples": missing_examples,
    }


def _word_count(text: str) -> int:
    return len((text or "").split())


def derive_context_tracking_strategy(book_payload: dict, canon_payload: dict, chapter_spec: dict):
    text_blob = " ".join(
        [
            str((book_payload or {}).get("premise", "")),
            str((chapter_spec or {}).get("purpose", "")),
            str((chapter_spec or {}).get("chapter_title", "")),
            str((canon_payload or {}).get("style_guide", "")),
        ]
    ).lower()

    time_markers = ["timeline", "year", "day", "night", "before", "after", "sequence", "history"]
    situation_markers = ["forgot", "memory", "identity", "amnesia", "confusion", "recall", "remember"]

    time_hits = sum(1 for marker in time_markers if marker in text_blob)
    situation_hits = sum(1 for marker in situation_markers if marker in text_blob)

    mode = "time_based" if time_hits >= situation_hits else "situation_based"
    return {
        "mode": mode,
        "time_hits": time_hits,
        "situation_hits": situation_hits,
        "policy": (
            "Prioritize chronological consistency and temporal causality."
            if mode == "time_based"
            else "Prioritize character-state/situation continuity and state transitions."
        ),
    }


def build_relevant_chapter_notes(rolling_memory: dict, chapter_number: int, section_goal: str, max_items: int = 6):
    chapter_summaries = (rolling_memory or {}).get("chapter_summaries") or []
    section_goal_tokens = {tok for tok in re.findall(r"[a-z0-9]+", (section_goal or "").lower()) if len(tok) > 3}

    scored = []
    for item in chapter_summaries:
        if not isinstance(item, dict):
            continue
        item_ch = int(item.get("chapter_number") or 0)
        recency = max(0, chapter_number - item_ch)
        summary_text = " ".join(
            [
                str(item.get("summary", "")),
                json.dumps(item.get("next_writer_notes", {})),
            ]
        ).lower()
        overlap = sum(1 for tok in section_goal_tokens if tok in summary_text)
        score = overlap * 10 - recency
        scored.append((score, item))

    scored.sort(key=lambda t: t[0], reverse=True)
    selected = [entry for _, entry in scored[:max_items]]
    unresolved = []
    for item in selected:
        loops = item.get("open_loops") or []
        if isinstance(loops, list):
            unresolved.extend(loops)

    return {
        "selected_notes": selected,
        "unresolved_threads": unresolved[:20],
        "note_usage_rule": "Use notes only when they support current section goals and do not conflict with current canon.",
    }


def _keyword_signals(text: str, max_items: int = 8) -> list[str]:
    stop_words = {
        "the", "and", "that", "with", "from", "this", "into", "about", "over",
        "under", "after", "before", "while", "where", "their", "there", "have",
        "must", "should", "will", "would", "could", "chapter", "section", "story",
    }
    tokens: list[str] = []
    seen: set[str] = set()
    for tok in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(tok) < 4 or tok in stop_words or tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
        if len(tokens) >= max_items:
            break
    return tokens


# ─────────────────────────────────────────────────────────────────────────────
# Research bootstrap: gather real source material before LLM synthesis.
# Uses Wikipedia OpenSearch + summaries, DuckDuckGo HTML snippets, and the
# Free Dictionary API via stdlib urllib. BeautifulSoup4 is used when available
# for DuckDuckGo HTML parsing; falls back gracefully without it.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from bs4 import BeautifulSoup as _BS4
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False

_RESEARCH_UA = "DragonLairResearch/1.0 (book-pipeline; github.com/daravenrk)"
_RESEARCH_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "chapter",
    "section", "story", "book", "about", "around", "must", "should", "have",
    "will", "your", "their", "there", "when", "where", "which", "what", "how",
    "who", "but", "not", "are", "its", "was", "has", "had", "can", "may",
    "one", "two", "all", "more", "than", "been", "just", "very", "some",
}


def _http_json_get(url: str, timeout_seconds: int = 8) -> dict | list | None:
    request = Request(url, headers={"User-Agent": _RESEARCH_UA, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = getattr(response.headers, "get_content_charset", lambda: None)() or "utf-8"
            payload = response.read().decode(charset, errors="replace")
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, (dict, list)) else None
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError):
        return None


def _http_html_get(url: str, timeout_seconds: int = 12) -> str | None:
    request = Request(url, headers={
        "User-Agent": _RESEARCH_UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = getattr(response.headers, "get_content_charset", lambda: None)() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except (URLError, TimeoutError, OSError):
        return None


def _build_research_query_terms(brief: dict, chapter: dict, premise: str) -> list[str]:
    text = " ".join([
        str((brief or {}).get("title_working") or ""),
        str((chapter or {}).get("chapter_title") or (chapter or {}).get("title") or ""),
        str((chapter or {}).get("section_title") or ""),
        str((chapter or {}).get("purpose") or (chapter or {}).get("section_goal") or ""),
        str(premise or ""),
    ])
    raw = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", text.lower())
    unique: list[str] = []
    for token in raw:
        if token not in _RESEARCH_STOP and token not in unique:
            unique.append(token)
        if len(unique) >= 12:
            break
    return unique


def _build_wikipedia_search_queries(brief: dict, chapter: dict, premise: str) -> list[str]:
    """Build up to 5 search queries optimised for Wikipedia OpenSearch."""
    queries: list[str] = []

    # Chapter purpose is usually the best thematic query
    chapter_purpose = str(
        (chapter or {}).get("purpose") or (chapter or {}).get("section_goal") or ""
    ).strip()
    if len(chapter_purpose) > 8:
        queries.append(" ".join(chapter_purpose.split()[:6]))

    chapter_title = str(
        (chapter or {}).get("chapter_title") or (chapter or {}).get("title") or ""
    ).strip()
    if chapter_title and chapter_title not in queries:
        queries.append(chapter_title)

    book_title = str((brief or {}).get("title_working") or "").strip()
    if book_title and book_title not in queries:
        queries.append(book_title)

    # Compound from meaningful terms
    terms = _build_research_query_terms(brief, chapter, premise)
    real_words = [t for t in terms if t.isalpha() and len(t) >= 4]
    if len(real_words) >= 2:
        queries.append(f"{real_words[0]} {real_words[1]}")
    elif real_words:
        queries.append(real_words[0])

    genre = str((brief or {}).get("genre") or "").strip()
    tone = str((brief or {}).get("tone") or "").strip()
    if genre and tone and f"{genre} {tone}" not in queries:
        queries.append(f"{genre} {tone}")

    return queries[:5]


def _wikipedia_search_and_fetch(query: str, max_articles: int = 2) -> list[dict]:
    """OpenSearch Wikipedia for query, then fetch summaries for the top hits."""
    packets: list[dict] = []
    search_url = (
        f"https://en.wikipedia.org/w/api.php"
        f"?action=opensearch&search={quote(query)}&limit={max_articles + 2}&format=json"
    )
    search_data = _http_json_get(search_url)
    if not isinstance(search_data, list) or len(search_data) < 2:
        return packets
    titles: list[str] = search_data[1] if isinstance(search_data[1], list) else []
    for title in titles[:max_articles]:
        title_slug = title.replace(" ", "_")
        summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title_slug)}"
        summary_data = _http_json_get(summary_url)
        if not isinstance(summary_data, dict):
            continue
        extract = str(summary_data.get("extract") or "").strip()
        if not extract or len(extract) < 50:
            continue
        if len(extract) > 1400:
            extract = extract[:1400] + "…"
        packet_id = f"wikipedia:{slugify(title)}"
        if not any(p.get("id") == packet_id for p in packets):
            packets.append({
                "id": packet_id,
                "source_type": "wikipedia_summary",
                "url": summary_url,
                "title": str(summary_data.get("title") or title),
                "facts": [extract],
            })
    return packets


def _duckduckgo_snippets(query: str, max_snippets: int = 5) -> list[str]:
    """Scrape DuckDuckGo HTML Lite for search result snippets (requires bs4)."""
    if not _BS4_AVAILABLE:
        return []
    url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    html = _http_html_get(url)
    if not html:
        return []
    soup = _BS4(html, "html.parser")
    snippets: list[str] = []
    for el in soup.select(".result__snippet"):
        text = el.get_text(separator=" ", strip=True)
        if text and len(text) > 20:
            snippets.append(text)
        if len(snippets) >= max_snippets:
            break
    return snippets


def _dictionary_packet(term: str) -> dict | None:
    """Fetch a Free Dictionary API entry for a single real word."""
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(term)}"
    data = _http_json_get(url)
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return None
    definitions: list[str] = []
    for meaning in (data[0].get("meanings") or [])[:3]:
        if not isinstance(meaning, dict):
            continue
        for defn in (meaning.get("definitions") or [])[:2]:
            text = str((defn or {}).get("definition") or "").strip()
            if text:
                definitions.append(text)
        if len(definitions) >= 4:
            break
    if not definitions:
        return None
    return {
        "id": f"dictionary:{term}",
        "source_type": "dictionary_api",
        "url": url,
        "title": f"Dictionary: '{term}'",
        "facts": definitions,
    }


def bootstrap_simple_research_packets(brief: dict, chapter: dict, premise: str) -> dict:
    """
    Gather real source material for the research stage before calling the LLM.
    Tries (in order): Wikipedia via OpenSearch + summaries, DuckDuckGo snippets,
    Free Dictionary API, then always appends the book premise anchor.
    Targets 4–8 non-empty source packets.
    """
    terms = _build_research_query_terms(brief, chapter, premise)
    wiki_queries = _build_wikipedia_search_queries(brief, chapter, premise)
    collected_ids: set[str] = set()
    packets: list[dict] = []

    def _add(p: dict | None) -> None:
        if not p or not p.get("facts"):
            return
        pid = str(p.get("id") or "")
        if pid and pid in collected_ids:
            return
        collected_ids.add(pid)
        packets.append(p)

    # Stage 1: Wikipedia – OpenSearch then fetch summary for each query
    for query in wiki_queries:
        if len(packets) >= 6:
            break
        for wiki_packet in _wikipedia_search_and_fetch(query, max_articles=2):
            _add(wiki_packet)
            if len(packets) >= 6:
                break

    # Stage 2: DuckDuckGo search snippets for the primary thematic query
    primary_query = wiki_queries[0] if wiki_queries else " ".join(terms[:3])
    ddg_snippets = _duckduckgo_snippets(primary_query, max_snippets=5)
    if len(ddg_snippets) >= 2:
        _add({
            "id": f"web:ddg:{slugify(primary_query[:40])}",
            "source_type": "web_search_snippets",
            "url": f"https://html.duckduckgo.com/html/?q={quote(primary_query)}",
            "title": f"Web search snippets: {primary_query}",
            "facts": ddg_snippets,
        })

    # Stage 3: Dictionary definitions for real thematic words (≥5 chars)
    real_words = [t for t in terms if t.isalpha() and len(t) >= 5]
    for word in real_words[:4]:
        if len(packets) >= 8:
            break
        _add(_dictionary_packet(word))

    # Stage 4: Always include the book premise and chapter goal as an anchor
    chapter_goal = str(
        (chapter or {}).get("purpose") or (chapter or {}).get("section_goal") or ""
    ).strip()
    premise_text = str(premise or "").strip()
    anchor_facts = [f for f in [premise_text, chapter_goal] if f]
    if anchor_facts:
        _add({
            "id": "book:premise_anchor",
            "source_type": "book_context",
            "url": "local://book_context",
            "title": "Book premise and chapter goal",
            "facts": anchor_facts,
        })

    return {
        "query_terms": terms,
        "wikipedia_queries": wiki_queries,
        "packets": packets,
    }


def render_research_source_packets_markdown(source_packets: list[dict]) -> str:
    if not source_packets:
        return ""
    lines = ["# Source Packets", ""]
    for packet in source_packets:
        packet_id = str(packet.get("id") or "source:unknown")
        title = str(packet.get("title") or packet_id)
        source_url = str(packet.get("url") or "")
        lines.append(f"## {packet_id}")
        lines.append(f"- Title: {title}")
        if source_url and not source_url.startswith("local://"):
            lines.append(f"- URL: {source_url}")
        facts = packet.get("facts") if isinstance(packet.get("facts"), list) else []
        for fact in facts[:5]:
            text = str(fact or "").strip()
            if text:
                lines.append(f"- {text[:600]}")
        lines.append("")
    return "\n".join(lines).strip()
def _match_targets_in_blob(targets: list[str], blob: str, max_key: int = 48) -> tuple[list[str], list[str]]:
    normalized = str(blob or "").lower()
    matched: list[str] = []
    missing: list[str] = []
    for item in targets:
        key = str(item or "").strip().lower()[:max_key]
        if not key:
            continue
        if key in normalized:
            matched.append(str(item))
        else:
            missing.append(str(item))
    return matched, missing


def build_section_consistency_sections(
    *,
    chapter_number: int,
    chapter_title: str,
    chapter_spec: dict,
    canon_payload: dict,
    previous_next_writer_notes: dict,
) -> dict:
    """Build per-section continuity checkpoints used by writer/reviewer stages."""

    def _dedupe(items: list[str], max_items: int = 10) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in items:
            text = str(raw or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= max_items:
                break
        return out

    def _normalize_signal_items(value) -> list[str]:
        if isinstance(value, list):
            signals: list[str] = []
            for entry in value:
                if isinstance(entry, dict):
                    for key in ("loop", "title", "name", "item", "description", "summary"):
                        if key in entry and str(entry.get(key) or "").strip():
                            signals.append(str(entry.get(key)))
                            break
                else:
                    signals.append(str(entry))
            return _dedupe(signals)
        if isinstance(value, dict):
            signals = []
            for key, entry in value.items():
                key_text = str(key or "").strip()
                if key_text:
                    signals.append(key_text)
                if isinstance(entry, dict):
                    for cand in ("name", "summary", "description"):
                        if str(entry.get(cand) or "").strip():
                            signals.append(str(entry.get(cand)))
                            break
            return _dedupe(signals)
        return _normalize_list(value)

    chapter_sections = chapter_spec.get("sections") if isinstance(chapter_spec, dict) else []
    if not isinstance(chapter_sections, list):
        chapter_sections = []

    canon = canon_payload.get("canon") if isinstance(canon_payload.get("canon"), dict) else {}
    open_loops = _normalize_signal_items(canon_payload.get("open_loops"))
    if not open_loops:
        open_loops = _normalize_signal_items(canon.get("open_loops"))

    character_bible = canon_payload.get("character_bible") if isinstance(canon_payload.get("character_bible"), dict) else {}
    character_targets = _normalize_signal_items(character_bible)
    if not character_targets:
        character_targets = _normalize_signal_items(previous_next_writer_notes.get("character_arcs") if isinstance(previous_next_writer_notes, dict) else None)
    if not character_targets:
        character_targets = [
            "Character details pending downstream continuity/editorial refinement.",
        ]

    important_elements: list[str] = []
    important_elements.extend(_normalize_signal_items((chapter_spec or {}).get("must_include")))
    important_elements.extend(_normalize_signal_items((chapter_spec or {}).get("must_avoid")))
    important_elements.extend(_normalize_signal_items((canon or {}).get("constraints")))
    important_elements.extend(_normalize_signal_items((canon or {}).get("acceptance_criteria")))
    if isinstance(previous_next_writer_notes, dict):
        important_elements.extend(_normalize_signal_items(previous_next_writer_notes.get("must_retain")))
        important_elements.extend(_normalize_signal_items(previous_next_writer_notes.get("revision_focus")))
    important_elements = _dedupe(important_elements, max_items=12)
    if not important_elements:
        important_elements = [
            "Preserve canon consistency for named characters and key elements.",
        ]

    sections_payload = []
    for idx, section_item in enumerate(chapter_sections, start=1):
        if isinstance(section_item, dict):
            section_title = str(section_item.get("title") or section_item.get("name") or f"Section {idx}")
            section_goal = str(section_item.get("objective") or section_item.get("goal") or "Advance chapter objective")
        else:
            section_title = str(section_item or f"Section {idx}")
            section_goal = "Advance chapter objective"

        situations = _keyword_signals(f"{section_title} {section_goal}", max_items=6)
        expectations = [
            f"Advance this section goal: {section_goal}",
            "Maintain continuity with prior accepted sections in this chapter.",
            "Preserve canon consistency for named characters and key elements.",
        ]

        sections_payload.append(
            {
                "section_index": idx,
                "section_title": section_title,
                "section_goal": section_goal,
                "expectations": expectations,
                "situations": situations,
                "tracking_targets": {
                    "open_loops": _dedupe(open_loops, max_items=8),
                    "character_arcs": _dedupe(character_targets, max_items=8),
                    "important_elements": _dedupe(important_elements, max_items=10),
                },
                "status": "pending",
                "coverage": {
                    "open_loops": [],
                    "character_arcs": [],
                    "important_elements": [],
                    "situation_hits": [],
                },
                "missing_tracking": {
                    "open_loops": [],
                    "character_arcs": [],
                    "important_elements": [],
                },
                "accepted_at": None,
                "last_updated": datetime.utcnow().isoformat(),
            }
        )

    return {
        "chapter_number": int(chapter_number or 0),
        "chapter_title": str(chapter_title or ""),
        "generated_at": datetime.utcnow().isoformat(),
        "active_section_index": 1,
        "sections": sections_payload,
        "ledger": [],
    }


def update_section_consistency_after_review(
    consistency_sections: dict,
    *,
    section_index: int,
    section_text: str,
    section_review: dict,
) -> tuple[dict, dict]:
    payload = copy.deepcopy(consistency_sections) if isinstance(consistency_sections, dict) else {}
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    checkpoint = None
    for item in sections:
        if int(item.get("section_index") or 0) == int(section_index):
            checkpoint = item
            break

    if checkpoint is None:
        return payload, {}

    review_blob = " ".join(
        [
            str(section_text or ""),
            str((section_review or {}).get("section_summary") or ""),
            json.dumps((section_review or {}).get("continuity_state_updates") or []),
            json.dumps((section_review or {}).get("warnings") or []),
        ]
    )

    targets = checkpoint.get("tracking_targets") if isinstance(checkpoint.get("tracking_targets"), dict) else {}
    open_loops = _normalize_list(targets.get("open_loops"))
    char_arcs = _normalize_list(targets.get("character_arcs"))
    elements = _normalize_list(targets.get("important_elements"))
    situations = _normalize_list(checkpoint.get("situations"))

    open_hits, open_missing = _match_targets_in_blob(open_loops, review_blob)
    char_hits, char_missing = _match_targets_in_blob(char_arcs, review_blob)
    element_hits, element_missing = _match_targets_in_blob(elements, review_blob)
    situation_hits, _ = _match_targets_in_blob(situations, review_blob, max_key=24)

    checkpoint["status"] = "accepted"
    checkpoint["accepted_at"] = datetime.utcnow().isoformat()
    checkpoint["last_updated"] = datetime.utcnow().isoformat()
    checkpoint["coverage"] = {
        "open_loops": open_hits,
        "character_arcs": char_hits,
        "important_elements": element_hits,
        "situation_hits": situation_hits,
    }
    checkpoint["missing_tracking"] = {
        "open_loops": open_missing,
        "character_arcs": char_missing,
        "important_elements": element_missing,
    }

    payload["active_section_index"] = min(section_index + 1, len(sections)) if sections else 0
    ledger = payload.get("ledger") if isinstance(payload.get("ledger"), list) else []
    ledger.append(
        {
            "timestamp": datetime.utcnow().isoformat(),
            "section_index": section_index,
            "status": "accepted",
            "coverage": checkpoint.get("coverage"),
            "missing_tracking": checkpoint.get("missing_tracking"),
        }
    )
    payload["ledger"] = ledger
    return payload, checkpoint


def chunk_items_by_word_budget(items, max_words):
    groups = []
    oversize = []
    current = []
    current_words = 0

    for item in items:
        text = item.get("text", "")
        words = _word_count(text)

        if words > max_words:
            oversize.append({"id": item.get("id"), "words": words, "max_words": max_words})
            if current:
                groups.append(current)
                current = []
                current_words = 0
            groups.append([item])
            continue

        if not current or (current_words + words) <= max_words:
            current.append(item)
            current_words += words
        else:
            groups.append(current)
            current = [item]
            current_words = words

    if current:
        groups.append(current)

    return groups, oversize


def _load_changes_log(path: Path):
    entries = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = parse_json_block(line, fallback=None)
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def _build_stage_attempt_summary(entries):
    summary = {}
    for entry in entries:
        action = entry.get("action")
        details = entry.get("details") or {}
        stage = details.get("stage")
        if not stage:
            continue
        bucket = summary.setdefault(stage, {"attempts": 0, "last_gate_ok": None, "last_gate_message": None})
        if action == "stage_start":
            bucket["attempts"] += 1
        elif action == "stage_result":
            bucket["last_gate_ok"] = details.get("gate_ok")
            bucket["last_gate_message"] = details.get("gate_message")
    return summary


def build_retro_report(run_dir: Path, summary: dict):
    reviews_dir = run_dir / "05_reviews"
    drafts_dir = run_dir / "04_drafts/chapter_01"
    final_dir = run_dir / "06_final"

    developmental = parse_json_block(read_text(reviews_dir / "developmental_report.json", "{}"), fallback={})
    rubric = parse_json_block(read_text(reviews_dir / "rubric_report.json", "{}"), fallback={})
    continuity = parse_json_block(read_text(reviews_dir / "continuity_report.json", "{}"), fallback={})
    publisher = parse_json_block(read_text(reviews_dir / "publisher_report.json", "{}"), fallback={})
    next_writer_notes = parse_json_block(read_text(reviews_dir / "next_writer_notes.json", "{}"), fallback={})
    changes_entries = _load_changes_log(run_dir / "changes.log")

    manuscript_text = read_text(final_dir / "manuscript_v1.md", "")
    stage_attempts = _build_stage_attempt_summary(changes_entries)
    rubric_scores = rubric.get("scores") or {}

    retro = {
        "run": {
            "run_dir": str(run_dir),
            "title": summary.get("title"),
            "chapter_number": summary.get("chapter_number"),
            "chapter_title": summary.get("chapter_title"),
            "section_title": summary.get("section_title"),
            "created_at": datetime.utcnow().isoformat(),
        },
        "output": {
            "final_word_count": _word_count(manuscript_text),
            "publisher_decision": summary.get("publisher_decision"),
            "artifact_validation": summary.get("artifact_validation"),
        },
        "quality": {
            "developmental": developmental,
            "rubric_scores": rubric_scores,
            "continuity": continuity,
            "publisher": publisher,
        },
        "workflow": {
            "stage_attempts": stage_attempts,
            "log_entries": len(changes_entries),
        },
        "handoff": {
            "next_writer_notes": next_writer_notes,
            "has_focus_topics": bool((next_writer_notes.get("focus_topics") if isinstance(next_writer_notes, dict) else None)),
            "has_continuity_watch": bool((next_writer_notes.get("continuity_watch") if isinstance(next_writer_notes, dict) else None)),
        },
    }

    low_scores = []
    if isinstance(rubric_scores, dict):
        for key, value in rubric_scores.items():
            if isinstance(value, (int, float)) and value < BOOK_QUALITY_EFFECTIVE_MIN_SCORE:
                low_scores.append({"dimension": key, "score": value})
    retro["improvement_targets"] = low_scores

    return retro


def build_retro_markdown(retro: dict) -> str:
    run = retro.get("run") or {}
    out = retro.get("output") or {}
    workflow = retro.get("workflow") or {}
    handoff = retro.get("handoff") or {}
    quality = retro.get("quality") or {}
    scores = quality.get("rubric_scores") or {}
    low_scores = retro.get("improvement_targets") or []

    lines = [
        "# Book Run Retrospective",
        "",
        f"- Title: {run.get('title', '')}",
        f"- Chapter: {run.get('chapter_number', '')} - {run.get('chapter_title', '')}",
        f"- Section: {run.get('section_title', '')}",
        f"- Publisher decision: {out.get('publisher_decision', '')}",
        f"- Final word count: {out.get('final_word_count', 0)}",
        f"- Log entries: {workflow.get('log_entries', 0)}",
        "",
        "## Rubric Scores",
    ]

    if isinstance(scores, dict) and scores:
        for key in sorted(scores.keys()):
            lines.append(f"- {key}: {scores.get(key)}")
    else:
        lines.append("- No rubric scores found")

    lines.extend([
        "",
        "## Continuity Handoff",
        f"- Has focus topics: {handoff.get('has_focus_topics', False)}",
        f"- Has continuity watch: {handoff.get('has_continuity_watch', False)}",
        "",
        "## Improvement Targets",
    ])

    if low_scores:
        for item in low_scores:
            lines.append(f"- {item.get('dimension')}: {item.get('score')}")
    else:
        lines.append(f"- None (all scored dimensions >= {BOOK_QUALITY_EFFECTIVE_MIN_SCORE:g})")

    lines.extend([
        "",
        "## Stage Attempts",
    ])

    stage_attempts = (workflow.get("stage_attempts") or {})
    if stage_attempts:
        for stage, data in stage_attempts.items():
            lines.append(
                f"- {stage}: attempts={data.get('attempts', 0)}, gate_ok={data.get('last_gate_ok')}, gate_message={data.get('last_gate_message')}"
            )
    else:
        lines.append("- No stage attempt data found")

    return "\n".join(lines) + "\n"


def load_recent_jsonl(path: Path, limit: int = 50):
    if not path.exists():
        return []
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    tail = raw_lines[-max(1, int(limit)):]
    rows = []
    for line in tail:
        item = parse_json_block(line, fallback=None)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def collect_used_fallback_stages(run_journal_path: Path) -> list:
    """Return sorted unique fallback stages observed in run_journal.jsonl."""
    if not run_journal_path.exists():
        return []

    used = set()
    for line in run_journal_path.read_text(encoding="utf-8").splitlines():
        item = parse_json_block(line, fallback=None)
        if not isinstance(item, dict):
            continue
        if str(item.get("event") or "") != "stage_fallback_applied":
            continue
        details = item.get("details")
        if not isinstance(details, dict):
            details = {}
        stage = str(details.get("stage") or item.get("stage") or "").strip()
        if stage:
            used.add(stage)
    return sorted(used)


def build_quality_failure_review_markdown(entries: list) -> str:
    lines = [
        "# Quality Failure Review",
        "",
        "Recent quality gate failures from perpetual tracking log.",
        "",
    ]
    if not entries:
        lines.append("- No recent quality gate failures recorded.")
        return "\n".join(lines) + "\n"

    for item in entries:
        lines.append(
            "- "
            + f"stage={item.get('stage', '')}, "
            + f"profile={item.get('profile', '')}, "
            + f"model={item.get('model', '')}, "
            + f"attempt={item.get('attempt', '')}, "
            + f"message={item.get('gate_message', '')}"
        )
    return "\n".join(lines) + "\n"


def run_stage(
    *,
    orchestrator,
    lock_manager,
    changes_log,
    context_store,
    stage_id,
    agent_name,
    profile_name,
    prompt,
    output_path=None,
    parse_json=False,
    output_schema=None,
    gate_fn=None,
    max_retries=2,
    diagnostics_path=None,
    verbose=False,
    debug=False,
    recovery_mode=None,
    support_profile_name=None,
):
    stage_instantiated_at = datetime.utcnow().isoformat()
    run_journal_path = context_store.get("_run_journal_path") if isinstance(context_store, dict) else None
    run_journal_path = Path(run_journal_path) if run_journal_path else None

    lock_before = lock_manager.get_lock_status(name="changes_log")
    lock_manager.log_agent_change(
        changes_log,
        agent_name,
        "lock_check",
        {"stage": stage_id, "lock_before": lock_before},
    )
    if verbose:
        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_instantiated",
            {"stage": stage_id, "instantiated_at": stage_instantiated_at},
        )
    append_run_event(
        run_journal_path,
        "stage_instantiated",
        {
            "stage": stage_id,
            "agent": agent_name,
            "profile": profile_name,
            "instantiated_at": stage_instantiated_at,
        },
    )

    last_raw = ""
    last_error = ""
    last_feedback = None
    parsed = None
    recovery_attempts = 1
    smart_recovery_default_stages = {"research", "architect_outline", "chapter_planner", "canon"}
    effective_recovery_mode = str(recovery_mode or ("smart" if stage_id in smart_recovery_default_stages else "simple")).strip().lower()
    if effective_recovery_mode not in {"simple", "smart"}:
        effective_recovery_mode = "simple"
    effective_support_profile = str(support_profile_name or "").strip() or None
    if effective_recovery_mode == "smart" and effective_support_profile is None and stage_id != "research":
        # Use the research-oriented profile as a helper model to infer missing data.
        effective_support_profile = "book-researcher"

    def persist_output(payload):
        if output_path is None:
            return
        with lock_manager.edit_lock(name="publisher_store"):
            if parse_json:
                write_json(output_path, payload)
            else:
                write_text(output_path, str(payload))

    def finalize_success(*, payload, raw_output, attempt, attempt_started_at, attempt_completed_at, recovered, gate_message, recovery_profile=None):
        persist_output(payload)

        if diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "event": "stage_success",
                    "instantiated_at": stage_instantiated_at,
                    "attempt": attempt,
                    "attempt_started_at": attempt_started_at,
                    "attempt_completed_at": attempt_completed_at,
                    "raw_output": raw_output,
                    "parsed_output": payload if parse_json else None,
                    "gate_ok": True,
                    "gate_message": gate_message,
                    "recovered": recovered,
                    "recovery_profile": recovery_profile,
                    "output_path": str(output_path) if output_path else None,
                },
            )

        context_store[stage_id] = {
            "agent": agent_name,
            "profile": profile_name,
            "output_path": str(output_path) if output_path else None,
            "lock_after": lock_manager.get_lock_status(name="changes_log"),
            "recovered": recovered,
        }

        handoff_dir = context_store.get("_handoff_dir")
        if handoff_dir:
            handoff_payload = {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "output_path": str(output_path) if output_path else None,
                "lock_after": context_store[stage_id]["lock_after"],
                "publisher_visible": True,
                "recovered": recovered,
            }
            write_json(Path(handoff_dir) / f"{stage_id}.json", handoff_payload)
            handoff_md = [
                f"# Stage Handoff: {stage_id}",
                "",
                f"- Agent: {agent_name}",
                f"- Profile: {profile_name}",
                f"- Output Path: {str(output_path) if output_path else 'inline'}",
                f"- Recovered: {recovered}",
                "",
                "## LLM Response",
                "",
            ]
            if parse_json:
                handoff_md.extend([
                    "```json",
                    json.dumps(payload, indent=2),
                    "```",
                ])
            else:
                handoff_md.append(str(payload))
            write_text(Path(handoff_dir) / f"{stage_id}.md", "\n".join(handoff_md) + "\n")

        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_complete",
            {
                "stage": stage_id,
                "attempt": attempt,
                "output_path": str(output_path) if output_path else None,
                "lock_after": context_store[stage_id]["lock_after"],
                "report_to_publisher": True,
                "recovered": recovered,
                "recovery_profile": recovery_profile,
            },
        )
        append_run_event(
            run_journal_path,
            "stage_complete",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "attempt": attempt,
                "output_path": str(output_path) if output_path else None,
                "recovered": recovered,
                "recovery_profile": recovery_profile,
            },
        )
        update_cli_runtime_activity_from_context(
            context_store,
            {
                "source": "cli-book-flow",
                "state": "running",
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "status_detail": f"stage complete: {stage_id}",
            },
        )
        return payload

    def _trim_text(value, limit=1600):
        text = str(value or "").strip()
        if not text:
            return ""
        return text if len(text) <= limit else text[:limit] + "..."

    def _build_recovery_context_guidance():
        if not isinstance(context_store, dict):
            return ""

        guidance = {
            "book": context_store.get("book") if isinstance(context_store.get("book"), dict) else {},
            "chapter": context_store.get("chapter") if isinstance(context_store.get("chapter"), dict) else {},
            "brief": ((context_store.get("permanent_memory") or {}).get("book_brief") or {}),
            "rolling_memory_recent": _normalize_list(context_store.get("rolling_memory"))[-4:],
        }

        text_blocks = []
        research_hint = context_store.get("research")
        if isinstance(research_hint, str):
            text_blocks.append({"research_excerpt": _trim_text(research_hint, 2200)})
        elif isinstance(research_hint, dict):
            text_blocks.append({"research_context": research_hint})

        if text_blocks:
            guidance["additional"] = text_blocks

        try:
            return json.dumps(guidance, ensure_ascii=True, indent=2)
        except Exception:
            return str(guidance)

    def _fetch_support_guidance(current_prompt):
        if effective_recovery_mode != "smart" or not effective_support_profile:
            return ""

        schema_hint = ""
        if parse_json and output_schema:
            schema_hint = f"\nRequired output schema id: {output_schema}."
        elif parse_json:
            schema_hint = "\nRequired output type: JSON object."

        support_prompt = (
            "You are a recovery support model.\n"
            f"Stage: {stage_id}\n"
            f"Failure reason: {last_error or 'unknown'}\n"
            + schema_hint
            + "\nGiven the failed output, current prompt, and context guidance, identify the missing or weak elements that must be present to pass quality gates."
            "\nReturn concise completion guidance with specific required fields/content."
            "\nDo not return the final stage output; return only helper guidance."
            "\n\nCURRENT PROMPT:\n"
            + _trim_text(current_prompt, 4000)
            + "\n\nLAST OUTPUT:\n"
            + _trim_text(last_feedback if last_feedback is not None else last_raw, 3000)
            + "\n\nCONTEXT GUIDANCE:\n"
            + _trim_text(_build_recovery_context_guidance(), 5000)
        )

        try:
            support_raw = orchestrator.handle_request_with_overrides(
                support_prompt,
                profile_name=effective_support_profile,
                stream_override=False,
            )
        except Exception:
            return ""
        return _trim_text(support_raw, 2500)

    def build_recovery_prompt(current_prompt, support_guidance=""):
        feedback_block = ""
        if isinstance(last_feedback, dict) and last_feedback:
            feedback_block = "\n\nLAST STRUCTURED OUTPUT:\n" + json.dumps(last_feedback, indent=2)
        elif last_feedback is not None:
            feedback_block = "\n\nLAST OUTPUT:\n" + str(last_feedback)
        raw_block = f"\n\nLAST RAW OUTPUT:\n{last_raw}" if last_raw else ""
        if effective_recovery_mode == "simple":
            return (
                current_prompt
                + feedback_block
                + raw_block
                + "\n\nRECOVERY INSTRUCTION:\n"
                + "Your last attempt failed. Repair the output so it fully satisfies the required format and all quality constraints."
                + f"\nFailure reason: {last_error or 'unknown failure'}"
                + "\nDo not explain the failure. Return only the corrected final output."
            )

        context_guidance = _build_recovery_context_guidance()
        support_block = f"\n\nSUPPORT MODEL GUIDANCE:\n{support_guidance}" if support_guidance else ""
        return (
            current_prompt
            + feedback_block
            + raw_block
            + "\n\nCONTEXT GUIDANCE:\n"
            + context_guidance
            + support_block
            + "\n\nINTELLIGENT RECOVERY INSTRUCTION:\n"
            + "Your previous attempt failed quality checks. Infer and synthesize any missing required information using the available context guidance and support model hints."
            + f"\nFailure reason: {last_error or 'unknown failure'}"
            + "\nReturn a complete output that satisfies all schema fields and quality constraints."
            + "\nDo not ask questions. Do not include explanations. Return only the corrected final output."
        )

    def apply_quarantine_backoff(*, error_code, error_details, attempt_index, route_hint, phase):
        if str(error_code or "") != "AGENT_QUARANTINED":
            return

        now = time.time()
        details = error_details if isinstance(error_details, dict) else {}
        route_name = str(details.get("agent") or route_hint or "").strip()

        quarantined_until = 0.0
        try:
            quarantined_until = float(details.get("quarantined_until") or 0.0)
        except (TypeError, ValueError):
            quarantined_until = 0.0

        remaining_seconds = max(0.0, quarantined_until - now) if quarantined_until > 0 else 0.0

        # If details are stale/missing, fall back to current health report for the route.
        if route_name and remaining_seconds <= 0.0:
            try:
                health_report = orchestrator.get_agent_health_report()
                route_health = ((health_report or {}).get("agents") or {}).get(route_name) or {}
                remaining_seconds = max(
                    remaining_seconds,
                    float(route_health.get("quarantine_remaining_seconds") or 0.0),
                )
            except Exception:
                pass

        if remaining_seconds <= 0.0:
            # Avoid hot-looping if quarantine metadata is unavailable.
            remaining_seconds = 1.0

        jitter_seconds = min(1.0, 0.25 * max(1, int(attempt_index)))
        delay_seconds = max(1.0, remaining_seconds + jitter_seconds)

        append_run_event(
            run_journal_path,
            "stage_retry_backoff",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "phase": phase,
                "attempt": int(attempt_index),
                "error_code": str(error_code),
                "route": route_name or None,
                "quarantine_remaining_seconds": remaining_seconds,
                "retry_delay_seconds": delay_seconds,
            },
        )
        if diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": int(attempt_index),
                    "event": "stage_retry_backoff",
                    "phase": phase,
                    "error_code": str(error_code),
                    "route": route_name or None,
                    "quarantine_remaining_seconds": remaining_seconds,
                    "retry_delay_seconds": delay_seconds,
                },
            )
        update_cli_runtime_activity_from_context(
            context_store,
            {
                "source": "cli-book-flow",
                "state": "running",
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "status_detail": f"quarantine backoff {delay_seconds:.1f}s ({phase})",
            },
        )
        time.sleep(delay_seconds)

    for attempt in range(1, max_retries + 2):
        stage_correlation_id = str(uuid.uuid4())
        resource_block = build_resource_reference_block(context_store)
        prompt_with_feedback = prompt
        if resource_block:
            prompt_with_feedback = prompt_with_feedback + "\n\n" + resource_block

        if last_error:
            feedback_block = ""
            if last_feedback is not None:
                # If reviewer output is JSON, pretty-print it; else, show as text
                if isinstance(last_feedback, dict):
                    feedback_block = "\n\nPREVIOUS REVIEWER FEEDBACK (JSON):\n" + json.dumps(last_feedback, indent=2)
                else:
                    feedback_block = "\n\nPREVIOUS REVIEWER FEEDBACK:\n" + str(last_feedback)
            prompt_with_feedback = (
                prompt_with_feedback
                + feedback_block
                + "\n\nPREVIOUS ATTEMPT FAILED QUALITY GATE:\n"
                + last_error
                + "\nRevise output to satisfy all constraints and output format."
            )

        # Capture resolved route/model/options before invocation for upstream diagnostics.
        resolved_plan = None
        try:
            resolved_plan = orchestrator.plan_request(
                prompt_with_feedback,
                profile_name=profile_name,
                stream_override=False,
                correlation_id=stage_correlation_id,
            )
        except AgentStackError:
            resolved_plan = None

        # Diagnostics: log prompt and context before agent call
        if diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                    "event": "pre_agent_call",
                    "prompt": prompt_with_feedback,
                    "context_store": dict(context_store),
                    "resolved_route": (resolved_plan or {}).get("route"),
                    "resolved_model": (resolved_plan or {}).get("model"),
                    "resolved_profile": ((resolved_plan or {}).get("profile") or {}).get("name"),
                    "resolved_stream": (resolved_plan or {}).get("stream"),
                    "resolved_options": (resolved_plan or {}).get("options"),
                    "correlation_id": stage_correlation_id,
                    "ml_mode": ((resolved_plan or {}).get("ml_shadow") or {}).get("mode"),
                    "ml_shadow_top_k": ((resolved_plan or {}).get("ml_shadow") or {}).get("top_k"),
                    "ml_shadow_chosen": ((resolved_plan or {}).get("ml_shadow") or {}).get("chosen"),
                },
            )
        attempt_started_at = datetime.utcnow().isoformat()
        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_start",
            {
                "stage": stage_id,
                "attempt": attempt,
                "correlation_id": stage_correlation_id,
                "route": (resolved_plan or {}).get("route"),
                "model": (resolved_plan or {}).get("model"),
                "profile": ((resolved_plan or {}).get("profile") or {}).get("name"),
            },
        )
        append_run_event(
            run_journal_path,
            "stage_attempt_start",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "attempt": attempt,
                "correlation_id": stage_correlation_id,
                "route": (resolved_plan or {}).get("route"),
                "model": (resolved_plan or {}).get("model"),
                "ml_mode": ((resolved_plan or {}).get("ml_shadow") or {}).get("mode"),
                "ml_shadow_top_k": ((resolved_plan or {}).get("ml_shadow") or {}).get("top_k"),
                "ml_shadow_chosen": ((resolved_plan or {}).get("ml_shadow") or {}).get("chosen"),
            },
        )
        update_cli_runtime_activity_from_context(
            context_store,
            {
                "source": "cli-book-flow",
                "state": "running",
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "route": (resolved_plan or {}).get("route"),
                "model": (resolved_plan or {}).get("model"),
                "attempt": attempt,
                "correlation_id": stage_correlation_id,
                "status_detail": f"stage attempt {attempt}: {stage_id}",
            },
        )

        try:
            raw = orchestrator.handle_request_with_overrides(
                prompt_with_feedback,
                profile_name=profile_name,
                stream_override=False,
                correlation_id=stage_correlation_id,
            )
        except AgentStackError as e:
            last_error = f"[{e.code}] {e}"
            last_feedback = {"error": str(e), "error_code": e.code}
            if diagnostics_path is not None:
                append_jsonl(
                    diagnostics_path,
                    {
                        "stage": stage_id,
                        "agent": agent_name,
                        "profile": profile_name,
                        "attempt": attempt,
                        "event": "agent_call_error",
                        "correlation_id": stage_correlation_id,
                        "error": str(e),
                        "error_code": e.code,
                        "prompt": prompt_with_feedback,
                        "context_store": dict(context_store),
                    },
                )
            lock_manager.log_agent_change(
                changes_log,
                agent_name,
                "stage_result",
                {
                    "stage": stage_id,
                    "attempt": attempt,
                    "gate_ok": False,
                    "gate_message": last_error,
                },
            )
            append_run_event(
                run_journal_path,
                "stage_attempt_error",
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                    "error": str(e),
                    "error_code": e.code,
                },
            )
            apply_quarantine_backoff(
                error_code=e.code,
                error_details=getattr(e, "details", {}),
                attempt_index=attempt,
                route_hint=(resolved_plan or {}).get("route"),
                phase="attempt",
            )
            continue
        except Exception as e:
            wrapped_error = AgentUnexpectedError(
                f"Stage attempt failed unexpectedly: {e}",
                details={
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                },
            )
            last_error = f"[{wrapped_error.code}] {wrapped_error}"
            last_feedback = {"error": str(wrapped_error), "error_code": wrapped_error.code}
            if diagnostics_path is not None:
                append_jsonl(
                    diagnostics_path,
                    {
                        "stage": stage_id,
                        "agent": agent_name,
                        "profile": profile_name,
                        "attempt": attempt,
                        "event": "agent_call_error",
                        "error": str(wrapped_error),
                        "error_code": wrapped_error.code,
                        "prompt": prompt_with_feedback,
                        "context_store": dict(context_store),
                    },
                )
            lock_manager.log_agent_change(
                changes_log,
                agent_name,
                "stage_result",
                {
                    "stage": stage_id,
                    "attempt": attempt,
                    "gate_ok": False,
                    "gate_message": last_error,
                },
            )
            append_run_event(
                run_journal_path,
                "stage_attempt_error",
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                    "error": str(wrapped_error),
                    "error_code": wrapped_error.code,
                },
            )
            continue
        attempt_completed_at = datetime.utcnow().isoformat()
        last_raw = raw
        parsed = parse_json_block(raw, fallback={}) if parse_json else raw

        if debug and diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                    "event": "stage_attempt_payload",
                    "attempt_started_at": attempt_started_at,
                    "attempt_completed_at": attempt_completed_at,
                    "prompt": prompt_with_feedback,
                    "raw_output": raw,
                    "parsed_output": parsed if parse_json else None,
                },
            )

        gate_ok = True
        gate_message = "ok"
        if parse_json and output_schema:
            gate_ok, gate_message = validate_stage_payload(output_schema, parsed)
        if gate_fn:
            if gate_ok:
                gate_ok, gate_message = gate_fn(parsed)

        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_result",
            {
                "stage": stage_id,
                "attempt": attempt,
                "gate_ok": gate_ok,
                "gate_message": gate_message,
            },
        )
        append_run_event(
            run_journal_path,
            "stage_attempt_result",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "attempt": attempt,
                "correlation_id": stage_correlation_id,
                "gate_ok": gate_ok,
                "gate_message": gate_message,
            },
        )

        if gate_ok:
            orchestrator.record_quality_gate_success(
                stage=stage_id,
                agent=agent_name,
                profile=profile_name,
                model=(resolved_plan or {}).get("model"),
                run_journal_path=str(run_journal_path) if run_journal_path else None,
                attempt=attempt,
            )
            return finalize_success(
                payload=parsed,
                raw_output=raw,
                attempt=attempt,
                attempt_started_at=attempt_started_at,
                attempt_completed_at=attempt_completed_at,
                recovered=False,
                gate_message=gate_message,
            )

        last_error = gate_message
        orchestrator.record_quality_gate_failure(
            stage=stage_id,
            agent=agent_name,
            profile=profile_name,
            model=(resolved_plan or {}).get("model"),
            gate_message=gate_message,
            run_journal_path=str(run_journal_path) if run_journal_path else None,
            attempt=attempt,
        )
        last_feedback = parsed
        if verbose and diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "instantiated_at": stage_instantiated_at,
                    "attempt": attempt,
                    "attempt_started_at": attempt_started_at,
                    "attempt_completed_at": attempt_completed_at,
                    "correlation_id": stage_correlation_id,
                    "prompt": prompt_with_feedback,
                    "raw_output": raw,
                    "parsed_output": parsed if parse_json else None,
                    "gate_ok": gate_ok,
                    "gate_message": gate_message,
                },
            )
    recovery_profile_name = profile_name
    for recovery_attempt in range(1, recovery_attempts + 1):
        support_guidance = _fetch_support_guidance(prompt)
        recovery_prompt = build_recovery_prompt(prompt, support_guidance=support_guidance)
        recovery_started_at = datetime.utcnow().isoformat()
        append_run_event(
            run_journal_path,
            "stage_recovery_start",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": recovery_profile_name,
                "attempt": recovery_attempt,
                "failure_reason": last_error,
                "recovery_mode": effective_recovery_mode,
                "support_profile": effective_support_profile,
            },
        )
        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_recovery_start",
            {
                "stage": stage_id,
                "attempt": recovery_attempt,
                "profile": recovery_profile_name,
                "failure_reason": last_error,
                "recovery_mode": effective_recovery_mode,
                "support_profile": effective_support_profile,
            },
        )
        try:
            raw = orchestrator.handle_request_with_overrides(
                recovery_prompt,
                profile_name=recovery_profile_name,
                stream_override=False,
            )
        except AgentStackError as e:
            last_error = f"[{e.code}] {e}"
            last_feedback = {"error": str(e), "error_code": e.code}
            append_run_event(
                run_journal_path,
                "stage_recovery_error",
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": recovery_profile_name,
                    "attempt": recovery_attempt,
                    "error": str(e),
                    "error_code": e.code,
                },
            )
            apply_quarantine_backoff(
                error_code=e.code,
                error_details=getattr(e, "details", {}),
                attempt_index=recovery_attempt,
                route_hint=(getattr(e, "details", {}) or {}).get("agent"),
                phase="recovery",
            )
            continue
        except Exception as e:
            wrapped_error = AgentUnexpectedError(
                f"Stage recovery failed unexpectedly: {e}",
                details={
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": recovery_profile_name,
                    "attempt": recovery_attempt,
                },
            )
            last_error = f"[{wrapped_error.code}] {wrapped_error}"
            last_feedback = {"error": str(wrapped_error), "error_code": wrapped_error.code}
            append_run_event(
                run_journal_path,
                "stage_recovery_error",
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": recovery_profile_name,
                    "attempt": recovery_attempt,
                    "error": str(wrapped_error),
                    "error_code": wrapped_error.code,
                },
            )
            continue

        recovery_completed_at = datetime.utcnow().isoformat()
        last_raw = raw
        parsed = parse_json_block(raw, fallback={}) if parse_json else raw
        if debug and diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": recovery_profile_name,
                    "attempt": recovery_attempt,
                    "event": "stage_recovery_payload",
                    "attempt_started_at": recovery_started_at,
                    "attempt_completed_at": recovery_completed_at,
                    "prompt": recovery_prompt,
                    "raw_output": raw,
                    "parsed_output": parsed if parse_json else None,
                },
            )
        gate_ok = True
        gate_message = "ok"
        if parse_json and output_schema:
            gate_ok, gate_message = validate_stage_payload(output_schema, parsed)
        if gate_fn:
            if gate_ok:
                gate_ok, gate_message = gate_fn(parsed)
        append_run_event(
            run_journal_path,
            "stage_recovery_result",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": recovery_profile_name,
                "attempt": recovery_attempt,
                "gate_ok": gate_ok,
                "gate_message": gate_message,
            },
        )
        if gate_ok:
            orchestrator.record_quality_gate_success(
                stage=stage_id,
                agent=agent_name,
                profile=recovery_profile_name,
                model=None,
                run_journal_path=str(run_journal_path) if run_journal_path else None,
                attempt=max_retries + recovery_attempt,
            )
            return finalize_success(
                payload=parsed,
                raw_output=raw,
                attempt=max_retries + recovery_attempt + 1,
                attempt_started_at=recovery_started_at,
                attempt_completed_at=recovery_completed_at,
                recovered=True,
                gate_message=gate_message,
                recovery_profile=recovery_profile_name,
            )
        last_error = gate_message
        orchestrator.record_quality_gate_failure(
            stage=stage_id,
            agent=agent_name,
            profile=recovery_profile_name,
            model=None,
            gate_message=gate_message,
            run_journal_path=str(run_journal_path) if run_journal_path else None,
            attempt=max_retries + recovery_attempt,
        )
        last_feedback = parsed

    lock_manager.log_agent_change(
        changes_log,
        agent_name,
        "stage_failure",
        {
            "stage": stage_id,
            "attempt": max_retries + recovery_attempts + 1,
            "gate_ok": False,
            "gate_message": last_error,
        },
    )
    append_run_event(
        run_journal_path,
        "stage_failure",
        {
            "stage": stage_id,
            "agent": agent_name,
            "profile": profile_name,
            "gate_message": last_error,
        },
    )
    update_cli_runtime_activity_from_context(
        context_store,
        {
            "source": "cli-book-flow",
            "state": "failed",
            "stage": stage_id,
            "agent": agent_name,
            "profile": profile_name,
            "last_error": last_error,
            "status_detail": f"stage failure: {stage_id}",
        },
    )
    if diagnostics_path is not None:
        append_jsonl(
            diagnostics_path,
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "event": "stage_failure",
                "attempt": max_retries + recovery_attempts + 1,
                "gate_ok": False,
                "gate_message": last_error,
            },
        )
    raise StageQualityGateError(
        f"{stage_id} failed quality gate after retries: {last_error}",
        details={"stage": stage_id, "agent": agent_name, "profile": profile_name, "last_error": last_error},
    )


def _is_canon_failover_trigger(error: StageQualityGateError) -> bool:
    """Return True when canon should fail over to alternate route/model profile."""
    message = str(error)
    trigger_codes = (
        "AGENT_QUARANTINED",
        "OLLAMA_EMPTY_RESPONSE",
        "OLLAMA_REQUEST_ERROR",
        "OLLAMA_ENDPOINT_ERROR",
    )
    return any(code in message for code in trigger_codes)


def _extract_stage_error_code(error: StageQualityGateError) -> str:
    """Best-effort extraction of upstream error code embedded in stage failure message."""
    message = str(error)
    known_codes = (
        "AGENT_QUARANTINED",
        "OLLAMA_EMPTY_RESPONSE",
        "OLLAMA_REQUEST_ERROR",
        "OLLAMA_ENDPOINT_ERROR",
    )
    for code in known_codes:
        if code in message:
            return code
    return "STAGE_QUALITY_GATE_ERROR"


def run_flow(args):
    import traceback
    from datetime import datetime
    orchestrator = OrchestratorAgent()
    lock_manager = AgentLockManager()
    # Parent-level log: start
    parent_log = {
        "timestamp": datetime.utcnow().isoformat(),
        "agent": "book-flow-parent",
        "action": "run_start",
        "details": {
            "input_title": getattr(args, 'title', None),
            "input_chapter": getattr(args, 'chapter_title', None),
            "input_section": getattr(args, 'section_title', None),
            "input_goal": getattr(args, 'section_goal', None),
        },
    }
    output_root = Path(args.output_dir).expanduser()
    book_slug = slugify(args.title) if hasattr(args, "title") else "book-error"
    book_root = output_root / book_slug
    runs_root = book_root / "runs"
    ensure_dir(runs_root)
    framework_root = book_root / "framework"
    ensure_dir(framework_root)

    framework_skeleton_path = framework_root / "framework_skeleton.json"
    arc_tracker_path = framework_root / "arc_tracker.json"
    progress_index_path = framework_root / "progress_index.json"
    agent_context_status_path = framework_root / "agent_context_status.jsonl"

    if not framework_skeleton_path.exists():
        write_json(
            framework_skeleton_path,
            {
                "book_identity": {
                    "title_working": args.title,
                    "genre": args.genre,
                    "audience": args.audience,
                    "tone": args.tone,
                    "target_word_count": args.target_word_count,
                    "page_target": args.page_target,
                },
                "design_framework": {
                    "constraints": [],
                    "acceptance_criteria": [],
                    "book_structure": {},
                    "master_outline_markdown": "",
                },
                "chapter_skeleton": {
                    "chapter_number": args.chapter_number,
                    "chapter_title": args.chapter_title,
                    "purpose": args.section_goal,
                    "ending_hook": "",
                    "target_words": args.writer_words,
                    "sections": [],
                    "must_include": [],
                    "must_avoid": [],
                },
                "generated_at": datetime.utcnow().isoformat(),
            },
        )
    if not arc_tracker_path.exists():
        write_json(
            arc_tracker_path,
            {
                "story_arcs": [],
                "character_arcs": [],
                "open_loops": [],
                "chapter_progress": [],
                "last_updated": datetime.utcnow().isoformat(),
            },
        )
    if not progress_index_path.exists():
        write_json(
            progress_index_path,
            {
                "book": {"title": args.title},
                "completed_chapters": [],
                "last_run": None,
                "last_updated": datetime.utcnow().isoformat(),
            },
        )

    # Stable per-book history log for all requests tied to the same title.
    book_history_log = book_root / "book_history.jsonl"
    append_jsonl(book_history_log, parent_log)

    run_history_root = book_root / "run_history"
    cleanup_summary = archive_and_prune_old_runs(runs_root, run_history_root)
    cleanup_summary["history_root"] = str(run_history_root)
    append_jsonl(
        book_history_log,
        {
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "book-flow-parent",
            "action": "pre_run_cleanup",
            "details": cleanup_summary,
        },
    )

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    run_name = f"{stamp}-ch{args.chapter_number:02d}-{slugify(args.section_title)}"
    run_dir = runs_root / run_name

    dirs = {
        "brief": run_dir / "00_brief",
        "research": run_dir / "01_research",
        "outline": run_dir / "02_outline",
        "chapter_specs": run_dir / "02_outline/chapter_specs",
        "canon": run_dir / "03_canon",
        "drafts_ch": run_dir / "04_drafts/chapter_01",
        "reviews": run_dir / "05_reviews",
        "section_reviews": run_dir / "05_reviews/section_reviews",
        "assembly_reviews": run_dir / "05_reviews/assembly_reviews",
        "final": run_dir / "06_final",
        "handoff": run_dir / "handoff",
        "diagnostics": run_dir / "diagnostics",
    }
    for path in dirs.values():
        ensure_dir(path)

    changes_log = run_dir / "changes.log"
    diagnostics_always = str(os.environ.get("BOOK_FLOW_DIAGNOSTICS_ALWAYS", "true")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    diagnostics_log = (dirs["diagnostics"] / "agent_diagnostics.jsonl") if (args.verbose or diagnostics_always) else None
    debug_env = str(os.environ.get("BOOK_FLOW_DEBUG", "true")).lower()
    debug_mode = bool(getattr(args, "debug", False))
    if debug_env in {"0", "false", "no", "off"}:
        debug_mode = False
    elif debug_env in {"1", "true", "yes", "on"}:
        debug_mode = True

    if getattr(args, "no_debug", False):
        debug_mode = False
    if not changes_log.exists():
        write_text(changes_log, "")

    context_store = {
        "book": {
            "title": args.title,
            "genre": args.genre,
            "audience": args.audience,
            "tone": args.tone,
            "premise": args.premise,
            "target_word_count": args.target_word_count,
            "page_target": args.page_target,
            "pen_name": getattr(args, "pen_name", "DaRaVeNrK"),
            "publisher_name": getattr(args, "publisher_name", "DaRaVeNrK LLC"),
        },
        "chapter": {
            "number": args.chapter_number,
            "title": args.chapter_title,
            "section_title": args.section_title,
            "section_goal": args.section_goal,
            "writer_words": args.writer_words,
        },
    }
    strategy_version = str(getattr(args, "strategy_version", "")).strip() or DEFAULT_STRATEGY_VERSION
    context_store["_strategy_version"] = strategy_version
    cli_activity_path = Path(
        str(
            getattr(
                args,
                "cli_activity_path",
                "/home/daravenrk/dragonlair/book_project/cli_runtime_activity.json",
            )
        )
    )
    context_store["_cli_run_id"] = run_name
    context_store["_cli_activity_path"] = str(cli_activity_path)
    context_store["_handoff_dir"] = str(dirs["handoff"])
    context_store["_task_id"] = str(getattr(args, "task_id", "") or "") or None
    run_journal = run_dir / "run_journal.jsonl"
    quality_threshold_snapshot = configure_effective_quality_thresholds(book_root)
    context_store["_run_journal_path"] = str(run_journal)
    write_review_gate_state(
        run_dir,
        {
            "status": "idle",
            "task_id": context_store.get("_task_id"),
            "run_id": run_name,
            "run_dir": str(run_dir),
            "title": args.title,
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
        },
    )
    append_run_event(
        run_journal,
        "run_cleanup_complete",
        cleanup_summary,
    )
    append_run_event(
        run_journal,
        "quality_thresholds_loaded",
        quality_threshold_snapshot,
    )
    append_run_event(
        run_journal,
        "run_start",
        {
            "title": args.title,
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "strategy_version": strategy_version,
        },
    )
    update_cli_runtime_activity(
        cli_activity_path,
        run_name,
        {
            "source": "cli-book-flow",
            "state": "running",
            "title": args.title,
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "run_dir": str(run_dir),
            "started_at_epoch": time.time(),
            "status_detail": "run started",
        },
    )

    resource_tracker_path = Path(
        str(
            getattr(
                args,
                "resource_tracker_path",
                "/home/daravenrk/dragonlair/book_project/resource_tracker.json",
            )
        )
    )
    resource_events_path = Path(
        str(
            getattr(
                args,
                "resource_events_path",
                "/home/daravenrk/dragonlair/book_project/resource_events.jsonl",
            )
        )
    )
    ui_state_path = Path("/home/daravenrk/dragonlair/book_project/webui_state.json")
    ui_events_path = Path("/home/daravenrk/dragonlair/book_project/webui_events.jsonl")
    context_store["_resource_refs"] = {
        "resource_tracker": str(resource_tracker_path),
        "resource_events": str(resource_events_path),
        "ui_state": str(ui_state_path),
        "ui_events": str(ui_events_path),
    }
    context_store["_framework_refs"] = {
        "framework_skeleton": str(framework_skeleton_path),
        "arc_tracker": str(arc_tracker_path),
        "progress_index": str(progress_index_path),
        "agent_context_status": str(agent_context_status_path),
    }
    context_store["_resource_snapshot"] = read_json(resource_tracker_path, default={})
    write_json(dirs["handoff"] / "resource_references.json", {
        "refs": context_store["_resource_refs"],
        "snapshot": context_store["_resource_snapshot"],
    })

    # 1) Publisher brief
    # R1 guardrail: keep publisher brief inputs bounded so long-run context
    # accumulation cannot overflow the stage context window.
    chapter_input = context_store.get("chapter") if isinstance(context_store.get("chapter"), dict) else {}
    chapter_input = chapter_input if isinstance(chapter_input, dict) else {}
    previous_brief = (((context_store.get("permanent_memory") or {}).get("book_brief") or {}))
    previous_brief = previous_brief if isinstance(previous_brief, dict) else {}
    rolling_memory = _normalize_list(context_store.get("rolling_memory"))
    recent_memory = [str(item)[:280] for item in rolling_memory[-5:]]
    publisher_inputs = {
        "book_request": {
            "title": args.title,
            "genre": args.genre,
            "audience": args.audience,
            "premise": args.premise,
            "target_word_count": args.target_word_count,
            "page_target": args.page_target,
            "tone": args.tone,
        },
        "chapter": {
            "chapter_number": chapter_input.get("chapter_number", args.chapter_number),
            "chapter_title": chapter_input.get("chapter_title", args.chapter_title),
            "section_title": chapter_input.get("section_title", args.section_title),
        },
        "previous_brief_snapshot": {
            "title_working": previous_brief.get("title_working"),
            "genre": previous_brief.get("genre"),
            "audience": previous_brief.get("audience"),
            "tone": previous_brief.get("tone"),
            "constraints": _normalize_list(previous_brief.get("constraints"))[:6],
            "acceptance_criteria": _normalize_list(previous_brief.get("acceptance_criteria"))[:6],
        },
        "recent_memory_excerpt": recent_memory,
    }

    publisher_contract = build_contract(
        role="Publisher / Executive Agent",
        objective="Define book brief and stage acceptance criteria for this run.",
        constraints=[
            "Return JSON only",
            "Include title_working, target_word_count, page_target, constraints, acceptance_criteria",
            "No prose outside JSON",
        ],
        inputs=publisher_inputs,
        output_format='JSON object with fields: title_working, genre, audience, target_word_count, page_target, tone, constraints, acceptance_criteria',
        failure_conditions=["missing fields", "invalid JSON", "vague acceptance criteria"],
    )
    # --- Publisher Brief with Title Generation and User Selection ---
    brief = None
    required_fields = [
        "title_working", "genre", "audience", "target_word_count", "page_target", "tone", "constraints", "acceptance_criteria"
    ]

    def missing_publisher_fields(payload):
        if not isinstance(payload, dict):
            return list(required_fields)
        missing = [field for field in required_fields if not payload.get(field)]
        constraints = payload.get("constraints")
        acceptance = payload.get("acceptance_criteria")
        if not isinstance(constraints, list) or len(constraints) < 5:
            if "constraints" not in missing:
                missing.append("constraints")
        if not isinstance(acceptance, list) or len(acceptance) < 5:
            if "acceptance_criteria" not in missing:
                missing.append("acceptance_criteria")
        return missing

    for attempt in range(args.max_retries + 1):
        brief = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id="publisher_brief",
            agent_name="book-publisher-brief",
            profile_name=args.publisher_brief_profile,
            prompt=publisher_contract,
            output_path=dirs["brief"] / "book_brief.json",
            parse_json=True,
            output_schema=None,
            gate_fn=None,
            max_retries=1,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
        missing = missing_publisher_fields(brief)
        # Fallback: always set title_working if missing, before breaking
        if "title_working" in missing:
            brief["title_working"] = args.title
            missing = missing_publisher_fields(brief)
        # Harden: after all attempts, always set before gate
        if attempt == args.max_retries and not brief.get("title_working"):
            brief["title_working"] = args.title
        # Debug: print brief before gate
        print("[DEBUG] publisher_brief fields just before gate:", json.dumps(brief, indent=2))
        missing = missing_publisher_fields(brief)
        if not missing:
            break
        # Try to fill missing fields from user input or model
        correction_path = Path("/home/daravenrk/dragonlair/book_project/webui_correction.json")
        user_data = {}
        if correction_path.exists():
            try:
                with open(correction_path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                user_data = {}
        for field in missing:
            # 1. User input
            if user_data.get(field):
                brief[field] = user_data[field]
                continue
            # 2. Model suggestion
            if field == "title_working":
                # Already set above, skip
                continue
            elif field == "genre":
                brief[field] = args.genre
            elif field == "audience":
                brief[field] = args.audience
            elif field == "target_word_count":
                brief[field] = args.target_word_count
            elif field == "page_target":
                brief[field] = args.page_target
            elif field == "tone":
                brief[field] = args.tone
            elif field == "constraints":
                # Model suggestion for constraints
                constraints_prompt = f"Suggest 5 concrete constraints for a book with the following premise and genre.\nPremise: {args.premise}\nGenre: {args.genre}\nReturn a JSON array of strings."
                try:
                    constraints_response = orchestrator.handle_request_with_overrides(
                        constraints_prompt,
                        profile_name=args.publisher_brief_profile,
                        stream_override=False,
                    )
                    constraints = json.loads(constraints_response) if isinstance(constraints_response, str) else []
                    if constraints:
                        brief[field] = constraints
                        continue
                except (AgentStackError, json.JSONDecodeError, TypeError, ValueError):
                    pass
                brief[field] = ["Must be original", "No plagiarism", "Consistent tone", "Meets genre expectations", "Engages target audience"]
            elif field == "acceptance_criteria":
                criteria_prompt = f"Suggest 5 measurable acceptance criteria for a book with the following premise and genre.\nPremise: {args.premise}\nGenre: {args.genre}\nReturn a JSON array of strings."
                try:
                    criteria_response = orchestrator.handle_request_with_overrides(
                        criteria_prompt,
                        profile_name=args.publisher_brief_profile,
                        stream_override=False,
                    )
                    criteria = json.loads(criteria_response) if isinstance(criteria_response, str) else []
                    if criteria:
                        brief[field] = criteria
                        continue
                except (AgentStackError, json.JSONDecodeError, TypeError, ValueError):
                    pass
                brief[field] = ["All sections present", "No major plot holes", "Meets word count", "Adheres to brief", "Passes editorial review"]

        missing = missing_publisher_fields(brief)
        if not missing:
            break
    context_store["permanent_memory"] = {"book_brief": brief}
    write_json(dirs["canon"] / "context_store.json", context_store)
    write_agent_context_status(
        agent_context_status_path,
        {
            "phase": "publisher_brief_complete",
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "expectations": {
                "constraints": _normalize_list(brief.get("constraints")),
                "acceptance_criteria": _normalize_list(brief.get("acceptance_criteria")),
            },
        },
    )

    # 2) Research
    research_bootstrap = bootstrap_simple_research_packets(
        brief=brief,
        chapter=context_store.get("chapter") or {},
        premise=str(args.premise),
    )
    research_source_packets = research_bootstrap.get("packets") if isinstance(research_bootstrap, dict) else []
    research_source_packets = research_source_packets if isinstance(research_source_packets, list) else []
    write_json(dirs["research"] / "source_packets.json", research_bootstrap)
    source_packets_markdown = render_research_source_packets_markdown(research_source_packets)
    append_run_event(
        run_journal,
        "research_bootstrap_complete",
        {
            "stage": "research",
            "query_terms": research_bootstrap.get("query_terms") if isinstance(research_bootstrap, dict) else [],
            "source_packet_count": len(research_source_packets),
            "source_packets_path": str(dirs["research"] / "source_packets.json"),
        },
    )

    research_contract = build_contract(
        role="Research Agent",
        objective="Produce research dossier and facts for chapter drafting.",
        constraints=[
            "Return markdown only",
            "Include source caveats",
            "Separate facts from assumptions",
            "Use source packet ids (for example [dictionary:term]) when grounding factual claims",
        ],
        inputs={
            "book_brief": brief,
            "chapter": context_store["chapter"],
            "source_packets": research_source_packets,
        },
        output_format="Markdown with headings: Overview, Facts, Worldbuilding Notes, Source Caveats, Do-Not-Claim-Without-Review",
        failure_conditions=["missing facts", "no caveats", "not actionable"],
    )
    if source_packets_markdown:
        research_contract = f"{research_contract}\n\n{source_packets_markdown}"
    try:
        research_md = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id="research",
            agent_name="book-researcher",
            profile_name="book-researcher",
            prompt=research_contract,
            output_path=dirs["research"] / "research_dossier.md",
            parse_json=False,
            gate_fn=gate_research_dossier,
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
    except StageQualityGateError as err:
        if "research output empty" not in str(err):
            raise
        research_md = build_fallback_research_dossier(
            brief=brief,
            chapter=context_store.get("chapter") or {},
            premise=str(args.premise),
            source_packets=research_source_packets,
        )
        write_text(dirs["research"] / "research_dossier.md", research_md)
        context_store["research"] = {
            "agent": "book-researcher",
            "profile": "book-researcher",
            "output_path": str(dirs["research"] / "research_dossier.md"),
            "fallback": True,
            "fallback_reason": "research output empty",
        }
        append_run_event(
            run_journal,
            "stage_fallback_applied",
            {
                "stage": "research",
                "agent": "book-researcher",
                "profile": "book-researcher",
                "reason": "research output empty",
                "fallback_type": "operator_generated_research_dossier",
                "output_path": str(dirs["research"] / "research_dossier.md"),
            },
        )

    # 3) Architect
    architect_contract = build_contract(
        role="Concept Architect Agent",
        objective="Create master outline and book structure.",
        constraints=[
            "Return JSON only",
            "Include top-level keys master_outline_markdown, book_structure, pacing_notes",
            "master_outline_markdown must be non-empty markdown text",
            "book_structure must be an object or array",
            "Do not wrap output in markdown code fences",
        ],
        inputs={"book_brief": brief, "research_dossier": research_md},
        output_format=(
            'JSON object with keys: master_outline_markdown, book_structure, pacing_notes. '
            'Example: {"master_outline_markdown":"# Outline...","book_structure":{"acts":[]},"pacing_notes":"..."}'
        ),
        failure_conditions=["missing master outline", "missing structure", "invalid JSON"],
    )
    try:
        outline_payload = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id="architect_outline",
            agent_name="book-architect",
            profile_name="book-architect",
            prompt=architect_contract,
            output_path=dirs["outline"] / "book_structure.json",
            parse_json=True,
            output_schema="architect_outline",
            gate_fn=gate_architect_outline,
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
    except StageQualityGateError as err:
        if "master_outline_markdown" not in str(err):
            raise
        outline_payload = build_fallback_architect_outline(
            brief=brief,
            chapter=context_store.get("chapter") or {},
            research_md=str(research_md),
        )
        write_json(dirs["outline"] / "book_structure.json", outline_payload)
        context_store["architect_outline"] = {
            "agent": "book-architect",
            "profile": "book-architect",
            "output_path": str(dirs["outline"] / "book_structure.json"),
            "fallback": True,
            "fallback_reason": "payload missing required field 'master_outline_markdown'",
        }
        append_run_event(
            run_journal,
            "stage_fallback_applied",
            {
                "stage": "architect_outline",
                "agent": "book-architect",
                "profile": "book-architect",
                "reason": "payload missing required field 'master_outline_markdown'",
                "fallback_type": "operator_generated_architect_outline",
                "output_path": str(dirs["outline"] / "book_structure.json"),
            },
        )
    write_text(dirs["outline"] / "master_outline.md", str(outline_payload.get("master_outline_markdown", "")))

    # 4) Chapter planner
    planner_contract = build_contract(
        role="Chapter Planner Agent",
        objective="Generate a bounded chapter specification for the requested chapter.",
        constraints=["Return JSON only", "Include sections array", "Include must_include/must_avoid and ending_hook"],
        inputs={"book_brief": brief, "book_structure": outline_payload, "chapter": context_store["chapter"]},
        output_format='JSON with keys: chapter_number, chapter_title, purpose, target_words, sections, must_include, must_avoid, ending_hook',
        failure_conditions=["vague sections", "missing ending hook", "invalid JSON"],
    )
    try:
        chapter_spec = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id="chapter_planner",
            agent_name="book-chapter-planner",
            profile_name="book-chapter-planner",
            prompt=planner_contract,
            output_path=dirs["chapter_specs"] / "chapter_01.json",
            parse_json=True,
            output_schema="chapter_planner",
            gate_fn=gate_chapter_spec,
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
    except StageQualityGateError as err:
        if "chapter_number" not in str(err):
            raise
        chapter_spec = build_fallback_chapter_spec(
            brief=brief,
            chapter=context_store.get("chapter") or {},
            outline_payload=outline_payload,
        )
        write_json(dirs["chapter_specs"] / "chapter_01.json", chapter_spec)
        context_store["chapter_planner"] = {
            "agent": "book-chapter-planner",
            "profile": "book-chapter-planner",
            "output_path": str(dirs["chapter_specs"] / "chapter_01.json"),
            "fallback": True,
            "fallback_reason": "payload missing required field 'chapter_number'",
        }
        append_run_event(
            run_journal,
            "stage_fallback_applied",
            {
                "stage": "chapter_planner",
                "agent": "book-chapter-planner",
                "profile": "book-chapter-planner",
                "reason": "payload missing required field 'chapter_number'",
                "fallback_type": "operator_generated_chapter_spec",
                "output_path": str(dirs["chapter_specs"] / "chapter_01.json"),
            },
        )

    framework_skeleton = build_framework_skeleton(
        brief=brief,
        outline_payload=outline_payload,
        chapter_spec=chapter_spec,
        chapter_number=args.chapter_number,
    )
    write_json(framework_skeleton_path, framework_skeleton)
    write_agent_context_status(
        agent_context_status_path,
        {
            "phase": "framework_skeleton_updated",
            "chapter_number": args.chapter_number,
            "chapter_title": chapter_spec.get("chapter_title"),
            "section_title": args.section_title,
            "expectations": {
                "must_include": _normalize_list(chapter_spec.get("must_include")),
                "must_avoid": _normalize_list(chapter_spec.get("must_avoid")),
                "ending_hook": chapter_spec.get("ending_hook"),
            },
        },
    )

    # Framework integrity gate — block draft progression if skeleton/arc/progress are malformed
    _arc_snapshot = read_json(arc_tracker_path, default={})
    _progress_snapshot = read_json(progress_index_path, default={})
    check_framework_integrity(framework_skeleton, _arc_snapshot, _progress_snapshot)
    append_run_event(
        run_journal,
        "framework_integrity_passed",
        {
            "chapter_number": args.chapter_number,
            "chapter_title": chapter_spec.get("chapter_title"),
            "skeleton_sections": len((framework_skeleton.get("chapter_skeleton") or {}).get("sections") or []),
        },
    )

    # 5) Canon manager
    canon_contract = build_contract(
        role="Canon / Memory Agent",
        objective="Initialize and persist canon for this chapter run.",
        constraints=["Return JSON only", "Include canon, timeline, character_bible, open_loops, style_guide"],
        inputs={
            "book_premise": context_store.get("book", {}).get("premise", ""),
            "book_details": {
                "title": context_store.get("book", {}).get("title", ""),
                "genre": context_store.get("book", {}).get("genre", ""),
                "tone": context_store.get("book", {}).get("tone", ""),
                "audience": context_store.get("book", {}).get("audience", ""),
            },
            "book_brief": brief,
            "chapter_spec": chapter_spec,
            "rolling_context": context_store.get("rolling_memory", {}),
        },
        output_format='JSON with keys: canon, timeline, character_bible, open_loops, style_guide',
        failure_conditions=["missing canon", "missing timeline", "invalid JSON"],
    )
    canon_fallback_reason = None
    try:
        canon_payload = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id="canon",
            agent_name="book-canon",
            profile_name="book-canon",
            prompt=canon_contract,
            output_path=dirs["canon"] / "canon.json",
            parse_json=True,
            output_schema="canon",
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
    except StageQualityGateError as err:
        if _is_canon_failover_trigger(err):
            trigger_code = _extract_stage_error_code(err)
            append_run_event(
                run_journal,
                "stage_route_failover",
                {
                    "stage": "canon",
                    "agent": "book-canon",
                    "from_profile": "book-canon",
                    "to_profile": "book-canon-nvidia",
                    "from_route": "ollama_amd",
                    "to_route": "ollama_nvidia",
                    "trigger_error_code": trigger_code,
                    "trigger_error": str(err),
                },
            )
            try:
                canon_payload = run_stage(
                    orchestrator=orchestrator,
                    lock_manager=lock_manager,
                    changes_log=changes_log,
                    context_store=context_store,
                    stage_id="canon",
                    agent_name="book-canon",
                    profile_name="book-canon-nvidia",
                    prompt=canon_contract,
                    output_path=dirs["canon"] / "canon.json",
                    parse_json=True,
                    output_schema="canon",
                    max_retries=args.max_retries,
                    diagnostics_path=diagnostics_log,
                    verbose=args.verbose,
                    debug=debug_mode,
                )
            except StageQualityGateError as nvidia_err:
                canon_fallback_reason = str(nvidia_err)
        else:
            canon_fallback_reason = str(err)

    if canon_fallback_reason:
        canon_payload = build_fallback_canon_payload(
            brief=brief,
            chapter=context_store.get("chapter") or {},
            chapter_spec=chapter_spec,
            outline_payload=outline_payload,
        )
        fallback_contract_report = validate_fallback_canon_contract(
            canon_payload,
            brief=brief,
            chapter=context_store.get("chapter") or {},
            chapter_spec=chapter_spec,
        )
        write_json(dirs["canon"] / "fallback_contract_report.json", fallback_contract_report)
        append_run_event(
            run_journal,
            "stage_fallback_contract_report",
            {
                "stage": "canon",
                "contract": fallback_contract_report.get("contract"),
                "all_passed": bool(fallback_contract_report.get("all_passed")),
                "missing": fallback_contract_report.get("missing") or [],
                "report_path": str(dirs["canon"] / "fallback_contract_report.json"),
            },
        )
        if not fallback_contract_report.get("all_passed"):
            raise FrameworkIntegrityError(
                "Deterministic canon fallback parity contract failed.",
                details={
                    "stage": "canon",
                    "missing": fallback_contract_report.get("missing") or [],
                    "report_path": str(dirs["canon"] / "fallback_contract_report.json"),
                },
            )
        source_materials = {
            "book_brief": brief,
            "outline_payload": outline_payload,
            "chapter_spec": chapter_spec,
            "chapter_context": context_store.get("chapter") or {},
        }
        source_hashes = {name: payload_sha256(value) for name, value in source_materials.items()}
        fallback_payload_checksum = payload_sha256(canon_payload)
        write_json(dirs["canon"] / "canon.json", canon_payload)
        write_json(
            dirs["canon"] / "canon_fallback_metadata.json",
            {
                "fallback": True,
                "fallback_type": "deterministic_canon_bootstrap",
                "stage": "canon",
                "profile": "book-canon",
                "reason": canon_fallback_reason,
                "generated_at": datetime.utcnow().isoformat(),
                "fallback_payload_checksum": fallback_payload_checksum,
                "source_input_hashes": source_hashes,
                "source_input_paths": {
                    "book_brief": str(dirs["brief"] / "book_brief.json"),
                    "outline_payload": str(dirs["outline"] / "book_structure.json"),
                    "chapter_spec": str(dirs["chapter_specs"] / "chapter_01.json"),
                    "fallback_contract_report": str(dirs["canon"] / "fallback_contract_report.json"),
                },
            },
        )
        context_store["canon"] = {
            "agent": "book-canon",
            "profile": "book-canon",
            "output_path": str(dirs["canon"] / "canon.json"),
            "fallback": True,
            "fallback_reason": canon_fallback_reason,
            "fallback_artifact": str(dirs["canon"] / "canon_fallback_metadata.json"),
            "fallback_contract_report": str(dirs["canon"] / "fallback_contract_report.json"),
            "fallback_payload_checksum": fallback_payload_checksum,
        }
        append_run_event(
            run_journal,
            "stage_fallback_applied",
            {
                "stage": "canon",
                "agent": "book-canon",
                "profile": "book-canon",
                "reason": canon_fallback_reason,
                "fallback_type": "deterministic_canon_bootstrap",
                "output_path": str(dirs["canon"] / "canon.json"),
                "fallback_artifact": str(dirs["canon"] / "canon_fallback_metadata.json"),
                "fallback_contract_report": str(dirs["canon"] / "fallback_contract_report.json"),
                "fallback_payload_checksum": fallback_payload_checksum,
                "source_input_hashes": source_hashes,
            },
        )
    write_json(dirs["canon"] / "timeline.json", canon_payload.get("timeline", {}))
    write_json(dirs["canon"] / "character_bible.json", canon_payload.get("character_bible", {}))
    write_json(dirs["canon"] / "open_loops.json", canon_payload.get("open_loops", []))
    write_text(dirs["canon"] / "style_guide.md", str(canon_payload.get("style_guide", "")))
    context_tracking_strategy = derive_context_tracking_strategy(context_store.get("book", {}), canon_payload, chapter_spec)

    # --- Writing Assistant Worldbuilding Integration ---
    wa_context = {
        "genre": brief.get("genre", ""),
        "setting": canon_payload.get("canon", {}).get("setting", ""),
        "era": canon_payload.get("canon", {}).get("era", ""),
        "notes": brief.get("premise", ""),
        "needs": chapter_spec.get("purpose", ""),
        "themes": chapter_spec.get("purpose", ""),
        "history": canon_payload.get("timeline", {}),
        "focus": chapter_spec.get("purpose", ""),
        "diversity": "broad, global, inclusive",
    }
    try:
        names_md = generate_names(wa_context)
        technology_md = generate_technology(wa_context)
        personalities_md = generate_personalities(wa_context)
        dates_md = generate_dates_history(wa_context)
    except Exception as err:
        # Non-critical enrichment should never block chapter production.
        append_run_event(
            run_journal,
            "stage_warning",
            {
                "stage": "worldbuilding_enrichment",
                "agent": "writing-assistant",
                "warning": str(err),
                "fallback": "empty_worldbuilding_artifacts",
            },
        )
        names_md = "# Names\n\n(worldbuilding enrichment unavailable in this run)\n"
        technology_md = "# Technology\n\n(worldbuilding enrichment unavailable in this run)\n"
        personalities_md = "# Personalities\n\n(worldbuilding enrichment unavailable in this run)\n"
        dates_md = "# History\n\n(worldbuilding enrichment unavailable in this run)\n"
    # Write outputs to book_project/
    book_project_root = Path(args.output_dir)
    write_text(book_project_root / "names.md", names_md)
    write_text(book_project_root / "technology.md", technology_md)
    write_text(book_project_root / "personalities.md", personalities_md)
    write_text(book_project_root / "history.md", dates_md)

    # 6) Writer + section consistency review per section
    chapter_sections = chapter_spec.get("sections") or []
    if len(chapter_sections) < 2:
        raise ChapterSpecValidationError(
            "Chapter spec must provide at least 2 sections for sequencing/assembly consistency checks",
            details={"section_count": len(chapter_sections)},
        )

    section_texts = []
    section_summaries = []
    continuity_state = parse_json_block(
        read_text(dirs["canon"] / "continuity_state.json", "{}"),
        fallback={},
    )
    previous_next_writer_notes = parse_json_block(
        read_text(dirs["canon"] / "next_writer_notes.json", "{}"),
        fallback={},
    )
    consistency_sections = build_section_consistency_sections(
        chapter_number=args.chapter_number,
        chapter_title=args.chapter_title,
        chapter_spec=chapter_spec,
        canon_payload=canon_payload,
        previous_next_writer_notes=previous_next_writer_notes,
    )
    continuity_state["consistency_sections"] = consistency_sections
    write_json(dirs["canon"] / "consistency_sections.json", consistency_sections)
    write_json(dirs["canon"] / "continuity_state.json", continuity_state)

    # -- Law context + living skeleton frame for writer guidance --
    # Canonical law (Tier 1) comes from immutable past chapter canonical records.
    # Writers MUST respect every law_item and continuity_constraint listed there.
    # The living skeleton frame (Tier 2) provides the current prediction for this
    # chapter so the writer knows what they are expected to accomplish next.
    _law_context_block = load_law_context(framework_root, for_chapter_number=args.chapter_number)
    _skeleton_frame_for_writer = get_future_frame(framework_root, args.chapter_number)

    for idx, section_item in enumerate(chapter_sections, start=1):
        section_title = ""
        section_goal = ""
        if isinstance(section_item, dict):
            section_title = str(section_item.get("title") or section_item.get("name") or f"Section {idx}")
            section_goal = str(section_item.get("objective") or section_item.get("goal") or "Advance chapter objective")
        else:
            section_title = str(section_item)
            section_goal = "Advance chapter objective"

        relevant_notes_packet = build_relevant_chapter_notes(
            context_store.get("rolling_memory", {}),
            args.chapter_number,
            section_goal,
            max_items=6,
        )
        # Cap recent_chapter_summaries to last 5 — prevents context window overflow as
        # rolling_memory grows across a full multi-chapter book run.
        _all_summaries = context_store.get("rolling_memory", {}).get("chapter_summaries", [])
        _recent_summaries = _all_summaries[-5:] if len(_all_summaries) > 5 else _all_summaries
        current_checkpoint = {}
        checkpoint_sections = consistency_sections.get("sections") if isinstance(consistency_sections.get("sections"), list) else []
        if (idx - 1) < len(checkpoint_sections):
            current_checkpoint = checkpoint_sections[idx - 1]
        section_output_path = dirs["drafts_ch"] / f"section_{idx:02d}.md"
        section_review_path = dirs["section_reviews"] / f"section_{idx:02d}_review.json"
        rewrite_feedback = None
        rewrite_cycles = 0

        while True:
            local_task_memory = {
                "chapter_spec": chapter_spec,
                "canon": canon_payload,
                "recent_chapter_summaries": _recent_summaries,
                "previous_next_writer_notes": previous_next_writer_notes,
                "previous_section_summaries": section_summaries,
                "continuity_state": continuity_state,
                "consistency_checkpoint": current_checkpoint,
                "consistency_tracker": {
                    "active_section_index": consistency_sections.get("active_section_index"),
                    "total_sections": len(checkpoint_sections),
                    "ledger_size": len(consistency_sections.get("ledger") or []),
                },
                "context_tracking_strategy": context_tracking_strategy,
                "relevant_notes": relevant_notes_packet,
                "section_title": section_title,
                "section_goal": section_goal,
                # Two-tier doc system: past law (immutable) + current skeleton frame (predictive)
                "canonical_law": _law_context_block or "(no prior accepted chapters — first chapter run)",
                "skeleton_guidance_frame": _skeleton_frame_for_writer or "(no skeleton frame — run skeleton_flow first)",
            }
            _writer_constraints = [
                "Return markdown only",
                f"Target words around {args.writer_words}",
                "Respect canon and style guide",
                "Use chapter notes only if relevant to this section goal",
                "Satisfy consistency_checkpoint expectations and situations for this section",
                "Follow provided context tracking strategy for continuity",
            ]
            if _law_context_block:
                _writer_constraints.insert(0,
                    "CRITICAL: Respect every item in canonical_law — these are locked facts from "
                    "accepted chapters. Contradicting any law_item or continuity_constraint is an "
                    "automatic quality gate failure."
                )
            if rewrite_feedback:
                local_task_memory["human_review_feedback"] = {
                    "comment": rewrite_feedback.get("comment"),
                    "issue_tags": rewrite_feedback.get("issue_tags") or [],
                    "rewrite_scope": rewrite_feedback.get("rewrite_scope"),
                    "note": rewrite_feedback.get("note"),
                }
                _writer_constraints.append(
                    "Incorporate human_review_feedback while preserving canon, accepted downstream facts, and future story requirements."
                )
            writer_contract = build_contract(
                role="Section Writer Agent",
                objective=f"Draft section {idx:02d} for the chapter.",
                constraints=_writer_constraints,
                inputs=local_task_memory,
                output_format="Markdown with heading, coherent section body, and transition sentence",
                failure_conditions=["canon contradiction", "missing transition", "off-topic section"],
            )
            section_text = run_stage(
                orchestrator=orchestrator,
                lock_manager=lock_manager,
                changes_log=changes_log,
                context_store=context_store,
                stage_id=f"writer_section_{idx:02d}",
                agent_name="book-writer",
                profile_name=args.writer_profile,
                prompt=writer_contract,
                output_path=section_output_path,
                parse_json=False,
                gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.6), "draft too short"),
                max_retries=args.max_retries,
                diagnostics_path=diagnostics_log,
                verbose=args.verbose,
                debug=debug_mode,
            )

            section_review_contract = build_contract(
                role="Section Continuity Reviewer",
                objective="Review drafted section for continuity/timeline/character-state contradictions before assembly.",
                constraints=[
                    "Return JSON only",
                    "Include blocking_issues, warnings, section_summary, continuity_state_updates",
                    "Treat unmet consistency checkpoint expectations as blocking_issues",
                ],
                inputs={
                    "section_title": section_title,
                    "section_goal": section_goal,
                    "section_text": section_text,
                    "canon": canon_payload,
                    "previous_section_summaries": section_summaries,
                    "consistency_checkpoint": current_checkpoint,
                    "consistency_tracker": consistency_sections,
                    "human_review_feedback": rewrite_feedback or {},
                },
                output_format='JSON with keys: blocking_issues, warnings, section_summary, continuity_state_updates',
                failure_conditions=["invalid JSON", "missing summary", "undetected major contradiction"],
            )
            section_review = run_stage(
                orchestrator=orchestrator,
                lock_manager=lock_manager,
                changes_log=changes_log,
                context_store=context_store,
                stage_id=f"section_review_{idx:02d}",
                agent_name="book-continuity",
                profile_name="book-continuity",
                prompt=section_review_contract,
                output_path=section_review_path,
                parse_json=True,
                output_schema="section_review",
                gate_fn=gate_no_blocking_issues,
                max_retries=args.max_retries,
                diagnostics_path=diagnostics_log,
                verbose=args.verbose,
                debug=debug_mode,
            )

            review_decision = await_review_gate_decision(
                run_dir=run_dir,
                run_journal=run_journal,
                context_store=context_store,
                section_index=idx,
                section_title=section_title,
                stage_id=f"section_review_{idx:02d}",
                section_path=section_output_path,
                section_review_path=section_review_path,
            )
            if review_decision.get("action") == "rewrite" and rewrite_cycles < BOOK_REVIEW_MAX_REWRITE_CYCLES:
                rewrite_cycles += 1
                rewrite_feedback = review_decision.get("state") or {}
                append_run_event(
                    run_journal,
                    "human_review_rewrite_cycle_started",
                    {
                        "task_id": context_store.get("_task_id"),
                        "section_index": idx,
                        "section_title": section_title,
                        "rewrite_cycle": rewrite_cycles,
                        "issue_tags": rewrite_feedback.get("issue_tags") or [],
                        "correlation_id": rewrite_feedback.get("correlation_id"),
                    },
                )
                continue
            if review_decision.get("action") == "rewrite":
                append_run_event(
                    run_journal,
                    "human_review_rewrite_limit_reached",
                    {
                        "task_id": context_store.get("_task_id"),
                        "section_index": idx,
                        "section_title": section_title,
                        "rewrite_cycles": rewrite_cycles,
                    },
                )
            break

        section_texts.append(section_text)
        section_summaries.append(
            {
                "section": idx,
                "title": section_title,
                "summary": section_review.get("section_summary", ""),
                "continuity_state_updates": section_review.get("continuity_state_updates", []),
            }
        )
        continuity_state.setdefault("section_updates", [])
        consistency_sections, updated_checkpoint = update_section_consistency_after_review(
            consistency_sections,
            section_index=idx,
            section_text=section_text,
            section_review=section_review,
        )
        continuity_state["consistency_sections"] = consistency_sections
        continuity_state["section_updates"].append(
            {
                "section": idx,
                "title": section_title,
                "updates": section_review.get("continuity_state_updates", []),
                "consistency_checkpoint": {
                    "status": updated_checkpoint.get("status", "unknown"),
                    "coverage": updated_checkpoint.get("coverage", {}),
                    "missing_tracking": updated_checkpoint.get("missing_tracking", {}),
                },
            }
        )
        write_json(dirs["canon"] / "consistency_sections.json", consistency_sections)
        write_json(dirs["canon"] / "continuity_state.json", continuity_state)

    # 6.5) Story architect review (post-writer concept/structure validation)
    story_architect_contract = build_contract(
        role="Story Architect Agent",
        objective="Validate concept and structure of the drafted section before downstream editing.",
        constraints=["Return JSON only", "Include concept_validation and structure_validation scores with notes"],
        inputs={"draft_sections": section_texts, "chapter_spec": chapter_spec, "book_structure": outline_payload},
        output_format='JSON with keys: concept_validation, structure_validation, notes, revision_focus',
        failure_conditions=["invalid JSON", "missing validation scores", "non-actionable notes"],
    )
    story_architect_review = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="story_architect_review",
        agent_name="book-architect",
        profile_name="book-architect",
        prompt=story_architect_contract,
        output_path=dirs["reviews"] / "story_architect_review.json",
        parse_json=True,
        output_schema="story_architect_review",
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
        debug=debug_mode,
    )

    # 7) Assembler + assembly consistency review
    section_bundle_text = "\n\n".join([f"## Section {i + 1}\n\n{text}" for i, text in enumerate(section_texts)])

    merge_items = [
        {
            "id": f"section_{idx:02d}",
            "text": text,
            "summary": section_summaries[idx - 1] if idx - 1 < len(section_summaries) else {},
        }
        for idx, text in enumerate(section_texts, start=1)
    ]
    merge_stats = {
        "initial_sections": len(merge_items),
        "merge_levels": [],
        "oversize_items": [],
    }

    max_merge_words = max(400, int(args.merge_context_words))
    level = 0
    while len(merge_items) > 1:
        level += 1
        groups, oversize = chunk_items_by_word_budget(merge_items, max_merge_words)
        merge_stats["oversize_items"].extend(oversize)
        merge_stats["merge_levels"].append(
            {
                "level": level,
                "input_items": len(merge_items),
                "groups": len(groups),
                "max_merge_words": max_merge_words,
            }
        )

        next_level = []
        for group_idx, group in enumerate(groups, start=1):
            if len(group) == 1:
                next_level.append(group[0])
                continue

            group_texts = [item.get("text", "") for item in group]
            group_summaries = [item.get("summary", {}) for item in group]
            assembler_contract = build_contract(
                role="Chapter Assembler Agent",
                objective="Assemble chapter draft from section output.",
                constraints=["Return markdown only", "Remove repetition", "Smooth transitions"],
                inputs={
                    "sections": group_texts,
                    "section_summaries": group_summaries,
                    "chapter_spec": chapter_spec,
                    "canon": canon_payload,
                    "context_tracking_strategy": context_tracking_strategy,
                },
                output_format="Markdown chapter draft",
                failure_conditions=["repetitive output", "broken flow", "new major facts invented"],
            )
            merged_text = run_stage(
                orchestrator=orchestrator,
                lock_manager=lock_manager,
                changes_log=changes_log,
                context_store=context_store,
                stage_id=f"assembler_l{level:02d}_g{group_idx:02d}",
                agent_name="book-assembler",
                profile_name="book-assembler",
                prompt=assembler_contract,
                output_path=None,
                parse_json=False,
                gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.35), "merged chunk too short"),
                max_retries=args.max_retries,
                diagnostics_path=diagnostics_log,
                verbose=args.verbose,
                debug=debug_mode,
            )
            next_level.append(
                {
                    "id": f"L{level:02d}G{group_idx:02d}",
                    "text": merged_text,
                    "summary": {
                        "level": level,
                        "group": group_idx,
                        "source_ids": [item.get("id") for item in group],
                    },
                }
            )

        merge_items = next_level

    assembled = merge_items[0].get("text", "") if merge_items else ""
    write_text(dirs["drafts_ch"] / "assembled.md", assembled)

    assembly_review_contract = build_contract(
        role="Assembly Continuity Reviewer",
        objective="Review assembled chapter for sequencing inconsistencies introduced by combining section drafts.",
        constraints=["Return JSON only", "Include blocking_issues, warnings, continuity_notes"],
        inputs={
            "section_bundle": section_bundle_text,
            "section_summaries": section_summaries,
            "assembled_chapter": assembled,
            "canon": canon_payload,
        },
        output_format='JSON with keys: blocking_issues, warnings, continuity_notes',
        failure_conditions=["invalid JSON", "missing continuity notes", "undetected sequence contradiction"],
    )
    run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="chapter_assembly_review",
        agent_name="book-continuity",
        profile_name="book-continuity",
        prompt=assembly_review_contract,
        output_path=dirs["assembly_reviews"] / "chapter_assembly_review.json",
        parse_json=True,
        output_schema="assembly_review",
        gate_fn=gate_no_blocking_issues,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
        debug=debug_mode,
    )

    # 8) Developmental editor with scoring gate
    dev_contract = build_contract(
        role="Developmental Editor Agent",
        objective="Score chapter and return rewrite guidance if weak.",
        constraints=["Return JSON only", "Include pass, scores, notes, rewrite_instructions"],
        inputs={
            "assembled": assembled,
            "chapter_spec": chapter_spec,
            "acceptance_criteria": brief.get("acceptance_criteria", []),
            "story_architect_review": story_architect_review,
        },
        output_format='JSON with keys: pass, scores, notes, rewrite_instructions',
        failure_conditions=["missing score dimensions", "missing rewrite instructions on fail", "invalid JSON"],
    )
    dev_report = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="developmental_editor",
        agent_name="book-developmental-editor",
        profile_name="book-developmental-editor",
        prompt=dev_contract,
        output_path=dirs["reviews"] / "developmental_report.json",
        parse_json=True,
        output_schema="developmental_editor",
        gate_fn=gate_developmental,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
        debug=debug_mode,
    )

    # 9) Line editor
    line_contract = build_contract(
        role="Line Editor / Style Agent",
        objective="Polish chapter while preserving meaning and canon.",
        constraints=["Return markdown only", "Enforce style guide", "Avoid cliches and repeated openings"],
        inputs={"assembled": assembled, "developmental_report": dev_report, "style_guide": canon_payload.get("style_guide", "")},
        output_format="Markdown polished chapter",
        failure_conditions=["style drift", "added contradictions", "meaning loss"],
    )
    polished = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="line_editor",
        agent_name="book-line-editor",
        profile_name="book-line-editor",
        prompt=line_contract,
        output_path=dirs["drafts_ch"] / "edited.md",
        parse_json=False,
        gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.7), "polished chapter too short"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
        debug=debug_mode,
    )

    # 9.5) Copy editor stage
    copy_contract = build_contract(
        role="Copy Editor Agent",
        objective="Correct grammar and mechanics while preserving narrative meaning.",
        constraints=["Return markdown only", "Preserve plot and meaning", "Enforce grammar and consistency"],
        inputs={"polished": polished, "style_guide": canon_payload.get("style_guide", "")},
        output_format="Markdown copy-edited chapter",
        failure_conditions=["meaning drift", "new contradictions", "mechanical errors left unresolved"],
    )
    copy_edited = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="copy_editor",
        agent_name="book-copy-editor",
        profile_name="book-copy-editor",
        prompt=copy_contract,
        output_path=dirs["drafts_ch"] / "copy_edited.md",
        parse_json=False,
        gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.7), "copy-edited chapter too short"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
        debug=debug_mode,
    )

    # 9.6) Proofreader stage
    proof_contract = build_contract(
        role="Proofreader Agent",
        objective="Run final typo/punctuation pass before review scoring.",
        constraints=["Return markdown only", "No structural rewrites", "No new facts"],
        inputs={"copy_edited": copy_edited},
        output_format="Markdown proofread chapter",
        failure_conditions=["typos remain", "punctuation defects", "introduced narrative changes"],
    )
    proofread = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="proofreader",
        agent_name="book-proofreader",
        profile_name="book-proofreader",
        prompt=proof_contract,
        output_path=dirs["drafts_ch"] / "proofread.md",
        parse_json=False,
        gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.7), "proofread chapter too short"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
        debug=debug_mode,
    )

    # 9.7) Rubric reviewer stage (10-point scoring + next-writer handoff)
    rubric_contract = build_contract(
        role="Session Reviewer Agent",
        objective="Score this writing session using the full 10-point rubric and produce next-writer handoff notes.",
        constraints=[
            "Return JSON only",
            "Scores must include all 10 rubric dimensions",
            "Include next_writer_notes with focus topics and continuity carry-forward",
        ],
        inputs={
            "proofread": proofread,
            "chapter_spec": chapter_spec,
            "canon": canon_payload,
            "developmental_report": dev_report,
        },
        output_format='JSON with keys: scores{concept_validation,structure_validation,chapter_coherence,sentence_clarity,grammar_correction,continuity_tracking,fact_verification,tone_consistency,genre_compliance,reader_engagement_score}, notes, next_writer_notes',
        failure_conditions=["missing score keys", "scores below threshold", "missing next_writer_notes"],
    )
    rubric_report = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="session_reviewer",
        agent_name="book-continuity",
        profile_name="book-continuity",
        prompt=rubric_contract,
        output_path=dirs["reviews"] / "rubric_report.json",
        parse_json=True,
        output_schema="session_reviewer",
        gate_fn=gate_rubric_report,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
        debug=debug_mode,
    )

    next_writer_notes = rubric_report.get("next_writer_notes", {})
    write_json(dirs["reviews"] / "next_writer_notes.json", next_writer_notes)
    write_json(dirs["canon"] / "next_writer_notes.json", next_writer_notes)

    continuity_state["next_writer_notes"] = next_writer_notes
    continuity_state["last_rubric_scores"] = rubric_report.get("scores", {})
    write_json(dirs["canon"] / "continuity_state.json", continuity_state)

    append_jsonl(
        dirs["canon"] / "session_handoffs.jsonl",
        {
            "timestamp": datetime.utcnow().isoformat(),
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "next_writer_notes": next_writer_notes,
            "rubric_scores": rubric_report.get("scores", {}),
        },
    )
    write_agent_context_status(
        agent_context_status_path,
        {
            "phase": "next_writer_handoff",
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "expectations": {
                "focus_topics": _normalize_list(next_writer_notes.get("focus_topics")),
                "continuity_watch": _normalize_list(next_writer_notes.get("continuity_watch")),
                "must_carry_forward": _normalize_list(next_writer_notes.get("must_carry_forward")),
            },
        },
    )

    # 10) Continuity audit
    continuity_contract = build_contract(
        role="Continuity / QA Agent",
        objective="Audit polished chapter for continuity and canon violations.",
        constraints=["Return JSON only", "Include blocking_issues, warnings, patch_tasks"],
        inputs={
            "polished": proofread,
            "canon": canon_payload,
            "open_loops": canon_payload.get("open_loops", []),
            "rubric_report": rubric_report,
        },
        output_format='JSON with keys: blocking_issues, warnings, patch_tasks, summary',
        failure_conditions=["invalid JSON", "missing issue lists", "non-actionable patches"],
    )
    continuity = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="continuity",
        agent_name="book-continuity",
        profile_name="book-continuity",
        prompt=continuity_contract,
        output_path=dirs["reviews"] / "continuity_report.json",
        parse_json=True,
        output_schema="continuity",
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
        debug=debug_mode,
    )

    # Arc consistency check — verify open loops are persisted into next_writer_notes and
    # character arcs are acknowledged. Open loops are intentional story features that
    # must survive chapter handoffs and be resolved before series end, not within the chapter.
    _arc_current = read_json(arc_tracker_path, default={})
    arc_score, arc_issues, untracked_loops, near_miss_scores = score_arc_consistency(_arc_current, rubric_report)
    write_json(
        dirs["reviews"] / "arc_consistency_score.json",
        {
            "score": arc_score,
            "threshold": ARC_CONSISTENCY_THRESHOLD,
            "passed": arc_score >= ARC_CONSISTENCY_THRESHOLD,
            "issues": arc_issues,
            "warning_only_mode": ARC_CONSISTENCY_WARNING_ONLY,
            "loop_match_threshold": ARC_LOOP_MATCH_THRESHOLD,
            "loop_warning_threshold": ARC_LOOP_WARNING_THRESHOLD,
            "near_miss_scores": near_miss_scores,
            "untracked_open_loops": untracked_loops,
            "all_open_loops": _normalize_list(_arc_current.get("open_loops")),
            "chapter_number": args.chapter_number,
            "section_title": args.section_title,
        },
    )
    # Log untracked loops as persistent story feature annotations so operators
    # know which loops are at risk of being dropped before series end.
    if untracked_loops:
        write_agent_context_status(
            agent_context_status_path,
            {
                "phase": "open_loop_persistence_warning",
                "chapter_number": args.chapter_number,
                "section_title": args.section_title,
                "untracked_open_loops": untracked_loops,
                "action_required": (
                    "These open loops were not found in must_carry_forward. "
                    "Add them to next_writer_notes or they risk being lost next chapter."
                ),
            },
        )
    append_run_event(
        run_journal,
        "arc_consistency_scored",
        {
            "score": arc_score,
            "threshold": ARC_CONSISTENCY_THRESHOLD,
            "passed": arc_score >= ARC_CONSISTENCY_THRESHOLD,
            "warning_only_mode": ARC_CONSISTENCY_WARNING_ONLY,
            "issues": arc_issues,
            "near_miss_scores": near_miss_scores,
            "untracked_open_loops": untracked_loops,
        },
    )
    if arc_score < ARC_CONSISTENCY_THRESHOLD:
        if ARC_CONSISTENCY_WARNING_ONLY:
            write_agent_context_status(
                agent_context_status_path,
                {
                    "phase": "arc_consistency_warning_only",
                    "chapter_number": args.chapter_number,
                    "section_title": args.section_title,
                    "score": arc_score,
                    "threshold": ARC_CONSISTENCY_THRESHOLD,
                    "issues": arc_issues,
                    "near_miss_scores": near_miss_scores,
                },
            )
            print(
                "[WARN] arc_consistency below threshold but warning-only mode is enabled; "
                f"score={arc_score}, threshold={ARC_CONSISTENCY_THRESHOLD}"
            )
        else:
            raise StageQualityGateError(
                f"arc_consistency_score={arc_score} below threshold={ARC_CONSISTENCY_THRESHOLD}: " + "; ".join(arc_issues),
                details={"score": arc_score, "threshold": ARC_CONSISTENCY_THRESHOLD, "issues": arc_issues},
            )

    # 11) Publisher final gate
    publisher_qa_contract = build_contract(
        role="Publisher QA Agent",
        objective="Approve or request revision for final chapter output.",
        constraints=["Return JSON only", "Set decision to APPROVE or REVISE", "Provide required_fixes"],
        inputs={"polished": proofread, "continuity_report": continuity, "chapter_spec": chapter_spec, "rubric_report": rubric_report},
        output_format='JSON with keys: decision, scores, notes, required_fixes, summary',
        failure_conditions=["invalid JSON", "missing decision", "missing required_fixes on REVISE"],
    )

    # --- Publisher QA with retry on revision request ---
    publisher_qa = None
    publisher_attempts = 0
    max_publisher_retries = max(1, args.max_retries + 1)
    last_publisher_prompt = publisher_qa_contract
    current_manuscript = proofread
    forced_completion = False
    while publisher_attempts < max_publisher_retries:
        publisher_attempts += 1
        publisher_qa = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id="publisher_qa",
            agent_name="book-publisher",
            profile_name="book-publisher",
            prompt=last_publisher_prompt,
            output_path=dirs["reviews"] / "publisher_report.json",
            parse_json=True,
            output_schema="publisher_qa",
            max_retries=1,  # Only one attempt per outer loop
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
        decision = str(publisher_qa.get("decision", "")).upper()
        if decision == "APPROVE":
            break
        append_run_event(
            run_journal,
            "publisher_revision_cycle",
            {
                "attempt": publisher_attempts,
                "decision": decision,
                "required_fixes": publisher_qa.get("required_fixes", []),
            },
        )

        autofix_contract = build_contract(
            role="Book Editor Recovery Agent",
            objective="Revise the manuscript so publisher and continuity issues are corrected and the chapter can complete successfully.",
            constraints=[
                "Return markdown only",
                "Apply all required_fixes and patch_tasks",
                "Preserve chapter intent, canon, and factual continuity",
                "Do not explain changes outside the manuscript",
            ],
            inputs={
                "manuscript": current_manuscript,
                "publisher_report": publisher_qa,
                "continuity_report": continuity,
                "chapter_spec": chapter_spec,
                "rubric_report": rubric_report,
            },
            output_format="Markdown revised chapter ready for copy edit and proofreading",
            failure_conditions=["required fixes omitted", "new contradictions introduced", "non-markdown response"],
        )
        revised_manuscript = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"publisher_autofix_{publisher_attempts:02d}",
            agent_name="book-editor",
            profile_name=args.editor_profile,
            prompt=autofix_contract,
            output_path=dirs["drafts_ch"] / f"publisher_autofix_{publisher_attempts:02d}.md",
            parse_json=False,
            gate_fn=lambda text: (len(str(text).split()) >= int(args.writer_words * 0.65), "autofix manuscript too short"),
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
        copy_edited = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"publisher_autofix_copy_{publisher_attempts:02d}",
            agent_name="book-copy-editor",
            profile_name="book-copy-editor",
            prompt=build_contract(
                role="Copy Editor Agent",
                objective="Correct grammar and mechanics after publisher-mandated revisions.",
                constraints=["Return markdown only", "Preserve meaning", "Fix mechanics and consistency"],
                inputs={"polished": revised_manuscript, "style_guide": canon_payload.get("style_guide", "")},
                output_format="Markdown copy-edited chapter",
                failure_conditions=["meaning drift", "new contradictions", "mechanical defects remain"],
            ),
            output_path=dirs["drafts_ch"] / f"publisher_autofix_copy_{publisher_attempts:02d}.md",
            parse_json=False,
            gate_fn=lambda text: (len(str(text).split()) >= int(args.writer_words * 0.65), "autofix copy-edited chapter too short"),
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
        proofread = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"publisher_autofix_proof_{publisher_attempts:02d}",
            agent_name="book-proofreader",
            profile_name="book-proofreader",
            prompt=build_contract(
                role="Proofreader Agent",
                objective="Run final typo and punctuation repair after publisher revisions.",
                constraints=["Return markdown only", "No structural rewrites", "No new facts"],
                inputs={"copy_edited": copy_edited},
                output_format="Markdown proofread chapter",
                failure_conditions=["typos remain", "punctuation defects", "narrative changes introduced"],
            ),
            output_path=dirs["drafts_ch"] / f"publisher_autofix_proof_{publisher_attempts:02d}.md",
            parse_json=False,
            gate_fn=lambda text: (len(str(text).split()) >= int(args.writer_words * 0.65), "autofix proofread chapter too short"),
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
        current_manuscript = proofread
        continuity_contract = build_contract(
            role="Continuity / QA Agent",
            objective="Audit revised chapter for continuity and canon violations.",
            constraints=["Return JSON only", "Include blocking_issues, warnings, patch_tasks"],
            inputs={
                "polished": current_manuscript,
                "canon": canon_payload,
                "open_loops": canon_payload.get("open_loops", []),
                "rubric_report": rubric_report,
            },
            output_format='JSON with keys: blocking_issues, warnings, patch_tasks, summary',
            failure_conditions=["invalid JSON", "missing issue lists", "non-actionable patches"],
        )
        continuity = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"continuity_recheck_{publisher_attempts:02d}",
            agent_name="book-continuity",
            profile_name="book-continuity",
            prompt=continuity_contract,
            output_path=dirs["reviews"] / f"continuity_report_{publisher_attempts:02d}.json",
            parse_json=True,
            output_schema="continuity",
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
            debug=debug_mode,
        )
        last_publisher_prompt = build_contract(
            role="Publisher QA Agent",
            objective="Approve or request revision for final chapter output.",
            constraints=["Return JSON only", "Set decision to APPROVE or REVISE", "Provide required_fixes"],
            inputs={"polished": current_manuscript, "continuity_report": continuity, "chapter_spec": chapter_spec, "rubric_report": rubric_report},
            output_format='JSON with keys: decision, scores, notes, required_fixes, summary',
            failure_conditions=["invalid JSON", "missing decision", "missing required_fixes on REVISE"],
        )

    if publisher_qa and str(publisher_qa.get("decision", "")).upper() != "APPROVE":
        forced_completion = True
        append_run_event(
            run_journal,
            "forced_completion",
            {
                "publisher_attempts": publisher_attempts,
                "final_decision": publisher_qa.get("decision"),
            },
        )

    # Persistent memory updates and final export
    chapter_summary = {
        "chapter_number": args.chapter_number,
        "summary": publisher_qa.get("summary", ""),
        "new_facts": continuity.get("patch_tasks", []),
        "open_loops": canon_payload.get("open_loops", []),
        "next_writer_notes": next_writer_notes,
    }
    rolling_memory = parse_json_block(read_text(dirs["canon"] / "rolling_memory.json", "{}"), fallback={})
    rolling_memory.setdefault("chapter_summaries", [])
    rolling_memory["chapter_summaries"].append(chapter_summary)
    context_store["rolling_memory"] = rolling_memory
    write_json(dirs["canon"] / "rolling_memory.json", rolling_memory)
    write_json(dirs["canon"] / "context_store.json", context_store)

    arc_tracker_existing = read_json(arc_tracker_path, default={})
    arc_tracker = update_arc_tracker(
        arc_tracker_existing,
        chapter_number=args.chapter_number,
        chapter_title=args.chapter_title,
        section_title=args.section_title,
        next_writer_notes=next_writer_notes,
        continuity_state=continuity_state,
        canon_payload=canon_payload,
        rubric_report=rubric_report,
    )
    write_json(arc_tracker_path, arc_tracker)

    progress_index = read_json(progress_index_path, default={})
    chapters = progress_index.get("completed_chapters") if isinstance(progress_index, dict) else []
    chapters = chapters if isinstance(chapters, list) else []
    chapters.append(
        {
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "publisher_decision": publisher_qa.get("decision"),
            "run_dir": str(run_dir),
            "updated_at": datetime.utcnow().isoformat(),
        }
    )
    progress_index["completed_chapters"] = chapters
    progress_index["last_run"] = str(run_dir)
    progress_index["last_updated"] = datetime.utcnow().isoformat()
    write_json(progress_index_path, progress_index)

    # Living skeleton: post-acceptance documentation update (two-tier system)
    # Writes the immutable canonical record and updates the predictive living
    # skeleton.  Errors here never block the chapter from completing — the
    # chapter is already accepted; we log and continue.
    living_skeleton_result = run_living_skeleton_update(
        framework_root=framework_root,
        book_root=book_root,
        chapter_number=args.chapter_number,
        chapter_title=args.chapter_title,
        section_title=args.section_title,
        accepted_manuscript=current_manuscript,
        arc_tracker=arc_tracker,
        canon_payload=canon_payload,
        next_writer_notes=next_writer_notes,
        rubric_report=rubric_report,
        run_dir=run_dir,
        orchestrator=orchestrator,
        verbose=getattr(args, "verbose", False),
    )
    append_run_event(
        run_journal,
        "living_skeleton_updated",
        {
            "chapter_number": args.chapter_number,
            "extraction_succeeded": living_skeleton_result.get("extraction_succeeded"),
            "canonical_record_path": living_skeleton_result.get("canonical_record_path"),
            "error": living_skeleton_result.get("error"),
        },
    )

    write_agent_context_status(
        agent_context_status_path,
        {
            "phase": "chapter_complete",
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "expectations": {
                "publisher_decision": publisher_qa.get("decision"),
                "open_loops": _normalize_list(canon_payload.get("open_loops")),
                "next_unresolved_questions": _normalize_list(next_writer_notes.get("unresolved_questions")),
            },
        },
    )

    # Build attribution title page and prepend to the manuscript
    pen_name = getattr(args, "pen_name", "DaRaVeNrK")
    publisher_name = getattr(args, "publisher_name", "DaRaVeNrK LLC")
    year = datetime.utcnow().year
    title_page = (
        f"# {args.title}\n\n"
        f"**Author:** {pen_name}  \n"
        f"**Publisher:** {publisher_name}  \n"
        f"**Copyright:** © {year} {publisher_name}. All rights reserved.  \n"
        f"**Chapter {args.chapter_number}:** {args.chapter_title}\n\n"
        f"---\n\n"
    )
    signed_manuscript = title_page + current_manuscript

    write_text(dirs["final"] / "manuscript_v1.md", signed_manuscript)
    write_text(dirs["final"] / "manuscript_v2.md", signed_manuscript)

    validation = validate_required_artifacts(run_dir, expected_section_count=len(chapter_sections))
    correlation_integrity_strict = str(
        os.environ.get("AGENT_CORRELATION_INTEGRITY_STRICT", "true")
    ).lower() in {"1", "true", "yes", "on"}
    correlation_integrity = validate_stage_correlation_integrity(run_journal)
    if not correlation_integrity.get("valid", True):
        append_run_event(
            run_journal,
            "correlation_integrity_warning",
            {
                "missing_correlation": correlation_integrity.get("missing_correlation"),
                "total_stage_attempts": correlation_integrity.get("total_stage_attempts"),
                "examples": correlation_integrity.get("missing_examples") or [],
            },
        )
        if correlation_integrity_strict:
            validation["valid"] = False
            validation.setdefault("missing", [])
            validation["missing"].append(
                "diagnostic_check:stage_attempt_start missing correlation_id"
            )
    used_fallbacks = collect_used_fallback_stages(run_journal)
    quality_learning_update = update_quality_learning_state(
        book_root,
        rubric_report,
        quality_threshold_snapshot.get("effective_thresholds") if isinstance(quality_threshold_snapshot, dict) else {},
    )
    append_run_event(
        run_journal,
        "quality_learning_state_updated",
        quality_learning_update,
    )
    summary = {
        "strategy_version": strategy_version,
        "ml_mode": str(getattr(orchestrator, "ml_mode", "off")),
        "run_dir": str(run_dir),
        "book_root": str(book_root),
        "framework_root": str(framework_root),
        "runs_root": str(runs_root),
        "title": args.title,
        "attribution": {
            "pen_name": getattr(args, "pen_name", "DaRaVeNrK"),
            "publisher_name": getattr(args, "publisher_name", "DaRaVeNrK LLC"),
            "copyright_year": datetime.utcnow().year,
        },
        "chapter_number": args.chapter_number,
        "chapter_title": args.chapter_title,
        "section_title": args.section_title,
        "section_count": len(chapter_sections),
        "publisher_decision": publisher_qa.get("decision"),
        "publisher_attempts": publisher_attempts,
        "forced_completion": forced_completion,
        "used_fallbacks": used_fallbacks,
        "fallback_provenance": {
            "used_fallbacks": used_fallbacks,
            "used_fallback_count": len(used_fallbacks),
            "human_review_recommended": bool(used_fallbacks),
            "note": "One or more deterministic stage fallbacks were used in this run." if used_fallbacks else "No deterministic stage fallbacks were used in this run.",
        },
        "artifact_validation": validation,
        "correlation_integrity": correlation_integrity,
        "correlation_integrity_strict": bool(correlation_integrity_strict),
        "merge_stats": merge_stats,
        "context_tracking_strategy": context_tracking_strategy,
        "changes_log": str(changes_log),
        "run_journal": str(run_journal),
        "diagnostics_log": str(diagnostics_log) if diagnostics_log else None,
        "framework_files": {
            "framework_skeleton": str(framework_skeleton_path),
            "arc_tracker": str(arc_tracker_path),
            "progress_index": str(progress_index_path),
            "agent_context_status": str(agent_context_status_path),
        },
        "agent_behavior_tracking": {
            "quality_failures_log": str(orchestrator.quality_failures_log_path),
            "reward_ledger": str(orchestrator.agent_rewards_path),
            "reward_events": str(orchestrator.agent_reward_events_path),
            "ml_shadow_events": str(getattr(orchestrator, "ml_shadow_events_path", "")),
        },
        "quality_thresholds": {
            "base": {
                "min_score": BOOK_QUALITY_MIN_SCORE,
                "min_avg_score": BOOK_QUALITY_MIN_AVG_SCORE,
                "min_content_score": BOOK_QUALITY_MIN_CONTENT_SCORE,
            },
            "effective": {
                "min_score": BOOK_QUALITY_EFFECTIVE_MIN_SCORE,
                "min_avg_score": BOOK_QUALITY_EFFECTIVE_MIN_AVG_SCORE,
                "min_content_score": BOOK_QUALITY_EFFECTIVE_MIN_CONTENT_SCORE,
            },
            "adaptive_enabled": BOOK_QUALITY_ADAPTIVE_ENABLED,
            "snapshot": quality_threshold_snapshot,
            "learning_update": quality_learning_update,
        },
        "living_skeleton_docs": {
            "canonical_record": living_skeleton_result.get("canonical_record_path"),
            "living_skeleton": living_skeleton_result.get("living_skeleton_path"),
            "doc_index": living_skeleton_result.get("doc_index_path"),
            "extraction_succeeded": living_skeleton_result.get("extraction_succeeded"),
            "error": living_skeleton_result.get("error"),
        },
        "verbose": bool(args.verbose),
    }
    write_json(run_dir / "run_summary.json", summary)

    retro_dir = run_dir / "07_retro"
    ensure_dir(retro_dir)
    retro = build_retro_report(run_dir, summary)
    write_json(retro_dir / "retrospective.json", retro)
    write_text(retro_dir / "retrospective.md", build_retro_markdown(retro))
    recent_quality_failures = load_recent_jsonl(orchestrator.quality_failures_log_path, limit=50)
    write_json(retro_dir / "quality_failures_review.json", {
        "source_log": str(orchestrator.quality_failures_log_path),
        "count": len(recent_quality_failures),
        "entries": recent_quality_failures,
    })
    write_text(
        retro_dir / "quality_failures_review.md",
        build_quality_failure_review_markdown(recent_quality_failures),
    )

    parent_log_success = {
        "timestamp": datetime.utcnow().isoformat(),
        "agent": "book-flow-parent",
        "action": "run_success",
        "details": {
            "output_dir": str(run_dir),
            "summary_keys": list(summary.keys()) if isinstance(summary, dict) else [],
        },
    }

    append_jsonl(changes_log, parent_log_success)
    append_run_event(
        run_journal,
        "run_success",
        {
            "publisher_decision": publisher_qa.get("decision"),
            "publisher_attempts": publisher_attempts,
            "forced_completion": forced_completion,
            "run_summary": str(run_dir / "run_summary.json"),
            "strategy_version": strategy_version,
        },
    )
    update_cli_runtime_activity_from_context(context_store, {}, clear=True)

    publication_export = {
        "enabled": True,
        "published": False,
        "archive": None,
        "remote": None,
        "returncode": None,
        "stderr": "",
        "stdout": "",
    }
    cleanup_after_publish = bool(getattr(args, "cleanup_after_publish", True))

    # --- Post-processing: Archive and export completed book run ---
    try:
        import subprocess
        archive_name = f"{book_slug}_run_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.tar.gz"
        archive_path = str(runs_root / archive_name)
        # Create tar.gz of the run_dir
        subprocess.run([
            "tar", "-czf", archive_path, "-C", str(runs_root), run_name
        ], check=True)
        # SCP to remote server
        remote_path = f"192.168.86.34:/media/daravenrk/The_Device/WrittenBooks/{archive_name}"
        scp_result = subprocess.run([
            "scp", archive_path, remote_path
        ], capture_output=True, text=True)
        publication_export = {
            "enabled": True,
            "published": scp_result.returncode == 0,
            "archive": archive_path,
            "remote": remote_path,
            "returncode": scp_result.returncode,
            "stdout": scp_result.stdout,
            "stderr": scp_result.stderr,
        }
        export_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "book-flow-parent",
            "action": "export_book_run",
            "details": publication_export,
        }
        append_jsonl(changes_log, export_log)
        append_run_event(
            run_journal,
            "publication_export_complete",
            publication_export,
        )
    except Exception as export_err:
        wrapped_export_err = BookExportError(
            f"Book run export failed: {export_err}",
            details={
                "run_dir": str(run_dir),
                "runs_root": str(runs_root),
            },
        )
        error_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "book-flow-parent",
            "action": "export_error",
            "details": {
                "error": str(wrapped_export_err),
                "error_code": wrapped_export_err.code,
                "details": wrapped_export_err.details,
            },
        }
        append_jsonl(changes_log, error_log)
        publication_export = {
            "enabled": True,
            "published": False,
            "archive": None,
            "remote": None,
            "returncode": None,
            "stdout": "",
            "stderr": str(wrapped_export_err),
        }
        append_run_event(
            run_journal,
            "publication_export_failed",
            publication_export,
        )

    summary["publication_export"] = publication_export
    write_json(run_dir / "run_summary.json", summary)

    if publication_export.get("published") and cleanup_after_publish:
        cleanup_summary = {
            "cleanup_reason": "post_publish",
            "history_root": str(run_history_root),
            "deleted_export_bundle": None,
            "skipped_entries": [],
        }
        append_run_event(
            run_journal,
            "run_cleanup_start",
            {
                "cleanup_reason": "post_publish",
                "history_root": str(run_history_root),
                "export_archive": publication_export.get("archive"),
            },
        )
        try:
            archived = archive_run_directory(
                run_dir,
                run_history_root,
                cleanup_reason="post_publish",
                extra_manifest={
                    "publisher_decision": publisher_qa.get("decision"),
                    "forced_completion": forced_completion,
                    "publication_export": publication_export,
                },
            )
            cleanup_summary.update(archived)
            archive_bundle = publication_export.get("archive")
            if archive_bundle and os.path.exists(archive_bundle):
                os.unlink(archive_bundle)
                cleanup_summary["deleted_export_bundle"] = archive_bundle

            archived_run_journal = Path(cleanup_summary["archive_dir"]) / "run_journal.jsonl"
            append_run_event(archived_run_journal, "run_cleanup_complete", cleanup_summary)
            archived_summary_path = Path(cleanup_summary["archive_dir"]) / "run_summary.json"
            archived_summary = read_json(archived_summary_path, default={})
            if isinstance(archived_summary, dict):
                archived_summary["post_publish_cleanup"] = cleanup_summary
                write_json(archived_summary_path, archived_summary)
            append_jsonl(
                book_history_log,
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "agent": "book-flow-parent",
                    "action": "post_publish_cleanup",
                    "details": cleanup_summary,
                },
            )
            summary["post_publish_cleanup"] = cleanup_summary
        except (OSError, PermissionError) as cleanup_err:
            cleanup_summary["skipped_entries"].append(
                {
                    "path": str(run_dir),
                    "reason": f"cleanup_error: {cleanup_err}",
                }
            )
            summary["post_publish_cleanup"] = cleanup_summary
            append_run_event(
                run_journal,
                "run_cleanup_failed",
                cleanup_summary,
            )

    print(json.dumps(summary, indent=2))
    return json.dumps(summary, indent=2)


def build_parser():
    p = argparse.ArgumentParser(description="Run hierarchical book writing flow with quality gates")
    p.add_argument("--title", required=True)
    p.add_argument("--genre", default="speculative fiction")
    p.add_argument("--audience", default="adult")
    p.add_argument("--tone", default="cinematic and emotionally grounded")
    p.add_argument("--premise", required=True)

    p.add_argument("--chapter-number", "--chapter_number", dest="chapter_number", type=int, default=1)
    p.add_argument("--chapter-title", "--chapter_title", dest="chapter_title", required=True)
    p.add_argument("--section-title", "--section_title", dest="section_title", required=True)
    p.add_argument("--section-goal", "--section_goal", dest="section_goal", required=True)
    p.add_argument("--writer-words", "--writer_words", dest="writer_words", type=int, default=1400)
    p.add_argument("--target-word-count", "--target_word_count", dest="target_word_count", type=int, default=125000)
    p.add_argument("--page-target", "--page_target", dest="page_target", type=int, default=450)
    p.add_argument("--max-retries", "--max_retries", dest="max_retries", type=int, default=2)
    p.add_argument("--merge-context-words", "--merge_context_words", dest="merge_context_words", type=int, default=3500)
    p.add_argument("-v", "--verbose", action="store_true", help="Enable detailed diagnostics logging")
    p.add_argument("--debug", action="store_true", help="Persist raw and parsed outputs for every stage attempt and recovery (default: on)")
    p.add_argument("--no-debug", action="store_true", help="Disable raw and parsed payload logging for this run")

    p.add_argument("--writer-profile", "--writer_profile", dest="writer_profile", default="book-writer")
    p.add_argument("--editor-profile", "--editor_profile", dest="editor_profile", default="book-editor")
    p.add_argument("--publisher-brief-profile", "--publisher_brief_profile", dest="publisher_brief_profile", default="book-publisher-brief")
    p.add_argument("--publisher-profile", "--publisher_profile", dest="publisher_profile", default="book-publisher")

    # Attribution / ownership
    p.add_argument("--pen-name", "--pen_name", dest="pen_name", default="DaRaVeNrK",
                   help="Author pen name to appear on the manuscript (e.g. Demosthenes, Locke, or a custom pen name)")
    p.add_argument("--publisher-name", "--publisher_name", dest="publisher_name", default="DaRaVeNrK LLC",
                   help="Publisher / company name for the copyright line")

    p.add_argument("--output-dir", "--output_dir", dest="output_dir", default="/home/daravenrk/dragonlair/book_project")
    p.add_argument(
        "--strategy-version",
        "--strategy_version",
        dest="strategy_version",
        default=DEFAULT_STRATEGY_VERSION,
        help="Strategy version tag stamped into run_journal and run_summary artifacts.",
    )
    cleanup_after_publish_default = str(
        os.environ.get("BOOK_FLOW_CLEANUP_AFTER_PUBLISH", "true")
    ).lower() in {"1", "true", "yes", "on"}
    p.add_argument(
        "--cleanup-after-publish",
        dest="cleanup_after_publish",
        action="store_true",
        default=cleanup_after_publish_default,
        help="Archive the completed run to run_history and remove the live runs/ copy after successful publication export.",
    )
    p.add_argument(
        "--no-cleanup-after-publish",
        dest="cleanup_after_publish",
        action="store_false",
        help="Keep the completed run in runs/ even after successful publication export.",
    )
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    run_flow(args)


if __name__ == "__main__":
    main()
