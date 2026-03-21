import os
import threading
import time
import uuid
import glob
import datetime
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from types import SimpleNamespace
from typing import Dict, Optional, List, Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from .book_flow import run_flow, slugify
from .exceptions import AgentStackError, AgentUnexpectedError, OpenClawProfileConfigError
from .orchestrator import OrchestratorAgent


class SteerRequest(BaseModel):
    prompt: str
    direction: Optional[str] = None
    profile: Optional[str] = None
    model: Optional[str] = None


class StreamRequest(BaseModel):
    prompt: str
    direction: Optional[str] = None
    profile: Optional[str] = None
    model: Optional[str] = None


class SpawnControlRequest(BaseModel):
    action: str


class RecoverHungRequest(BaseModel):
    force: bool = False


class BookHoldRequest(BaseModel):
    hold: bool = True


class BookReviewActionRequest(BaseModel):
    action: str
    note: Optional[str] = None
    reviewer: str = "operator"


class BookFlowRequest(BaseModel):
    title: str
    premise: str
    chapter_number: int = 1
    chapter_title: str
    section_title: str
    section_goal: str
    genre: str = "speculative fiction"
    audience: str = "adult"
    tone: str = "cinematic and emotionally grounded"
    writer_words: int = 1400
    target_word_count: int = 125000
    page_target: int = 450
    max_retries: int = 2
    merge_context_words: int = 3500
    verbose: bool = False
    writer_profile: str = "book-writer"
    editor_profile: str = "book-editor"
    publisher_brief_profile: str = "book-publisher-brief"
    publisher_profile: str = "book-publisher"
    output_dir: str = "/home/daravenrk/dragonlair/book_project"
    resource_tracker_path: Optional[str] = None
    resource_events_path: Optional[str] = None
    # Attribution / ownership fields
    pen_name: str = "DaRaVeNrK"
    publisher_name: str = "DaRaVeNrK LLC"


class FeedbackTextRange(BaseModel):
    start: int
    end: int


class WritingFeedbackRequest(BaseModel):
    task_id: Optional[str] = None
    run_dir: Optional[str] = None
    output_dir: Optional[str] = "/home/daravenrk/dragonlair/book_project"
    title: Optional[str] = None
    chapter_number: Optional[int] = None
    section_index: Optional[int] = None
    section_title: Optional[str] = None
    stage_id: Optional[str] = None
    approved: bool
    needs_rewrite: bool
    score: float
    comment: Optional[str] = None
    feedback_type: str = "thumb"
    issue_tags: List[str] = Field(default_factory=list)
    rewrite_scope: str = "ask_each_time"
    pause_before_continue: bool = False
    assistant_rewrite_requested: bool = False
    selected_text_range: Optional[FeedbackTextRange] = None
    reviewer: str = "operator"


class OpenClawCompatMessage(BaseModel):
    role: str
    content: Optional[Any] = None
    name: Optional[str] = None


class OpenClawCompatChatRequest(BaseModel):
    model: str
    messages: List[OpenClawCompatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[List[dict]] = None
    tool_choice: Optional[Any] = None


@dataclass
class TaskRecord:
    id: str
    created_at: float
    status: str
    prompt: str
    direction: Optional[str]
    profile: Optional[str]
    route: Optional[str] = None
    model: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    response: Optional[str] = None
    error: Optional[str] = None
    book_request: Optional[dict] = None
    retry_count: int = 0
    max_auto_retries: int = 0
    hold: bool = False
    next_retry_at: Optional[float] = None
    production_status: Optional[dict] = None
    spawn_release_at: Optional[float] = None
    spawn_requested_at: Optional[float] = None


app = FastAPI(title="Dragonlair Agent API", version="0.1.0")
orchestrator = OrchestratorAgent()
executor = ThreadPoolExecutor(max_workers=int(os.environ.get("AGENT_MAX_WORKERS", "2")))
_task_lock = threading.Lock()
_tasks: Dict[str, TaskRecord] = {}
STRICT_ONE_MODEL_PER_ROUTE = str(os.environ.get("STRICT_ONE_MODEL_PER_ROUTE", "true")).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
NVIDIA_ROUTE_NAME = str(os.environ.get("AGENT_NVIDIA_ROUTE_NAME", "ollama_nvidia")).strip() or "ollama_nvidia"
NVIDIA_ALLOW_MIXED_TINY_MODELS = str(
    os.environ.get("AGENT_NVIDIA_ALLOW_MIXED_TINY_MODELS", "false")
).lower() in {"1", "true", "yes", "on"}
NVIDIA_TINY_MAX_ACTIVE_MODELS = max(
    1,
    int(os.environ.get("AGENT_NVIDIA_TINY_MAX_ACTIVE_MODELS", "2")),
)
_nvidia_tiny_raw = str(
    os.environ.get(
        "AGENT_NVIDIA_TINY_MODELS",
        "qwen3.5:4b,qwen2.5-coder:3b,qwen2.5-coder:1.5b,llama3.2:3b,llama3.2:1b,codegemma:2b",
    )
)
NVIDIA_TINY_MODELS = {item.strip() for item in _nvidia_tiny_raw.split(",") if item.strip()}
STRICT_MODE_VALIDATION = str(os.environ.get("AGENT_STRICT_MODE_VALIDATION", "true")).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
UI_STATE_PATH = Path(os.environ.get("AGENT_UI_STATE_PATH", "/home/daravenrk/dragonlair/book_project/webui_state.json"))
UI_EVENTS_PATH = Path(os.environ.get("AGENT_UI_EVENTS_PATH", "/home/daravenrk/dragonlair/book_project/webui_events.jsonl"))
CLI_RUNTIME_ACTIVITY_PATH = Path(
    os.environ.get("AGENT_CLI_RUNTIME_ACTIVITY_PATH", "/home/daravenrk/dragonlair/book_project/cli_runtime_activity.json")
)
RESOURCE_TRACKER_PATH = Path(
    os.environ.get("AGENT_RESOURCE_TRACKER_PATH", "/home/daravenrk/dragonlair/book_project/resource_tracker.json")
)
RESOURCE_EVENTS_PATH = Path(
    os.environ.get("AGENT_RESOURCE_EVENTS_PATH", "/home/daravenrk/dragonlair/book_project/resource_events.jsonl")
)
BOOK_FEEDBACK_PATH = Path(
    os.environ.get("AGENT_BOOK_FEEDBACK_PATH", "/home/daravenrk/dragonlair/book_project/book_feedback_events.jsonl")
)
REVIEW_GATE_STATE_FILENAME = "review_gate_state.json"
REVIEW_GATE_PREVIEW_MAX_CHARS = max(
    1200,
    int(os.environ.get("REVIEW_GATE_PREVIEW_MAX_CHARS", "6000") or 6000),
)
ALLOWED_FEEDBACK_TYPES = {"thumb", "numeric", "editorial"}
ALLOWED_REWRITE_SCOPES = {"ask_each_time", "section_only", "chapter_reflow"}
ALLOWED_REVIEW_ACTIONS = {"continue", "rewrite", "defer"}
REVIEW_GATE_ACTIVE_PAUSE_STATUSES = {"pause_requested", "paused", "deferred"}
REVIEW_GATE_RESUME_STATUSES = {"continue_requested", "rewrite_requested"}
ALLOWED_FEEDBACK_ISSUE_TAGS = {
    "canon_violation",
    "continuity_gap",
    "tone_mismatch",
    "pacing_problem",
    "structure_problem",
    "missing_world_detail",
    "character_voice",
    "clarity",
    "other",
}
TASK_LEDGER_PATH = Path(
    os.environ.get("AGENT_TASK_LEDGER_PATH", "/home/daravenrk/dragonlair/book_project/task_ledger.json")
)
PRESSURE_ENABLED = str(os.environ.get("AGENT_PRESSURE_ENABLED", "true")).lower() in {"1", "true", "yes", "on"}
PRESSURE_THRESHOLD = max(1, int(os.environ.get("AGENT_PRESSURE_QUEUE_THRESHOLD", "3")))
PRESSURE_HYSTERESIS_CLEAR = max(0, int(os.environ.get("AGENT_PRESSURE_QUEUE_CLEAR", "2")))
PRESSURE_MODELS = {
    item.strip()
    for item in str(os.environ.get("AGENT_PRESSURE_MODELS", "qwen3.5:9b,qwen3.5:4b")).split(",")
    if item.strip()
}
PRESSURE_PAUSE_PROFILES = {
    item.strip()
    for item in str(
        os.environ.get(
            "AGENT_PRESSURE_PAUSE_PROFILES",
            "book-publisher,book-continuity,book-canon,orchestrator,book-flow",
        )
    ).split(",")
    if item.strip()
}
PRESSURE_WRITER_PROFILES = {
    item.strip()
    for item in str(os.environ.get("AGENT_PRESSURE_WRITER_PROFILES", "book-writer")).split(",")
    if item.strip()
}
_pressure_mode = {"active": False, "last_depth": 0, "last_update": 0.0}
_resource_switch_state = {"mode": "normal", "last_switch_at": 0.0}
BOOK_AUTO_RESUME_ENABLED = str(os.environ.get("BOOK_AUTO_RESUME_ENABLED", "true")).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
BOOK_AUTO_RESUME_MAX_RETRIES = max(0, int(os.environ.get("BOOK_AUTO_RESUME_MAX_RETRIES", "6")))
BOOK_AUTO_RESUME_BACKOFF_SECONDS = max(
    5,
    int(os.environ.get("BOOK_AUTO_RESUME_BACKOFF_SECONDS", "45")),
)
BOOK_AUTO_RESUME_BACKOFF_MAX_SECONDS = max(
    BOOK_AUTO_RESUME_BACKOFF_SECONDS,
    int(os.environ.get("BOOK_AUTO_RESUME_BACKOFF_MAX_SECONDS", "600")),
)
SPAWN_PRE_CREATE_DELAY_SECONDS = max(
    0.0,
    float(os.environ.get("AGENT_SPAWN_PRECREATE_DELAY_SECONDS", "10")),
)
BOOK_RUN_STALL_SECONDS = max(
    120,
    int(os.environ.get("BOOK_RUN_STALL_SECONDS", "1800")),
)
BOOK_FLOW_STRATEGY_VERSION = str(
    os.environ.get("AGENT_STRATEGY_VERSION", "2026-03-20-strategy-v1")
).strip() or "2026-03-20-strategy-v1"
BOOK_FLOW_PREFLIGHT_ENABLED = str(os.environ.get("BOOK_FLOW_PREFLIGHT_ENABLED", "true")).lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_json_atomic(path: Path, payload: dict):
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_cli_runtime_activity(stale_seconds: float = 1800.0) -> List[dict]:
    if not CLI_RUNTIME_ACTIVITY_PATH.exists():
        return []
    try:
        payload = json.loads(CLI_RUNTIME_ACTIVITY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    runs = payload.get("active_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        return []
    now = time.time()
    filtered: List[dict] = []
    dirty = False
    for item in runs:
        if not isinstance(item, dict):
            dirty = True
            continue
        state = str(item.get("state") or "running")
        updated_at = float(item.get("updated_at_epoch") or item.get("started_at_epoch") or 0.0)
        run_dir_raw = str(item.get("run_dir") or "").strip()
        if state not in {"running", "starting", "recovering", "paused", "deferred"}:
            dirty = True
            continue
        if run_dir_raw and not Path(run_dir_raw).exists():
            dirty = True
            continue
        if updated_at and (now - updated_at) > stale_seconds:
            dirty = True
            continue
        filtered.append(item)
    if dirty:
        _write_json_atomic(CLI_RUNTIME_ACTIVITY_PATH, {"active_runs": filtered})
    return filtered


def _task_record_to_dict(record: TaskRecord) -> dict:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "status": record.status,
        "prompt": record.prompt,
        "direction": record.direction,
        "profile": record.profile,
        "route": record.route,
        "model": record.model,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "response": record.response,
        "error": record.error,
        "book_request": record.book_request,
        "retry_count": record.retry_count,
        "max_auto_retries": record.max_auto_retries,
        "hold": record.hold,
        "next_retry_at": record.next_retry_at,
        "production_status": record.production_status,
        "spawn_release_at": record.spawn_release_at,
        "spawn_requested_at": record.spawn_requested_at,
    }


def _task_record_from_dict(payload: dict) -> TaskRecord:
    return TaskRecord(
        id=str(payload.get("id") or uuid.uuid4()),
        created_at=float(payload.get("created_at") or time.time()),
        status=str(payload.get("status") or "queued"),
        prompt=str(payload.get("prompt") or ""),
        direction=payload.get("direction"),
        profile=payload.get("profile"),
        route=payload.get("route"),
        model=payload.get("model"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        response=payload.get("response"),
        error=payload.get("error"),
        book_request=payload.get("book_request"),
        retry_count=int(payload.get("retry_count") or 0),
        max_auto_retries=int(payload.get("max_auto_retries") or 0),
        hold=bool(payload.get("hold") or False),
        next_retry_at=payload.get("next_retry_at"),
        production_status=payload.get("production_status"),
        spawn_release_at=payload.get("spawn_release_at"),
        spawn_requested_at=payload.get("spawn_requested_at"),
    )


def _persist_tasks_locked(reason: str) -> None:
    payload = {
        "generated_at": time.time(),
        "reason": reason,
        "tasks": [_task_record_to_dict(rec) for rec in _tasks.values()],
    }
    _write_json_atomic(TASK_LEDGER_PATH, payload)


def _load_tasks_from_disk() -> Dict[str, TaskRecord]:
    if not TASK_LEDGER_PATH.exists():
        return {}
    try:
        raw = json.loads(TASK_LEDGER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    tasks_raw = raw.get("tasks") if isinstance(raw, dict) else None
    if not isinstance(tasks_raw, list):
        return {}

    loaded: Dict[str, TaskRecord] = {}
    for item in tasks_raw:
        if not isinstance(item, dict):
            continue
        try:
            rec = _task_record_from_dict(item)
        except (TypeError, ValueError):
            continue

        # Running tasks are considered interrupted across restarts and must be re-queued.
        if rec.status == "running":
            rec.status = "queued"
            rec.error = "Recovered after API restart while running"
            rec.started_at = None
            rec.finished_at = None
            rec.next_retry_at = None
        loaded[rec.id] = rec
    return loaded


def _append_ui_event(event_type: str, payload: dict):
    entry = {
        "ts": time.time(),
        "event": event_type,
        "payload": payload,
    }
    _ensure_parent(UI_EVENTS_PATH)
    with open(UI_EVENTS_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _append_resource_event(event_type: str, payload: dict):
    entry = {
        "ts": time.time(),
        "event": event_type,
        "payload": payload,
    }
    _ensure_parent(RESOURCE_EVENTS_PATH)
    with open(RESOURCE_EVENTS_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _append_jsonl(path: Path, payload: dict):
    _ensure_parent(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def _append_run_journal_event(run_dir: Optional[str], event_type: str, payload: dict):
    if not run_dir:
        return
    try:
        run_path = Path(run_dir)
        run_path.mkdir(parents=True, exist_ok=True)
        journal_path = run_path / "run_journal.jsonl"
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "event": event_type,
            "details": payload,
        }
        with open(journal_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except (OSError, TypeError, ValueError):
        pass


# Events that mark a run as irrevocably finished (no further progress expected).
_TERMINAL_RUN_EVENTS = frozenset({"run_success", "run_failure"})


def _ensure_run_journal_terminal(run_dir: Optional[str], task_id: str, reason: str) -> bool:
    """Write run_failure to run_journal.jsonl only when no terminal event already exists.

    Returns True if run_failure was written, False if a terminal event was already
    present (idempotent) or run_dir is missing/unreadable.
    """
    if not run_dir:
        return False
    try:
        journal_path = Path(run_dir) / "run_journal.jsonl"
        if journal_path.exists():
            for row in _read_jsonl(journal_path):
                if row.get("event") in _TERMINAL_RUN_EVENTS:
                    return False
    except (OSError, TypeError, ValueError):
        return False
    _append_run_journal_event(
        run_dir,
        "run_failure",
        {
            "task_id": task_id,
            "reason": reason,
            "emitted_by": "integrity_guard",
        },
    )
    return True


def _agent_health_summary(health_report: dict) -> dict:
    agents = (health_report or {}).get("agents") or {}
    summary = {
        "idle": 0,
        "running": 0,
        "healthy": 0,
        "hung": 0,
        "failed": 0,
        "hibernated": 0,
        "quarantined": 0,
    }
    now = time.time()
    for data in agents.values():
        display_state = str((data or {}).get("display_state") or "")
        state = display_state or str((data or {}).get("state") or "idle")
        if state in summary:
            summary[state] += 1
        quarantined_until = float((data or {}).get("quarantined_until") or 0.0)
        if not display_state and quarantined_until > now:
            summary["quarantined"] += 1
    summary["total_agents"] = len(agents)
    return summary


def _resource_switch_event_locked(mode: str, reason: str, depth: int):
    now = time.time()
    previous_mode = _resource_switch_state.get("mode", "normal")
    if previous_mode == mode:
        return

    _resource_switch_state["mode"] = mode
    _resource_switch_state["last_switch_at"] = now
    payload = {
        "from": previous_mode,
        "to": mode,
        "reason": reason,
        "nvidia_depth": depth,
        "at": now,
    }
    _append_ui_event("resource_switch", payload)
    _append_resource_event("resource_switch", payload)


def _build_resource_tracker_payload_locked(reason: str) -> dict:
    now = time.time()
    records = list(_tasks.values())
    status_counts = {"queued": 0, "running": 0, "paused": 0, "completed": 0, "failed": 0, "cancelled": 0}
    route_counts: Dict[str, int] = {}
    model_counts: Dict[str, int] = {}
    book_runtime_hints: Dict[str, dict] = {}
    cli_runs = _load_cli_runtime_activity(stale_seconds=float(max(BOOK_RUN_STALL_SECONDS, 1800)))

    for rec in records:
        if rec.status not in {"queued", "running"}:
            continue
        hint = _latest_book_stage_runtime_hint(rec)
        if hint:
            book_runtime_hints[rec.id] = hint

    for rec in records:
        if rec.status in status_counts:
            status_counts[rec.status] += 1
        if rec.status not in {"queued", "running"}:
            continue
        effective = _effective_runtime_target_for_record(rec, book_runtime_hints.get(rec.id))
        route = effective.get("route") or rec.route or "unknown"
        route_counts[route] = route_counts.get(route, 0) + 1
        model = effective.get("model") or rec.model
        if model:
            model_counts[model] = model_counts.get(model, 0) + 1

    for item in cli_runs:
        route = str(item.get("route") or "").strip()
        if route:
            route_counts[route] = route_counts.get(route, 0) + 1
        model = str(item.get("model") or "").strip()
        if model:
            model_counts[model] = model_counts.get(model, 0) + 1

    health_report = orchestrator.get_agent_health_report()
    pressure = dict(_pressure_mode)
    pressure["mode"] = "pressure" if pressure.get("active") else "normal"

    return {
        "generated_at": now,
        "reason": reason,
        "mode": _resource_switch_state.get("mode", "normal"),
        "switch": dict(_resource_switch_state),
        "pressure_mode": pressure,
        "queue": {
            "status_counts": status_counts,
            "route_active_counts": route_counts,
            "model_active_counts": model_counts,
            "nvidia_depth": _nvidia_pressure_depth(),
            "out_of_band_run_count": len(cli_runs),
        },
        "agents": {
            "summary": _agent_health_summary(health_report),
            "health": health_report,
        },
        "references": {
            "resource_tracker": str(RESOURCE_TRACKER_PATH),
            "resource_events": str(RESOURCE_EVENTS_PATH),
            "ui_state": str(UI_STATE_PATH),
            "ui_events": str(UI_EVENTS_PATH),
        },
    }


def _write_resource_tracker_locked(reason: str) -> dict:
    payload = _build_resource_tracker_payload_locked(reason=reason)
    _write_json_atomic(RESOURCE_TRACKER_PATH, payload)
    _persist_tasks_locked(reason=f"resource_tracker:{reason}")
    _append_resource_event("resource_check", {"reason": reason, "mode": payload.get("mode")})
    return payload


def _resource_snapshot(reason: str = "snapshot") -> dict:
    with _task_lock:
        return _write_resource_tracker_locked(reason=reason)


def _latest_ollama_ledger_entry() -> Optional[dict]:
    """Return the last line from the Ollama run ledger for live WebUI display."""
    try:
        ledger = Path(str(orchestrator.ollama_run_ledger_path))
        if not ledger.exists():
            return None
        last: Optional[dict] = None
        with open(ledger, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if raw_line:
                    try:
                        last = json.loads(raw_line)
                    except json.JSONDecodeError:
                        pass
        return last
    except (OSError, TypeError):
        return None


def _recent_quarantine_events(limit: int = 12) -> List[dict]:
    try:
        path = Path(str(orchestrator.quarantine_events_path))
        if not path.exists():
            return []
        rows = []
        for raw_line in path.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                item = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows
    except (OSError, TypeError):
        return []


def _latest_book_stage_runtime_hint(record: TaskRecord) -> Optional[dict]:
    """Return the latest route/model/profile selected by an active book-flow stage.

    The top-level `book-flow` task record only stores a coarse route/model guess.
    The actual runtime target for the currently executing stage is logged into the
    run's changes.log at `stage_start`.  The WebUI should reflect that effective
    stage route, especially when NVIDIA is active inside a book-flow task.
    """
    if getattr(record, "profile", None) != "book-flow" or not getattr(record, "book_request", None):
        return None
    try:
        req = BookFlowRequest(**record.book_request)
    except ValidationError:
        return None

    run_dir = _latest_book_run_dir(req.output_dir, req.title)
    if not run_dir:
        return None
    def _scan_lines(lines: List[str], source: str) -> Optional[dict]:
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # changes.log format
            if source == "changes":
                action = str(obj.get("action") or "")
                if action != "stage_start":
                    continue
                details = obj.get("details") or {}
                route = details.get("route")
                model = details.get("model")
                profile = details.get("profile") or obj.get("profile")
                stage = details.get("stage")
                if not any([route, model, profile, stage]):
                    continue
                return {
                    "task_id": record.id,
                    "run_dir": str(run_dir),
                    "stage": stage,
                    "agent": obj.get("agent"),
                    "profile": profile,
                    "route": route,
                    "model": model,
                    "correlation_id": details.get("correlation_id"),
                    "attempt": details.get("attempt"),
                    "ts": obj.get("timestamp"),
                    "source": "changes.log",
                }

            # run_journal format
            event = str(obj.get("event") or "")
            if event != "stage_attempt_start":
                continue
            details = obj.get("details") or {}
            route = details.get("route")
            model = details.get("model")
            profile = details.get("profile")
            stage = details.get("stage")
            agent = details.get("agent")
            if not any([route, model, profile, stage]):
                continue
            return {
                "task_id": record.id,
                "run_dir": str(run_dir),
                "stage": stage,
                "agent": agent,
                "profile": profile,
                "route": route,
                "model": model,
                "correlation_id": details.get("correlation_id"),
                "attempt": details.get("attempt"),
                "ts": obj.get("timestamp"),
                "source": "run_journal.jsonl",
            }

        return None

    changes_log = run_dir / "changes.log"
    if changes_log.exists():
        try:
            hint = _scan_lines(changes_log.read_text(encoding="utf-8").splitlines(), "changes")
            if hint:
                return hint
        except OSError:
            pass

    run_journal = run_dir / "run_journal.jsonl"
    if run_journal.exists():
        try:
            hint = _scan_lines(run_journal.read_text(encoding="utf-8").splitlines(), "journal")
            if hint:
                return hint
        except OSError:
            pass

    return None


def _effective_runtime_target_for_record(record: TaskRecord, runtime_hint: Optional[dict]) -> dict:
    effective_route = record.route
    effective_model = record.model
    effective_profile = record.profile

    if runtime_hint:
        if runtime_hint.get("route"):
            effective_route = runtime_hint.get("route")
        if runtime_hint.get("model"):
            effective_model = runtime_hint.get("model")
        if runtime_hint.get("profile"):
            effective_profile = runtime_hint.get("profile")

    return {
        "route": effective_route,
        "model": effective_model,
        "profile": effective_profile,
    }


def _build_status_payload(
    records: List[TaskRecord],
    fallback_used: Optional[bool] = None,
    fallback_stage: Optional[str] = None,
):
    queue_positions = _compute_queue_positions(records)
    book_runtime_hints: Dict[str, dict] = {}
    for rec in records:
        if rec.status not in {"queued", "running"}:
            continue
        hint = _latest_book_stage_runtime_hint(rec)
        if hint:
            book_runtime_hints[rec.id] = hint

    tasks = []
    fallback_stage = str(fallback_stage or "").strip()
    for v in sorted(records, key=lambda t: t.created_at, reverse=True)[:50]:
        task = _task_to_dict(v, queue_position=queue_positions.get(v.id))
        effective = _effective_runtime_target_for_record(v, book_runtime_hints.get(v.id))
        if effective.get("route"):
            task["route"] = effective.get("route")
        if effective.get("model"):
            task["model"] = effective.get("model")
        if effective.get("profile") and task.get("profile") == "book-flow":
            task["runtime_profile"] = effective.get("profile")
        if book_runtime_hints.get(v.id):
            task["runtime_stage"] = book_runtime_hints[v.id].get("stage")
            task["runtime_correlation_id"] = book_runtime_hints[v.id].get("correlation_id")

        # Optional filters for fallback provenance visibility in /api/status
        provenance = task.get("fallback_provenance_summary") or {}
        used_fallbacks = provenance.get("used_fallbacks") or []
        if not isinstance(used_fallbacks, list):
            used_fallbacks = []
        used_fallbacks = [str(stage) for stage in used_fallbacks if str(stage)]

        if fallback_used is True and not used_fallbacks:
            continue
        if fallback_used is False and used_fallbacks:
            continue
        if fallback_stage and fallback_stage not in used_fallbacks:
            continue

        tasks.append(task)

    fallback_integrity_blocks = []
    for task in tasks:
        summary = task.get("fallback_integrity_summary") or {}
        if not bool(summary.get("blocked")):
            continue
        provenance = task.get("fallback_provenance_summary") or {}
        fallback_integrity_blocks.append(
            {
                "task_id": task.get("id"),
                "status": task.get("status"),
                "hold": bool(task.get("hold")),
                "runtime_profile": task.get("runtime_profile") or task.get("profile"),
                "runtime_stage": task.get("runtime_stage"),
                "run_dir": summary.get("run_dir"),
                "issues": summary.get("all_issues") or summary.get("issues") or [],
                "used_fallbacks": provenance.get("used_fallbacks") or [],
                "human_review_recommended": bool(provenance.get("human_review_recommended")),
                "guidance": summary.get("guidance"),
            }
        )

    counts = {"queued": 0, "running": 0, "paused": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for task in tasks:
        st = task["status"]
        if st in counts:
            counts[st] += 1

    pending_spawn_groups = _build_pending_spawn_groups(records)

    # Find latest actionable stage event and gate failure from changes.log for book-flow tasks.
    latest_failure = None
    latest_stage_event = None
    def _book_record_priority(rec: TaskRecord):
        status = str(getattr(rec, "status", ""))
        if status == "running":
            bucket = 0
        elif status == "queued":
            bucket = 1
        elif status == "failed":
            bucket = 2
        elif status == "completed":
            bucket = 3
        else:
            bucket = 4
        return (bucket, -float(getattr(rec, "created_at", 0.0) or 0.0))

    prioritized_book_records = sorted(
        [rec for rec in records if getattr(rec, "profile", None) == "book-flow" and getattr(rec, "book_request", None)],
        key=_book_record_priority,
    )

    for rec in prioritized_book_records:
        if getattr(rec, "profile", None) == "book-flow" and getattr(rec, "book_request", None):
            req = BookFlowRequest(**rec.book_request)
            run_dir = _latest_book_run_dir(req.output_dir, req.title)
            if run_dir:
                changes_log = run_dir / "changes.log"
                if changes_log.exists():
                    for line in reversed(changes_log.read_text(encoding="utf-8").splitlines()):
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        action = str(obj.get("action") or "")
                        details = obj.get("details") or {}
                        if latest_stage_event is None and action in {"stage_start", "stage_result", "stage_complete", "stage_failure"}:
                            latest_stage_event = {
                                "action": action,
                                "stage": details.get("stage"),
                                "agent": obj.get("agent"),
                                "profile": obj.get("profile"),
                                "route": details.get("route"),
                                "model": details.get("model"),
                                "attempt": details.get("attempt"),
                                "gate_ok": details.get("gate_ok"),
                                "gate_message": details.get("gate_message"),
                                "ts": obj.get("timestamp"),
                            }

                        if action == "stage_failure" and latest_failure is None:
                            latest_failure = {
                                "stage": details.get("stage"),
                                "agent": obj.get("agent"),
                                "profile": obj.get("profile"),
                                "gate_message": details.get("gate_message"),
                                "attempt": details.get("attempt"),
                                "ts": obj.get("timestamp"),
                            }
                        if latest_failure is not None and latest_stage_event is not None:
                            break
                if latest_failure is None or latest_stage_event is None:
                    run_journal = run_dir / "run_journal.jsonl"
                    if run_journal.exists():
                        for line in reversed(run_journal.read_text(encoding="utf-8").splitlines()):
                            try:
                                obj = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            event = str(obj.get("event") or "")
                            details = obj.get("details") or {}

                            if latest_stage_event is None and event in {"stage_attempt_start", "stage_result", "stage_failure"}:
                                latest_stage_event = {
                                    "action": event,
                                    "stage": details.get("stage"),
                                    "agent": details.get("agent"),
                                    "profile": details.get("profile"),
                                    "route": details.get("route"),
                                    "model": details.get("model"),
                                    "attempt": details.get("attempt"),
                                    "gate_ok": details.get("gate_ok"),
                                    "gate_message": details.get("gate_message"),
                                    "ts": obj.get("timestamp"),
                                }

                            if latest_failure is None and event == "stage_failure":
                                latest_failure = {
                                    "stage": details.get("stage"),
                                    "agent": details.get("agent"),
                                    "profile": details.get("profile"),
                                    "route": details.get("route"),
                                    "model": details.get("model"),
                                    "gate_message": details.get("gate_message"),
                                    "attempt": details.get("attempt"),
                                    "ts": obj.get("timestamp"),
                                }

                            if latest_failure is not None and latest_stage_event is not None:
                                break
            if latest_failure is not None or latest_stage_event is not None:
                break

    health_report = orchestrator.get_agent_health_report()
    cli_runs = _load_cli_runtime_activity(stale_seconds=float(max(BOOK_RUN_STALL_SECONDS, 1800)))

    # Synthesize inflight running state from task queue route counts.
    # When a route has active (queued/running) tasks but the agent health shows idle,
    # the agent is actually busy waiting for or executing an LLM call — the health
    # dict only reflects in-RPC state which is momentarily idle between book-flow stages.
    route_active: Dict[str, List[str]] = {}
    for rec in records:
        if rec.status not in {"queued", "running"}:
            continue
        effective = _effective_runtime_target_for_record(rec, book_runtime_hints.get(rec.id))
        effective_route = effective.get("route")
        if effective_route:
            route_active.setdefault(effective_route, []).append(rec.id)
    # Also collect model per task-id for richer status_detail
    task_by_id: Dict[str, TaskRecord] = {rec.id: rec for rec in records}
    agents_health = (health_report or {}).get("agents") or {}
    for agent_name, info in agents_health.items():
        active_task_ids = route_active.get(agent_name, [])
        if not active_task_ids:
            continue
        current_display = str(info.get("display_state") or info.get("state") or "idle")
        if current_display in {"idle", ""}:
            # At least one task is active on this route — promote to running
            sample_task = task_by_id.get(active_task_ids[0])
            effective = _effective_runtime_target_for_record(sample_task, book_runtime_hints.get(active_task_ids[0])) if sample_task else {}
            detail = f"inflight via task queue ({len(active_task_ids)} active)"
            if effective.get("model"):
                detail += f" model={effective.get('model')}"
            if sample_task and sample_task.status == "running":
                detail += f" task={active_task_ids[0][:8]}"
            info["display_state"] = "running"
            info["status_detail"] = detail
            info["route_inflight"] = len(active_task_ids)
            if effective.get("profile"):
                info["current_profile"] = effective.get("profile")
            if effective.get("model"):
                info["current_model"] = effective.get("model")
            hint = book_runtime_hints.get(active_task_ids[0])
            if hint:
                stage = hint.get("stage") or "book-flow"
                info["current_task_excerpt"] = f"stage={stage} task={active_task_ids[0][:8]}"

    cli_by_route: Dict[str, List[dict]] = {}
    for item in cli_runs:
        route = str(item.get("route") or "").strip()
        if route:
            cli_by_route.setdefault(route, []).append(item)

    for agent_name, items in cli_by_route.items():
        info = agents_health.get(agent_name)
        if not info or not items:
            continue
        sample = max(items, key=lambda entry: float(entry.get("updated_at_epoch") or entry.get("started_at_epoch") or 0.0))
        current_display = str(info.get("display_state") or info.get("state") or "idle")
        if current_display in {"idle", ""}:
            info["display_state"] = "running"
        info["route_inflight"] = max(int(info.get("route_inflight") or 0), len(items))
        info["status_detail"] = (
            f"out-of-band CLI book-flow ({len(items)} active)"
            + (f" model={sample.get('model')}" if sample.get("model") else "")
        )
        info["activity_source"] = "cli-book-flow"
        if sample.get("profile"):
            info["current_profile"] = sample.get("profile")
        if sample.get("model"):
            info["current_model"] = sample.get("model")
        stage = sample.get("stage") or "book-flow"
        run_id = str(sample.get("run_id") or "")[:8]
        info["current_task_excerpt"] = f"stage={stage} run={run_id} cli"

    resource_tracker = _resource_snapshot(reason="status_payload")
    return {
        "source": "flat_file",
        "generated_at": time.time(),
        "pressure_mode": dict(_pressure_mode),
        "resource_tracker": resource_tracker,
        "health": health_report,
        "task_counts": counts,
        "tasks": tasks,
        "pending_spawn_groups": pending_spawn_groups,
        "fallback_integrity_blocks": fallback_integrity_blocks,
        "latest_gate_failure": latest_failure,
        "latest_stage_event": latest_stage_event,
        "latest_ollama_call": _latest_ollama_ledger_entry(),
        "recent_quarantine_events": _recent_quarantine_events(),
        "out_of_band_runs": cli_runs,
        "crontab_next_execution": _calculate_next_crontab_execution(),
    }


def _build_pending_spawn_groups(records: List[TaskRecord]) -> List[dict]:
    now = time.time()
    groups: Dict[str, dict] = {}

    for rec in sorted(records, key=lambda t: t.created_at):
        release_at = float(rec.spawn_release_at or 0.0)
        if rec.status != "queued" or release_at <= now:
            continue

        group_name = rec.profile or "auto"
        group = groups.setdefault(
            group_name,
            {
                "group": group_name,
                "count": 0,
                "agents": [],
            },
        )

        prompt = (rec.prompt or "").strip().replace("\n", " ")
        purpose = prompt[:140]
        if len(prompt) > 140:
            purpose += "..."

        group["count"] += 1
        group["agents"].append(
            {
                "task_id": rec.id,
                "name": rec.profile or "auto",
                "purpose": purpose,
                "run": rec.id[:8],
                "remaining_seconds": max(0, int(round(release_at - now))),
                "release_at": release_at,
            }
        )

    return sorted(groups.values(), key=lambda item: item["group"])


def _set_spawn_release_locked(record: TaskRecord, delay_seconds: float = SPAWN_PRE_CREATE_DELAY_SECONDS) -> None:
    now = time.time()
    record.spawn_requested_at = now
    record.spawn_release_at = now + max(0.0, float(delay_seconds))


def _schedule_spawn(task_id: str, runner, *runner_args):
    def delayed_spawn():
        while True:
            with _task_lock:
                record = _tasks.get(task_id)
                if not record:
                    return
                if record.status != "queued":
                    return
                release_at = float(record.spawn_release_at or 0.0)

            if release_at <= time.time():
                break
            time.sleep(min(0.5, release_at - time.time()))

        with _task_lock:
            record = _tasks.get(task_id)
            if not record or record.status != "queued":
                return
            record.spawn_release_at = None

        executor.submit(runner, task_id, *runner_args)

    threading.Thread(target=delayed_spawn, daemon=True).start()


def _book_task_review_gate_status(record: TaskRecord, req: Optional[BookFlowRequest] = None) -> str:
    if record.profile != "book-flow":
        return ""
    if req is None:
        if not record.book_request:
            return ""
        try:
            req = BookFlowRequest(**record.book_request)
        except ValidationError:
            return ""

    record.production_status = _analyze_book_production(req)
    review_gate = (record.production_status or {}).get("review_gate") or {}
    return str(review_gate.get("status") or "").strip().lower()


def _bootstrap_task_ledger():
    loaded = _load_tasks_from_disk()
    if not loaded:
        return

    queued_for_resume: List[tuple] = []
    with _task_lock:
        _tasks.clear()
        _tasks.update(loaded)

        for task_id, record in _tasks.items():
            if record.profile == "book-flow" and record.book_request:
                try:
                    req = BookFlowRequest(**record.book_request)
                except ValidationError:
                    record.status = "failed"
                    record.error = "Invalid persisted book request payload"
                    record.finished_at = time.time()
                    continue

                gate_status = _book_task_review_gate_status(record, req)
                if record.status == "paused":
                    # A restarted API process has no live runner for paused tasks.
                    # Clear runtime-only fields so later review actions can safely
                    # detect that resume must requeue the task.
                    record.started_at = None
                    record.spawn_release_at = None
                    record.next_retry_at = None
                    if gate_status in REVIEW_GATE_RESUME_STATUSES:
                        record.status = "queued"
                        record.hold = False
                        _set_spawn_release_locked(record, delay_seconds=0.0)
                        queued_for_resume.append((task_id, _run_book_task, req))
                        continue
                    if gate_status in REVIEW_GATE_ACTIVE_PAUSE_STATUSES:
                        record.hold = True

            if record.status != "queued":
                continue
            _set_spawn_release_locked(record, delay_seconds=0.0)
            if record.profile == "book-flow" and record.book_request:
                try:
                    req = BookFlowRequest(**record.book_request)
                except ValidationError:
                    record.status = "failed"
                    record.error = "Invalid persisted book request payload"
                    record.finished_at = time.time()
                    continue
                fallback_integrity = (record.production_status or {}).get("fallback_integrity") or {}
                _fi_failed, _fi_issues, _fi_stages = _any_fallback_stage_failed(fallback_integrity)
                if _fi_failed:
                    record.status = "failed"
                    record.hold = True
                    record.finished_at = time.time()
                    record.next_retry_at = None
                    record.error = (
                        "Startup blocked auto-resume due to fallback integrity failure in stage(s): "
                        + ", ".join(_fi_stages) + "; operator review required"
                    )
                    _append_run_journal_event(
                        (record.production_status or {}).get("run_dir"),
                        "fallback_integrity_failed",
                        {
                            "task_id": task_id,
                            "source": "task_ledger_bootstrap",
                            "stages": _fi_stages,
                            "issues": _fi_issues,
                        },
                    )
                    continue
                queued_for_resume.append((task_id, _run_book_task, req))
            else:
                queued_for_resume.append((task_id, _run_task))

        _update_pressure_state_locked()
        _write_resource_tracker_locked(reason="task_ledger_bootstrap")

    for item in queued_for_resume:
        task_id = item[0]
        runner = item[1]
        args = item[2:] if len(item) > 2 else ()
        _schedule_spawn(task_id, runner, *args)

    _refresh_ui_state_snapshot(event_type="task_ledger_bootstrap")


def _refresh_ui_state_snapshot(event_type: str = "snapshot"):
    with _task_lock:
        records = list(_tasks.values())
    payload = _build_status_payload(records)
    _write_json_atomic(UI_STATE_PATH, payload)
    _append_ui_event(event_type, {"task_count": len(payload.get("tasks", []))})
    return payload


def _read_ui_state_snapshot():
    if not UI_STATE_PATH.exists():
        return _refresh_ui_state_snapshot(event_type="bootstrap")
    try:
        return json.loads(UI_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _refresh_ui_state_snapshot(event_type="repair")


# Store the latest correction in memory for now (could be persisted if needed)
_latest_correction = {"text": None, "ts": 0.0}


def _calculate_next_crontab_execution() -> dict:
    """
    Calculate the next scheduled crontab execution time.
    Crontab runs every 5 minutes: 38-59/5 * * * * and 0-37/5 * * * *
    This simplifies to: runs at 0, 5, 10, 15, 20, 25, 30, 35, 38, 43, 48, 53, 58 of each hour.
    """
    import datetime
    now = datetime.datetime.now()
    current_minute = now.minute
    
    # Define all valid minutes in the hour when cron runs
    valid_minutes = [0, 5, 10, 15, 20, 25, 30, 35, 38, 43, 48, 53, 58]
    
    # Find next execution minute
    next_minute = None
    for vm in valid_minutes:
        if vm > current_minute:
            next_minute = vm
            break
    
    if next_minute is None:
        # Next execution is in the next hour
        next_minute = valid_minutes[0]
        next_hour = (now.hour + 1) % 24
        next_exec = now.replace(hour=next_hour, minute=next_minute, second=0, microsecond=0)
    else:
        next_exec = now.replace(minute=next_minute, second=0, microsecond=0)
    
    time_until = (next_exec - now).total_seconds()
    return {
        "next_execution_ts": next_exec.timestamp(),
        "next_execution_iso": next_exec.isoformat(),
        "seconds_until": max(0, time_until),
        "next_execution_minute": next_minute,
    }


def _find_latest_failed_book_task():
    with _task_lock:
        failed = [record for record in _tasks.values() if record.profile == "book-flow" and record.status == "failed"]
        if not failed:
            return None
        return max(failed, key=lambda record: record.finished_at or 0)


@app.post("/api/submit-correction")
def submit_correction(payload: dict = Body(...)):
    correction = (payload or {}).get("correction", "").strip()
    if not correction:
        raise HTTPException(status_code=400, detail="Correction text required.")

    _latest_correction["text"] = correction
    _latest_correction["ts"] = time.time()

    record = _find_latest_failed_book_task()
    if not record:
        raise HTTPException(status_code=404, detail="No failed book-flow task to retry.")

    req_dict = dict(record.book_request or {})
    if not req_dict:
        raise HTTPException(status_code=500, detail="No book request found for failed task.")

    req_dict["section_goal"] = f"{req_dict.get('section_goal', '')}\n[Correction: {correction}]"
    new_req = BookFlowRequest(**req_dict)
    new_id = uuid.uuid4().hex
    new_record = TaskRecord(
        id=new_id,
        created_at=time.time(),
        status="queued",
        prompt=record.prompt,
        direction=record.direction,
        profile="book-flow",
        route=record.route,
        model=record.model,
        book_request=req_dict,
        retry_count=0,
        max_auto_retries=record.max_auto_retries,
    )
    with _task_lock:
        _set_spawn_release_locked(new_record)
        _tasks[new_id] = new_record
    _schedule_spawn(new_id, _run_book_task, new_req)
    _refresh_ui_state_snapshot(event_type="user_correction_submitted")
    return {"ok": True, "message": "Correction submitted and retry started."}


@app.post("/api/book-feedback")
def submit_book_feedback(req: WritingFeedbackRequest):
    _validate_feedback_request(req)
    resolved_run_dir = _resolve_feedback_run_dir(req)
    run_dir_str = str(resolved_run_dir) if resolved_run_dir else (str(req.run_dir or "").strip() or None)
    linked_record = None
    runtime_hint = None

    if req.task_id:
        with _task_lock:
            linked_record = _tasks.get(str(req.task_id))
            if linked_record:
                runtime_hint = _latest_book_stage_runtime_hint(linked_record)

    feedback_id = uuid.uuid4().hex
    event = {
        "feedback_id": feedback_id,
        "timestamp": time.time(),
        "reviewer": str(req.reviewer or "operator").strip() or "operator",
        "task_id": str(req.task_id or "").strip() or None,
        "run_dir": run_dir_str,
        "run_id": Path(run_dir_str).name if run_dir_str else None,
        "title": str(req.title or "").strip() or None,
        "chapter_number": req.chapter_number,
        "section_index": req.section_index,
        "section_title": str(req.section_title or "").strip() or None,
        "stage_id": str(req.stage_id or "").strip() or None,
        "approved": bool(req.approved),
        "needs_rewrite": bool(req.needs_rewrite),
        "score": float(req.score),
        "comment": str(req.comment or "").strip(),
        "feedback_type": str(req.feedback_type or "thumb").strip().lower(),
        "issue_tags": [str(tag).strip().lower() for tag in (req.issue_tags or []) if str(tag).strip()],
        "rewrite_scope": str(req.rewrite_scope or "ask_each_time").strip().lower(),
        "pause_before_continue": bool(req.pause_before_continue),
        "assistant_rewrite_requested": bool(req.assistant_rewrite_requested),
        "selected_text_range": req.selected_text_range.model_dump() if req.selected_text_range else None,
        "correlation_id": (runtime_hint or {}).get("correlation_id"),
    }
    _append_jsonl(BOOK_FEEDBACK_PATH, event)

    review_gate_state = None
    if event.get("pause_before_continue") and run_dir_str:
        review_gate_state = _write_review_gate_state(
            run_dir_str,
            {
                "status": "pause_requested",
                "feedback_id": feedback_id,
                "task_id": event.get("task_id"),
                "run_id": event.get("run_id"),
                "chapter_number": event.get("chapter_number"),
                "section_index": event.get("section_index"),
                "section_title": event.get("section_title"),
                "stage_id": event.get("stage_id"),
                "comment": event.get("comment"),
                "issue_tags": event.get("issue_tags"),
                "rewrite_scope": event.get("rewrite_scope"),
                "assistant_rewrite_requested": event.get("assistant_rewrite_requested"),
                "reviewer": event.get("reviewer"),
                "requested_at_epoch": time.time(),
                "correlation_id": event.get("correlation_id"),
            },
        )
        _append_run_journal_event(
            run_dir_str,
            "human_review_pause_requested",
            {
                "feedback_id": feedback_id,
                "task_id": event.get("task_id"),
                "chapter_number": event.get("chapter_number"),
                "section_index": event.get("section_index"),
                "section_title": event.get("section_title"),
                "stage_id": event.get("stage_id"),
                "rewrite_scope": event.get("rewrite_scope"),
                "correlation_id": event.get("correlation_id"),
            },
        )
        with _task_lock:
            if linked_record and linked_record.profile == "book-flow":
                linked_record.hold = True
                if linked_record.status == "running":
                    linked_record.status = "paused"
                _write_resource_tracker_locked(reason="book_feedback_pause_requested")
                _update_pressure_state_locked()

    # Feed Todo 173 signal into reward events stream for downstream training tuples.
    _append_jsonl(
        orchestrator.agent_reward_events_path,
        {
            "timestamp": time.time(),
            "profile": None,
            "reason": "human_writing_feedback",
            "delta": 0,
            "tokens_before": None,
            "tokens_after": None,
            "details": {
                "feedback_id": feedback_id,
                "task_id": event.get("task_id"),
                "run_dir": event.get("run_dir"),
                "run_id": event.get("run_id"),
                "chapter_number": event.get("chapter_number"),
                "section_index": event.get("section_index"),
                "section_title": event.get("section_title"),
                "stage_id": event.get("stage_id"),
                "approved": event.get("approved"),
                "needs_rewrite": event.get("needs_rewrite"),
                "score": event.get("score"),
                "comment": event.get("comment"),
                "feedback_type": event.get("feedback_type"),
                "issue_tags": event.get("issue_tags"),
                "rewrite_scope": event.get("rewrite_scope"),
                "pause_before_continue": event.get("pause_before_continue"),
                "assistant_rewrite_requested": event.get("assistant_rewrite_requested"),
                "selected_text_range": event.get("selected_text_range"),
            },
        },
    )
    _append_resource_event("book_feedback_submitted", event)
    _refresh_ui_state_snapshot(event_type="book_feedback_submitted")
    return {
        "ok": True,
        "feedback_id": feedback_id,
        "run_dir": run_dir_str,
        "linked": bool(run_dir_str),
        "review_gate_state": review_gate_state,
    }


@app.get("/api/book-feedback")
def list_book_feedback(
    run_id: Optional[str] = None,
    chapter_number: Optional[int] = None,
    section_index: Optional[int] = None,
    stage_id: Optional[str] = None,
    needs_rewrite: Optional[bool] = None,
    limit: int = 100,
):
    rows = _read_jsonl(BOOK_FEEDBACK_PATH)
    filtered: List[dict] = []

    normalized_stage = str(stage_id or "").strip()
    target_run_id = str(run_id or "").strip()
    max_rows = max(1, min(int(limit), 500))

    for item in reversed(rows):
        if not isinstance(item, dict):
            continue
        if target_run_id and str(item.get("run_id") or "") != target_run_id:
            continue
        if chapter_number is not None and int(item.get("chapter_number") or 0) != int(chapter_number):
            continue
        if section_index is not None and int(item.get("section_index") or 0) != int(section_index):
            continue
        if normalized_stage and str(item.get("stage_id") or "") != normalized_stage:
            continue
        if needs_rewrite is not None and bool(item.get("needs_rewrite")) != bool(needs_rewrite):
            continue
        filtered.append(item)
        if len(filtered) >= max_rows:
            break

    return {
        "count": len(filtered),
        "items": filtered,
        "source": str(BOOK_FEEDBACK_PATH),
    }


def _compose_prompt(direction: Optional[str], prompt: str) -> str:
    if direction and direction.strip():
        return f"Direction:\n{direction.strip()}\n\nTask:\n{prompt.strip()}"
    return prompt.strip()


def _require_openclaw_mode():
    mode = getattr(orchestrator, "server_mode", "standard")
    if mode not in {"openclaw-client", "openclaw"}:
        raise HTTPException(
            status_code=503,
            detail="OpenClaw-compatible endpoints are available only in openclaw-client mode",
        )


def _load_openclaw_model_profile_map() -> Dict[str, str]:
    """
    Supports either JSON or comma-delimited mapping.

    JSON example:
    OPENCLAW_MODEL_PROFILE_MAP={"openclaw-fast":"nvidia-fast","openclaw-deep":"amd-writer"}

    CSV example:
    OPENCLAW_MODEL_PROFILE_MAP=openclaw-fast=nvidia-fast,openclaw-deep=amd-writer
    """
    raw = str(os.environ.get("OPENCLAW_MODEL_PROFILE_MAP", "")).strip()
    if not raw:
        return {}

    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            return {}
        return {}

    mapping: Dict[str, str] = {}
    for item in raw.split(","):
        chunk = item.strip()
        if not chunk or "=" not in chunk:
            continue
        model_name, profile_name = chunk.split("=", 1)
        model_name = model_name.strip()
        profile_name = profile_name.strip()
        if model_name and profile_name:
            mapping[model_name] = profile_name
    return mapping


def _validate_openclaw_profile_config():
    mode = getattr(orchestrator, "server_mode", "standard")
    if mode not in {"openclaw-client", "openclaw"}:
        return
    if not STRICT_MODE_VALIDATION:
        return

    available_profiles = {p.get("name") for p in orchestrator.profiles}
    fast_profile = os.environ.get("OPENCLAW_FAST_PROFILE", "nvidia-fast")
    deep_profile = os.environ.get("OPENCLAW_DEEP_PROFILE", "amd-writer")
    tool_profile = os.environ.get("OPENCLAW_TOOL_PROFILE", "amd-coder")
    priority_profile = os.environ.get("OPENCLAW_PRIORITY_PROFILE", "nvidia-fast")

    for profile_name in {fast_profile, deep_profile, tool_profile, priority_profile}:
        if profile_name not in available_profiles:
            raise OpenClawProfileConfigError(
                f"OpenClaw profile is not loaded/available: {profile_name}",
                details={"profile": profile_name},
            )

    mapping = _load_openclaw_model_profile_map()
    for model_name, profile_name in mapping.items():
        if profile_name not in available_profiles:
            raise OpenClawProfileConfigError(
                f"OPENCLAW_MODEL_PROFILE_MAP maps '{model_name}' to unknown profile '{profile_name}'",
                details={"model": model_name, "profile": profile_name},
            )


def _select_openclaw_profile(req: OpenClawCompatChatRequest) -> str:
    fast_profile = os.environ.get("OPENCLAW_FAST_PROFILE", "nvidia-fast")
    deep_profile = os.environ.get("OPENCLAW_DEEP_PROFILE", "amd-writer")
    tool_profile = os.environ.get("OPENCLAW_TOOL_PROFILE", "amd-coder")
    priority_profile = os.environ.get("OPENCLAW_PRIORITY_PROFILE", "nvidia-fast")
    priority_by_default = str(os.environ.get("OPENCLAW_PRIORITY_BY_DEFAULT", "true")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    priority_models_raw = str(
        os.environ.get("OPENCLAW_PRIORITY_MODELS", "qwen3.5:4b,dragonlair-active:latest")
    )
    priority_models = {item.strip() for item in priority_models_raw.split(",") if item.strip()}

    # Standard pattern: use tool-capable profile only when tools are present.
    if req.tools:
        return tool_profile

    model_map = _load_openclaw_model_profile_map()
    direct = model_map.get(req.model)
    if direct and direct != tool_profile:
        return direct

    if req.model and req.model in priority_models:
        return priority_profile

    if not req.model and priority_by_default:
        return priority_profile

    model_hint = (req.model or "").lower()
    if "fast" in model_hint or "small" in model_hint or "instruction" in model_hint:
        return fast_profile
    return deep_profile


_validate_openclaw_profile_config()


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content)


def _messages_to_prompt(messages: List[OpenClawCompatMessage], tools: Optional[List[dict]] = None) -> str:
    lines = []
    for msg in messages:
        role = (msg.role or "user").strip().lower()
        text = _extract_text(msg.content)
        if text:
            lines.append(f"[{role}] {text}")

    if tools:
        lines.append("[system] Tools are available. If a tool is needed, explain tool intent clearly in output.")
        lines.append("[system] Tool schemas: " + json.dumps(tools))

    return "\n\n".join(lines).strip()


def _openclaw_compat_now() -> int:
    return int(time.time())


def _openclaw_compat_response(content: str, req_model: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": _openclaw_compat_now(),
        "model": req_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def _compute_queue_positions(records):
    # Queue position is route-local and only for queued tasks.
    # Position starts at 1 for the next queued task behind currently running work.
    by_route = {}
    for rec in sorted(records, key=lambda t: t.created_at):
        route = rec.route or "unknown"
        route_state = by_route.setdefault(route, {"running": 0, "queued": []})
        if rec.status == "running":
            route_state["running"] += 1
        elif rec.status == "queued":
            route_state["queued"].append(rec)

    positions = {}
    for route, state in by_route.items():
        base = state["running"]
        for idx, rec in enumerate(state["queued"], start=1):
            positions[rec.id] = base + idx
    return positions


def _fallback_repair_guidance(issues: List[str]) -> str:
    issues = [str(item) for item in (issues or []) if str(item)]
    if not issues:
        return "Inspect 03_canon fallback artifacts, regenerate canon fallback if needed, then clear hold and retry."

    if "fallback_checksum_mismatch" in issues:
        return "canon.json no longer matches canon_fallback_metadata.json; restore matching artifacts or regenerate the canon fallback before retrying."
    if "fallback_contract_failed" in issues:
        return "fallback_contract_report.json indicates missing semantic anchors; repair or regenerate the canon fallback before retrying."
    if any(issue in issues for issue in {"fallback_metadata_missing", "fallback_metadata_invalid_json", "fallback_metadata_flag_invalid", "fallback_metadata_stage_mismatch"}):
        return "canon fallback metadata is missing or invalid; recreate canon_fallback_metadata.json from a fresh fallback generation before retrying."
    if any(issue in issues for issue in {"fallback_contract_missing", "fallback_contract_invalid_json"}):
        return "fallback contract report is missing or invalid; regenerate fallback_contract_report.json from a verified canon fallback before retrying."
    if any(issue in issues for issue in {"fallback_checksum_missing", "fallback_payload_missing_or_invalid"}):
        return "canon fallback payload or checksum is incomplete; regenerate canon fallback artifacts before retrying."
    if "fallback_artifact_stale" in issues:
        return "fallback artifact is stale; regenerate the stage fallback from fresh inputs before retrying (avoid manual artifact patching)."
    if any(issue in issues for issue in {"fallback_generated_at_missing", "fallback_generated_at_unparseable"}):
        return "fallback metadata generated_at is missing or invalid; regenerate fallback metadata and contract artifacts before retrying."

    return "Inspect 03_canon fallback artifacts, repair integrity mismatches, then clear hold and retry."


def _fallback_integrity_summary(record: TaskRecord) -> Optional[dict]:
    production_status = record.production_status if isinstance(record.production_status, dict) else {}
    fallback_integrity = production_status.get("fallback_integrity")
    if not isinstance(fallback_integrity, dict):
        return None

    # Aggregate across all registered stages
    stage_summaries: dict = {}
    for stage, integrity in fallback_integrity.items():
        if not isinstance(integrity, dict):
            continue
        checked = bool(integrity.get("checked"))
        valid = bool(integrity.get("valid", True))
        stage_blocked = checked and (not valid) and bool(record.hold)
        stage_issues = [str(i) for i in (integrity.get("issues") or []) if str(i)]
        stage_summaries[stage] = {
            "checked": checked,
            "valid": valid,
            "blocked": stage_blocked,
            "reason": integrity.get("reason"),
            "issues": stage_issues,
        }

    any_checked = any(s.get("checked") for s in stage_summaries.values())
    any_invalid = any(not s.get("valid", True) for s in stage_summaries.values())
    if not any_checked or not any_invalid:
        return None

    all_issues = [i for s in stage_summaries.values() for i in s.get("issues", [])]
    blocked_stages = [st for st, sv in stage_summaries.items() if sv.get("blocked")]
    any_blocked = bool(blocked_stages)

    return {
        "checked": any_checked,
        "valid": not any_invalid,
        "blocked": any_blocked,
        "stages": stage_summaries,
        "blocked_stages": blocked_stages,
        "all_issues": all_issues,
        "run_dir": production_status.get("run_dir"),
        "guidance": _fallback_repair_guidance(all_issues) if any_blocked else None,
    }


def _fallback_provenance_summary(record: TaskRecord) -> Optional[dict]:
    """Load fallback provenance from run_summary.json for status payload visibility."""
    production_status = record.production_status if isinstance(record.production_status, dict) else {}
    run_dir_raw = production_status.get("run_dir")
    if not run_dir_raw:
        return None

    run_dir = Path(str(run_dir_raw))
    run_summary = _read_json_file(run_dir / "run_summary.json")
    if not isinstance(run_summary, dict):
        return None

    raw_used = run_summary.get("used_fallbacks")
    used_fallbacks = [str(stage) for stage in raw_used if str(stage)] if isinstance(raw_used, list) else []
    raw_provenance = run_summary.get("fallback_provenance")
    if isinstance(raw_provenance, dict):
        human_review_recommended = bool(raw_provenance.get("human_review_recommended", bool(used_fallbacks)))
        note = str(raw_provenance.get("note") or "")
    else:
        human_review_recommended = bool(used_fallbacks)
        note = "One or more deterministic stage fallbacks were used in this run." if used_fallbacks else ""

    return {
        "used_fallbacks": used_fallbacks,
        "used_fallback_count": len(used_fallbacks),
        "human_review_recommended": human_review_recommended,
        "note": note,
        "run_dir": str(run_dir),
    }


def _task_to_dict(record: TaskRecord, queue_position=None):
    payload = {
        "id": record.id,
        "created_at": record.created_at,
        "status": record.status,
        "prompt": record.prompt,
        "direction": record.direction,
        "profile": record.profile,
        "route": record.route,
        "model": record.model,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "response": record.response,
        "error": record.error,
        "retry_count": record.retry_count,
        "max_auto_retries": record.max_auto_retries,
        "hold": record.hold,
        "next_retry_at": record.next_retry_at,
        "production_status": record.production_status,
        "queue_position": queue_position,
        "spawn_release_at": record.spawn_release_at,
        "spawn_requested_at": record.spawn_requested_at,
    }
    fallback_summary = _fallback_integrity_summary(record)
    if fallback_summary:
        payload["fallback_integrity_summary"] = fallback_summary
    fallback_provenance = _fallback_provenance_summary(record)
    if fallback_provenance:
        payload["fallback_provenance_summary"] = fallback_provenance
    return payload


def _find_route_model_conflict(route: Optional[str], model: Optional[str]):
    if not route or not model:
        return None

    def _is_nvidia_tiny(candidate: Optional[str]) -> bool:
        return bool(candidate) and route == NVIDIA_ROUTE_NAME and candidate in NVIDIA_TINY_MODELS

    def _allow_nvidia_tiny_mix(requested_model: Optional[str]) -> bool:
        if not NVIDIA_ALLOW_MIXED_TINY_MODELS:
            return False
        if route != NVIDIA_ROUTE_NAME:
            return False
        if not _is_nvidia_tiny(requested_model):
            return False

        active_models = {
            rec.model
            for rec in _tasks.values()
            if rec.route == route and rec.status in {"queued", "running"} and rec.model
        }
        if not active_models:
            return True
        if requested_model in active_models:
            return True
        if any(model_name not in NVIDIA_TINY_MODELS for model_name in active_models):
            return False
        return len(active_models) < NVIDIA_TINY_MAX_ACTIVE_MODELS

    for rec in _tasks.values():
        if rec.route != route:
            continue
        if rec.status not in {"queued", "running"}:
            continue
        if rec.model and rec.model != model:
            if _allow_nvidia_tiny_mix(model) and _is_nvidia_tiny(rec.model):
                continue
            return rec.model
    return None


def _latest_book_run_dir(output_dir: str, title: str) -> Optional[Path]:
    base = Path(output_dir).expanduser() / slugify(title) / "runs"
    runs = sorted(base.glob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    return runs[0] if runs else None


def _review_gate_state_path(run_dir: Optional[str]) -> Optional[Path]:
    run_dir_str = str(run_dir or "").strip()
    if not run_dir_str:
        return None
    return Path(run_dir_str).expanduser() / "handoff" / REVIEW_GATE_STATE_FILENAME


def _read_text_preview(path_value: Any, max_chars: int = REVIEW_GATE_PREVIEW_MAX_CHARS) -> Optional[dict]:
    path_str = str(path_value or "").strip()
    if not path_str:
        return None
    path = Path(path_str).expanduser()
    if not path.exists() or not path.is_file():
        return {
            "path": str(path),
            "exists": False,
            "text": None,
            "truncated": False,
            "char_count": 0,
        }
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {
            "path": str(path),
            "exists": True,
            "text": None,
            "truncated": False,
            "char_count": 0,
        }

    preview_text = text[:max_chars]
    return {
        "path": str(path),
        "exists": True,
        "text": preview_text,
        "truncated": len(text) > len(preview_text),
        "char_count": len(text),
    }


def _read_review_gate_state(run_dir: Optional[str]) -> dict:
    path = _review_gate_state_path(run_dir)
    if path is None:
        return {}
    payload = _read_json_file(path)
    if not isinstance(payload, dict):
        return {}
    section_preview = _read_text_preview(payload.get("section_path"))
    if section_preview is not None:
        payload["section_preview"] = section_preview
    review_preview = _read_text_preview(payload.get("section_review_path"))
    if review_preview is not None:
        payload["section_review_preview"] = review_preview
    return payload


def _write_review_gate_state(run_dir: Optional[str], patch: dict) -> dict:
    path = _review_gate_state_path(run_dir)
    if path is None:
        return {}
    existing = _read_json_file(path)
    payload = existing if isinstance(existing, dict) else {}
    payload.update(patch or {})
    payload["updated_at_epoch"] = time.time()
    _write_json_atomic(path, payload)
    return payload


def _resolve_feedback_run_dir(req: WritingFeedbackRequest) -> Optional[Path]:
    if req.run_dir:
        candidate = Path(str(req.run_dir)).expanduser()
        return candidate if candidate.exists() else None

    if req.task_id:
        with _task_lock:
            rec = _tasks.get(str(req.task_id))
        if rec and isinstance(rec.book_request, dict):
            req_title = str((rec.book_request or {}).get("title") or "").strip()
            req_output_dir = str((rec.book_request or {}).get("output_dir") or "/home/daravenrk/dragonlair/book_project").strip()
            if req_title:
                return _latest_book_run_dir(req_output_dir, req_title)

    if req.title:
        out_dir = str(req.output_dir or "/home/daravenrk/dragonlair/book_project").strip()
        return _latest_book_run_dir(out_dir, str(req.title))

    return None


def _validate_feedback_request(req: WritingFeedbackRequest) -> None:
    if req.approved and req.needs_rewrite:
        raise HTTPException(status_code=400, detail="approved and needs_rewrite cannot both be true")
    if req.score < 0 or req.score > 10:
        raise HTTPException(status_code=400, detail="score must be between 0 and 10")
    normalized_feedback_type = str(req.feedback_type or "thumb").strip().lower()
    if normalized_feedback_type not in ALLOWED_FEEDBACK_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"feedback_type must be one of: {', '.join(sorted(ALLOWED_FEEDBACK_TYPES))}",
        )
    normalized_scope = str(req.rewrite_scope or "ask_each_time").strip().lower()
    if normalized_scope not in ALLOWED_REWRITE_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"rewrite_scope must be one of: {', '.join(sorted(ALLOWED_REWRITE_SCOPES))}",
        )

    issue_tags = [str(tag).strip().lower() for tag in (req.issue_tags or []) if str(tag).strip()]
    invalid_tags = sorted({tag for tag in issue_tags if tag not in ALLOWED_FEEDBACK_ISSUE_TAGS})
    if invalid_tags:
        raise HTTPException(
            status_code=400,
            detail=f"issue_tags contains unsupported values: {', '.join(invalid_tags)}",
        )

    has_comment = bool(str(req.comment or "").strip())
    if req.needs_rewrite and (not has_comment and not issue_tags):
        raise HTTPException(status_code=400, detail="needs_rewrite feedback requires a comment or at least one issue tag")
    if req.selected_text_range is not None:
        start = int(req.selected_text_range.start)
        end = int(req.selected_text_range.end)
        if start < 0 or end < 0 or end <= start:
            raise HTTPException(status_code=400, detail="selected_text_range must have start >= 0 and end > start")


def _validate_review_action(action: str) -> str:
    normalized = str(action or "").strip().lower()
    if normalized not in ALLOWED_REVIEW_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of: {', '.join(sorted(ALLOWED_REVIEW_ACTIONS))}",
        )
    return normalized


def _read_jsonl(path: Path) -> List[dict]:
    rows: List[dict] = []
    if not path.exists():
        return rows
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows


def _read_json_file(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _stable_payload_sha256(payload: Any) -> str:
    # Must match book_flow.payload_sha256 exactly: ensure_ascii=True, default=str
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# Stage fallback integrity registry
# Add stages here as deterministic fallback paths are introduced in book_flow.py.
# Each entry maps a stage name to the artifact paths relative to run_dir.
# ---------------------------------------------------------------------------
_FALLBACK_STAGE_CONFIGS: dict = {
    "canon": {
        "artifact_dir": "03_canon",
        "payload_file": "canon.json",          # JSON payload for checksum verification
        "metadata_file": "canon_fallback_metadata.json",
        "contract_file": "fallback_contract_report.json",
    },
    # Future stages — uncomment and fill in when deterministic fallback is added:
    # "sections_written": {
    #     "artifact_dir": "04_drafts/chapter_01",
    #     "payload_file": None,                  # No single-file JSON checksum yet
    #     "metadata_file": "sections_fallback_metadata.json",
    #     "contract_file": "sections_fallback_contract_report.json",
    # },
}

# Fallback artifacts older than this threshold are treated as untrusted (stale).
# Override via FALLBACK_STALE_HOURS env var.
_FALLBACK_STALE_HOURS: float = float(os.environ.get("FALLBACK_STALE_HOURS", "72"))


def _normalize_fallback_stage(input_value: Optional[str]) -> Optional[str]:
    """Normalize fallback_stage filter input according to policy.
    
    Policy: whitespace trimmed, case-insensitive (converted to lowercase) to match
    registered stage names. This reduces operator confusion from typos/case variations.
    
    Args:
        input_value: Raw input from query parameter or function call (e.g., "CANON")
    
    Returns:
        Normalized stage name (lowercase), or None/empty-string if input was empty.
        Does NOT validate against registered stages — that is caller's responsibility.
    
    Examples:
        _normalize_fallback_stage("CANON") -> "canon"
        _normalize_fallback_stage("  Canon  ") -> "canon"
        _normalize_fallback_stage("") -> ""
        _normalize_fallback_stage(None) -> None
    """
    if input_value is None:
        return None
    normalized = str(input_value).strip().lower()
    return normalized


def _verify_stage_fallback_integrity(run_dir: Optional[Path], stage: str, config: dict) -> dict:
    """Generic fallback integrity check for any registered stage."""
    if not run_dir:
        return {
            "checked": False,
            "valid": True,
            "reason": "run_dir_missing",
            "issues": [],
            "stage": stage,
        }

    artifact_dir = run_dir / config["artifact_dir"]
    payload_path = artifact_dir / config["payload_file"] if config.get("payload_file") else None
    metadata_path = artifact_dir / config["metadata_file"]
    contract_path = artifact_dir / config["contract_file"]
    journal_path = run_dir / "run_journal.jsonl"

    journal_rows = _read_jsonl(journal_path)
    fallback_event_seen = any(
        str(row.get("event") or "") == "stage_fallback_applied"
        and str(((row.get("details") or {}).get("stage") or "")) == stage
        for row in journal_rows
        if isinstance(row, dict)
    )

    if not fallback_event_seen:
        return {
            "checked": False,
            "valid": True,
            "reason": f"no_{stage}_fallback_detected",
            "issues": [],
            "stage": stage,
            "paths": {
                "metadata": str(metadata_path),
                "contract_report": str(contract_path),
            },
        }

    issues: List[str] = []
    metadata = _read_json_file(metadata_path)
    contract_report = _read_json_file(contract_path)
    payload = _read_json_file(payload_path) if payload_path else None

    if not metadata_path.exists():
        issues.append("fallback_metadata_missing")
    elif not isinstance(metadata, dict):
        issues.append("fallback_metadata_invalid_json")
    else:
        if metadata.get("fallback") is not True:
            issues.append("fallback_metadata_flag_invalid")
        if str(metadata.get("stage") or stage) != stage:
            issues.append("fallback_metadata_stage_mismatch")

        if payload_path is not None:
            expected_checksum = str(metadata.get("fallback_payload_checksum") or "").strip()
            if not expected_checksum:
                issues.append("fallback_checksum_missing")
            elif isinstance(payload, (dict, list)):
                actual_checksum = _stable_payload_sha256(payload)
                if actual_checksum != expected_checksum:
                    issues.append("fallback_checksum_mismatch")
            else:
                issues.append("fallback_payload_missing_or_invalid")

    if not contract_path.exists():
        issues.append("fallback_contract_missing")
    elif not isinstance(contract_report, dict):
        issues.append("fallback_contract_invalid_json")
    elif not bool(contract_report.get("all_passed")):
        issues.append("fallback_contract_failed")

    # Staleness check — run after metadata validity checks so we only parse
    # generated_at when we have a valid metadata dict.
    age_hours: Optional[float] = None
    generated_at_raw: Optional[str] = None
    if isinstance(metadata, dict):
        generated_at_raw = str(metadata.get("generated_at") or "").strip() or None
        if generated_at_raw:
            try:
                # Accept ISO-8601 strings or Unix timestamps (float/int as string)
                try:
                    ts = float(generated_at_raw)
                except ValueError:
                    from datetime import timezone
                    import datetime as _dt
                    ts = _dt.datetime.fromisoformat(generated_at_raw.replace("Z", "+00:00")).timestamp()
                age_hours = (time.time() - ts) / 3600.0
                if age_hours > _FALLBACK_STALE_HOURS:
                    issues.append("fallback_artifact_stale")
            except Exception:  # noqa: BLE001
                issues.append("fallback_generated_at_unparseable")
        else:
            issues.append("fallback_generated_at_missing")

    valid = len(issues) == 0
    result: dict = {
        "checked": True,
        "valid": valid,
        "reason": "fallback_integrity_passed" if valid else "fallback_integrity_failed",
        "issues": issues,
        "stage": stage,
        "paths": {
            "metadata": str(metadata_path),
            "contract_report": str(contract_path),
            "run_journal": str(journal_path),
        },
        "fallback_event_seen": fallback_event_seen,
    }
    if generated_at_raw is not None:
        result["generated_at"] = generated_at_raw
    if age_hours is not None:
        result["age_hours"] = round(age_hours, 2)
    if payload_path:
        result["paths"]["payload"] = str(payload_path)
    return result


def _any_fallback_stage_failed(fallback_integrity: dict) -> tuple:
    """Return (any_failed, combined_issues, failed_stages) from a stage-keyed integrity dict."""
    failed_stages: List[str] = []
    all_issues: List[str] = []
    for stage, result in (fallback_integrity or {}).items():
        if not isinstance(result, dict):
            continue
        if bool(result.get("checked")) and not bool(result.get("valid", True)):
            failed_stages.append(stage)
            all_issues.extend(str(i) for i in (result.get("issues") or []) if str(i))
    return bool(failed_stages), all_issues, failed_stages


def _verify_canon_fallback_integrity(run_dir: Optional[Path]) -> dict:
    """Backward-compatible wrapper — verifies only the canon stage."""
    config = _FALLBACK_STAGE_CONFIGS.get("canon", {})
    if not config:
        return {"checked": False, "valid": True, "reason": "stage_not_registered", "issues": []}
    return _verify_stage_fallback_integrity(run_dir, "canon", config)


def _verify_all_stage_fallback_integrity(run_dir: Optional[Path]) -> dict:
    """Verify all registered fallback stages; returns {stage: result}."""
    return {
        stage: _verify_stage_fallback_integrity(run_dir, stage, cfg)
        for stage, cfg in _FALLBACK_STAGE_CONFIGS.items()
    }


def _parse_event_ts(raw_value: Any) -> Optional[float]:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _assess_run_interruption(run_dir: Optional[Path], stall_seconds: int = BOOK_RUN_STALL_SECONDS) -> dict:
    if not run_dir:
        return {
            "interrupted": False,
            "stalled": False,
            "stall_seconds": stall_seconds,
            "reason": "run_dir_missing",
            "last_event": None,
            "last_event_ts": None,
            "age_seconds": None,
            "terminal": False,
        }

    run_journal = run_dir / "run_journal.jsonl"
    rows = _read_jsonl(run_journal)
    if not rows:
        return {
            "interrupted": False,
            "stalled": False,
            "stall_seconds": stall_seconds,
            "reason": "journal_missing_or_empty",
            "last_event": None,
            "last_event_ts": None,
            "age_seconds": None,
            "terminal": False,
        }

    last = rows[-1]
    last_event = str(last.get("event") or "")
    last_event_ts = _parse_event_ts(last.get("timestamp"))
    age_seconds = None
    if last_event_ts is not None:
        age_seconds = max(0, time.time() - last_event_ts)

    terminal_events = {"run_success", "run_failure", "forced_completion"}
    terminal = last_event in terminal_events

    blocked_events = {"stage_attempt_start", "stage_recovery_start", "stage_instantiated", "run_start"}
    stalled = bool(
        (not terminal)
        and (last_event in blocked_events)
        and (age_seconds is not None)
        and (age_seconds >= stall_seconds)
    )

    return {
        "interrupted": stalled,
        "stalled": stalled,
        "stall_seconds": stall_seconds,
        "reason": "stalled_last_event" if stalled else "ok",
        "last_event": last_event,
        "last_event_ts": last_event_ts,
        "age_seconds": age_seconds,
        "terminal": terminal,
        "run_journal": str(run_journal),
    }


def _analyze_book_production(req: BookFlowRequest) -> dict:
    run_dir = _latest_book_run_dir(req.output_dir, req.title)
    if not run_dir:
        return {
            "run_dir": None,
            "status": "not-started",
            "checkpoint_score": {"completed": 0, "total": 10},
            "next_checkpoint": "publisher_brief",
            "checkpoints": [],
            "stage_attempts": {},
            "latest_stage_event": None,
            "last_error": None,
            "interruption": _assess_run_interruption(None),
            "review_gate": {},
        }

    changes_log = run_dir / "changes.log"
    rows = _read_jsonl(changes_log)
    stage_start = {}
    stage_complete = {}
    stage_result = {}
    latest_stage_event = None
    last_error = None

    for item in rows:
        action = str(item.get("action") or "")
        details = item.get("details") or {}
        stage = str(details.get("stage") or "")
        if action == "stage_start" and stage:
            stage_start[stage] = int(stage_start.get(stage, 0)) + 1
        elif action == "stage_complete" and stage:
            stage_complete[stage] = int(stage_complete.get(stage, 0)) + 1
        elif action == "stage_result" and stage:
            stage_result[stage] = {
                "gate_ok": details.get("gate_ok"),
                "gate_message": details.get("gate_message"),
                "attempt": details.get("attempt"),
            }

    for item in reversed(rows):
        action = str(item.get("action") or "")
        if action not in {"stage_start", "stage_result", "stage_complete", "stage_failure"}:
            continue
        details = item.get("details") or {}
        latest_stage_event = {
            "action": action,
            "stage": details.get("stage"),
            "agent": item.get("agent"),
            "profile": item.get("profile"),
            "attempt": details.get("attempt"),
            "gate_ok": details.get("gate_ok"),
            "gate_message": details.get("gate_message"),
            "ts": item.get("timestamp"),
        }
        break

    diag = run_dir / "diagnostics" / "agent_diagnostics.jsonl"
    for item in _read_jsonl(diag):
        if str(item.get("event") or "") == "agent_call_error":
            last_error = str(item.get("error") or "")

    checkpoint_specs = [
        ("publisher_brief", [run_dir / "00_brief" / "book_brief.json"]),
        ("research", [run_dir / "01_research" / "research_dossier.md"]),
        ("architect_outline", [run_dir / "02_outline" / "master_outline.md", run_dir / "02_outline" / "book_structure.json"]),
        ("chapter_planner", [run_dir / "02_outline" / "chapter_specs" / "chapter_01.json"]),
        ("canon", [run_dir / "03_canon" / "canon.json"]),
        ("sections_written", [run_dir / "04_drafts" / "chapter_01"]),
        ("section_reviews", [run_dir / "05_reviews" / "section_reviews"]),
        ("assembly", [run_dir / "04_drafts" / "chapter_01" / "assembled.md"]),
        ("quality_gates", [run_dir / "05_reviews" / "developmental_report.json", run_dir / "05_reviews" / "rubric_report.json", run_dir / "05_reviews" / "continuity_report.json", run_dir / "05_reviews" / "publisher_report.json"]),
        ("final_export", [run_dir / "06_final" / "manuscript_v1.md"]),
    ]

    checkpoints = []
    completed_count = 0
    next_checkpoint = None

    for checkpoint_name, artifacts in checkpoint_specs:
        artifact_ok = True
        for ap in artifacts:
            if ap.is_dir():
                has_any = any(ap.iterdir()) if ap.exists() else False
                artifact_ok = artifact_ok and has_any
            else:
                artifact_ok = artifact_ok and ap.exists()

        stage_ok = bool(stage_complete.get(checkpoint_name, 0) > 0)
        if checkpoint_name in {"sections_written", "section_reviews", "quality_gates", "final_export"}:
            # Derived checkpoints rely mainly on artifacts.
            stage_ok = artifact_ok

        done = bool(stage_ok and artifact_ok)
        if done:
            completed_count += 1
        elif next_checkpoint is None:
            next_checkpoint = checkpoint_name

        checkpoints.append(
            {
                "name": checkpoint_name,
                "completed": done,
                "artifact_ok": artifact_ok,
                "stage_attempts": int(stage_start.get(checkpoint_name, 0)),
                "last_stage_result": stage_result.get(checkpoint_name),
            }
        )

    if completed_count == len(checkpoint_specs):
        status = "complete"
        next_checkpoint = None
    elif completed_count == 0:
        status = "started"
    else:
        status = "in-progress"

    return {
        "run_dir": str(run_dir),
        "status": status,
        "checkpoint_score": {"completed": completed_count, "total": len(checkpoint_specs)},
        "next_checkpoint": next_checkpoint,
        "checkpoints": checkpoints,
        "stage_attempts": stage_start,
        "latest_stage_event": latest_stage_event,
        "last_error": last_error,
        "interruption": _assess_run_interruption(run_dir),
        "review_gate": _read_review_gate_state(str(run_dir)),
        "fallback_integrity": _verify_all_stage_fallback_integrity(run_dir),
        "evaluated_at": time.time(),
    }


def _resolve_profile_route_model(profile_name: Optional[str], prompt_hint: str = "") -> dict:
    if not profile_name:
        return {"route": None, "model": None}
    try:
        plan = orchestrator.plan_request(prompt_hint or "book-flow metadata", profile_name=profile_name)
        return {
            "route": plan.get("route"),
            "model": plan.get("model"),
        }
    except AgentStackError:
        return {"route": None, "model": None}


def _validate_book_flow_strategy_preflight() -> dict:
    """Fail-closed preflight validation for critical strategy profiles."""

    def _hints_for_checks(profile_name: str, checks: List[str]) -> List[str]:
        hints: List[str] = []
        for check in checks:
            text = str(check)
            if text.startswith("expected_route_mismatch"):
                hints.append(
                    f"Update {profile_name} route/allowed_routes frontmatter to match strategy matrix (expected ollama_nvidia)."
                )
            elif text.startswith("expected_model_mismatch"):
                hints.append(
                    f"Update {profile_name} model/model_allowlist to qwen3.5:4b or revise strategy matrix intentionally."
                )
            elif text.startswith("allowed_routes_violation"):
                hints.append(
                    f"Fix {profile_name} allowed_routes frontmatter so planned route is explicitly permitted."
                )
            elif text.startswith("model_allowlist_violation"):
                hints.append(
                    f"Fix {profile_name} model_allowlist to include the selected model or adjust selected model to allowlist."
                )
            elif text == "cpu_backend_block_disabled":
                hints.append("Set AGENT_BLOCK_CPU_BACKEND=true in runtime environment.")
            elif text == "force_full_gpu_layers_disabled":
                hints.append("Set AGENT_FORCE_FULL_GPU_LAYERS=true to enforce full GPU policy.")
            elif text.startswith("invalid_gpu_layers"):
                hints.append("Verify AGENT_NVIDIA_NUM_GPU_BY_MODEL / AGENT_AMD_NUM_GPU_BY_MODEL and enforce num_gpu=-1 policy.")
            elif text.startswith("plan_request_failed"):
                hints.append("Run profile lint and fix invalid profile frontmatter before relaunch.")
        unique: List[str] = []
        for hint in hints:
            if hint not in unique:
                unique.append(hint)
        return unique

    if not BOOK_FLOW_PREFLIGHT_ENABLED:
        return {
            "enabled": False,
            "strategy_version": BOOK_FLOW_STRATEGY_VERSION,
            "overall_ok": True,
            "results": [],
            "remediation_hints": [],
        }

    expected_matrix = {
        "book-publisher-brief": {"route": "ollama_nvidia", "model": "qwen3.5:4b"},
        "writing-assistant": {"route": "ollama_nvidia", "model": "qwen3.5:4b"},
        "book-canon": {"route": "ollama_nvidia", "model": "qwen3.5:4b"},
        "book-writer": {"route": "ollama_nvidia", "model": "qwen3.5:4b"},
    }

    results = []
    overall_ok = True
    for profile_name, expected in expected_matrix.items():
        checks = []
        ok = True
        route = None
        model = None
        try:
            plan = orchestrator.plan_request("book-flow-preflight", profile_name=profile_name)
            profile = plan.get("profile") or {}
            route = str(plan.get("route") or "").strip() or None
            model = str(plan.get("model") or "").strip() or None

            if route != expected["route"]:
                ok = False
                checks.append(f"expected_route_mismatch:{route}!={expected['route']}")
            if model != expected["model"]:
                ok = False
                checks.append(f"expected_model_mismatch:{model}!={expected['model']}")

            allowed_routes = {
                str(item).strip() for item in (profile.get("allowed_routes") or []) if str(item).strip()
            }
            if allowed_routes and route not in allowed_routes:
                ok = False
                checks.append(f"allowed_routes_violation:{route} not in {sorted(allowed_routes)}")

            model_allowlist = {
                str(item).strip() for item in (profile.get("model_allowlist") or []) if str(item).strip()
            }
            if model_allowlist and model not in model_allowlist:
                ok = False
                checks.append(f"model_allowlist_violation:{model} not in {sorted(model_allowlist)}")

            if route in {"ollama_nvidia", "ollama_amd"}:
                if not bool(getattr(orchestrator, "block_cpu_backend", False)):
                    ok = False
                    checks.append("cpu_backend_block_disabled")
                if not bool(getattr(orchestrator, "force_full_gpu_layers", False)):
                    ok = False
                    checks.append("force_full_gpu_layers_disabled")
                resolved_layers = orchestrator._resolve_model_num_gpu_layers(model, route)  # pylint: disable=protected-access
                if resolved_layers in (None, 0):
                    ok = False
                    checks.append(f"invalid_gpu_layers:{resolved_layers}")
                else:
                    checks.append(f"num_gpu={resolved_layers}")
        except AgentStackError as err:
            ok = False
            checks.append(f"plan_request_failed:{err}")

        if not ok:
            overall_ok = False

        results.append(
            {
                "profile": profile_name,
                "ok": ok,
                "route": route,
                "model": model,
                "checks": checks,
                "hints": _hints_for_checks(profile_name, checks),
            }
        )

    remediation_hints: List[str] = []
    for row in results:
        for hint in row.get("hints", []):
            if hint not in remediation_hints:
                remediation_hints.append(hint)

    return {
        "enabled": True,
        "strategy_version": BOOK_FLOW_STRATEGY_VERSION,
        "overall_ok": overall_ok,
        "results": results,
        "remediation_hints": remediation_hints,
    }


def _resolve_book_task_runtime_target(req: BookFlowRequest, production_status: Optional[dict]) -> dict:
    production_status = production_status if isinstance(production_status, dict) else {}
    latest_stage = production_status.get("latest_stage_event") or {}
    latest_profile = latest_stage.get("profile") if isinstance(latest_stage, dict) else None

    candidates = [
        latest_profile,
        req.writer_profile,
        req.editor_profile,
        req.publisher_brief_profile,
        req.publisher_profile,
    ]

    seen = set()
    for profile_name in candidates:
        if not profile_name:
            continue
        if profile_name in seen:
            continue
        seen.add(profile_name)
        resolved = _resolve_profile_route_model(profile_name, prompt_hint=req.section_goal)
        route = resolved.get("route")
        model = resolved.get("model")
        if route or model:
            return {"profile": profile_name, "route": route, "model": model}

    return {"profile": None, "route": None, "model": None}


def _nvidia_pressure_depth() -> int:
    depth = 0
    for rec in _tasks.values():
        if rec.route != NVIDIA_ROUTE_NAME:
            continue
        if rec.status not in {"queued", "running"}:
            continue
        if not rec.model or rec.model not in PRESSURE_MODELS:
            continue
        depth += 1
    return depth


def _update_pressure_state_locked() -> dict:
    now = time.time()
    if not PRESSURE_ENABLED:
        _pressure_mode["active"] = False
        _pressure_mode["last_depth"] = 0
        _pressure_mode["last_update"] = now
        _resource_switch_event_locked(mode="normal", reason="pressure_disabled", depth=0)
        _write_resource_tracker_locked(reason="pressure_disabled")
        return dict(_pressure_mode)

    depth = _nvidia_pressure_depth()
    previous_active = bool(_pressure_mode.get("active"))
    if _pressure_mode["active"]:
        if depth <= PRESSURE_HYSTERESIS_CLEAR:
            _pressure_mode["active"] = False
    else:
        if depth > PRESSURE_THRESHOLD:
            _pressure_mode["active"] = True

    _pressure_mode["last_depth"] = depth
    _pressure_mode["last_update"] = now
    if previous_active != bool(_pressure_mode["active"]):
        mode = "pressure" if _pressure_mode["active"] else "normal"
        _resource_switch_event_locked(mode=mode, reason="pressure_transition", depth=depth)
    _write_resource_tracker_locked(reason="pressure_update")
    return dict(_pressure_mode)


def _pressure_snapshot() -> dict:
    with _task_lock:
        return _update_pressure_state_locked()


def _pressure_guard(profile_name: Optional[str], context: str) -> None:
    snap = _pressure_snapshot()
    if not snap.get("active"):
        return

    # In pressure mode, always perform watchdog scan before deciding admission.
    watchdog = orchestrator.scan_unresponsive_agents()
    if watchdog.get("count"):
        _append_ui_event("pressure_watchdog_recovery", {"context": context, "watchdog": watchdog})

    pname = (profile_name or "").strip()
    if pname in PRESSURE_WRITER_PROFILES:
        return
    if pname in PRESSURE_PAUSE_PROFILES or pname == "":
        raise HTTPException(
            status_code=429,
            detail=(
                f"pressure-mode active (nvidia_depth={snap.get('last_depth')}); "
                f"spawning paused for profile '{pname or 'auto'}' in {context}. "
                "Writer tasks remain prioritized."
            ),
        )


def _agent_error_to_http_exception(err: Exception, default_status: int = 502) -> HTTPException:
    code = getattr(err, "code", None)
    status_map = {
        "AGENT_PROFILE_ERROR": 400,
        "AGENT_ROUTE_CONFIG_ERROR": 400,
        "OPENCLAW_PROFILE_CONFIG_ERROR": 400,
        "AGENT_QUARANTINED": 503,
        "AGENT_HUNG": 504,
        "OLLAMA_REQUEST_ERROR": 502,
        "OLLAMA_RESPONSE_DECODE_ERROR": 502,
        "OLLAMA_ENDPOINT_ERROR": 502,
        "OLLAMA_EMPTY_RESPONSE": 502,
        "STAGE_QUALITY_GATE_ERROR": 422,
        "CHAPTER_SPEC_VALIDATION_ERROR": 422,
    }
    status_code = status_map.get(code, default_status)
    if not code:
        return HTTPException(status_code=status_code, detail=str(err))
    detail = {"code": code, "message": str(err)}
    payload = getattr(err, "details", None)
    if isinstance(payload, dict) and payload:
        detail["details"] = payload
    return HTTPException(status_code=status_code, detail=detail)


def _run_task(task_id: str):
    with _task_lock:
        record = _tasks.get(task_id)
        if not record:
            return
        record.status = "running"
        record.started_at = time.time()
        _write_resource_tracker_locked(reason="task_running")
    _refresh_ui_state_snapshot(event_type="task_running")

    try:
        result = orchestrator.handle_request_with_overrides(
            record.prompt,
            profile_name=record.profile,
            model_override=record.model,
            stream_override=False,
        )

        with _task_lock:
            record.status = "completed"
            record.response = result
            record.finished_at = time.time()
            _update_pressure_state_locked()
            _write_resource_tracker_locked(reason="task_completed")
        _refresh_ui_state_snapshot(event_type="task_completed")
    except AgentStackError as err:
        error_code = err.code
        with _task_lock:
            record.status = "failed"
            record.error = f"[{error_code}] {err}"
            record.finished_at = time.time()
            _update_pressure_state_locked()
            _write_resource_tracker_locked(reason="task_failed")
        _refresh_ui_state_snapshot(event_type="task_failed")
    except Exception as err:
        wrapped = AgentUnexpectedError(
            f"Unexpected task execution failure: {err}",
            details={"task_id": task_id, "profile": record.profile, "model": record.model},
        )
        error_code = wrapped.code
        with _task_lock:
            record.status = "failed"
            record.error = f"[{error_code}] {wrapped}"
            record.finished_at = time.time()
            _update_pressure_state_locked()
            _write_resource_tracker_locked(reason="task_failed")
        _refresh_ui_state_snapshot(event_type="task_failed")


def _run_book_task(task_id: str, req: BookFlowRequest):
    with _task_lock:
        record = _tasks.get(task_id)
        if not record:
            return
        record.status = "running"
        record.started_at = time.time()
        record.profile = "book-flow"
        record.production_status = _analyze_book_production(req)
        runtime_target = _resolve_book_task_runtime_target(req, record.production_status)
        if runtime_target.get("route"):
            record.route = runtime_target.get("route")
        if runtime_target.get("model"):
            record.model = runtime_target.get("model")
        if (record.production_status or {}).get("last_error"):
            record.error = (record.production_status or {}).get("last_error")
        _write_resource_tracker_locked(reason="book_task_running")
    _refresh_ui_state_snapshot(event_type="book_task_running")

    try:
        args = SimpleNamespace(
            task_id=task_id,
            title=req.title,
            premise=req.premise,
            chapter_number=req.chapter_number,
            chapter_title=req.chapter_title,
            section_title=req.section_title,
            section_goal=req.section_goal,
            genre=req.genre,
            audience=req.audience,
            tone=req.tone,
            writer_words=req.writer_words,
            target_word_count=req.target_word_count,
            page_target=req.page_target,
            max_retries=req.max_retries,
            merge_context_words=req.merge_context_words,
            verbose=req.verbose,
            debug=False,
            writer_profile=req.writer_profile,
            editor_profile=req.editor_profile,
            publisher_brief_profile=req.publisher_brief_profile,
            publisher_profile=req.publisher_profile,
            output_dir=req.output_dir,
            strategy_version=BOOK_FLOW_STRATEGY_VERSION,
            resource_tracker_path=req.resource_tracker_path or str(RESOURCE_TRACKER_PATH),
            resource_events_path=req.resource_events_path or str(RESOURCE_EVENTS_PATH),
        )
        summary = run_flow(args)
        with _task_lock:
            record.status = "completed"
            record.response = summary
            record.finished_at = time.time()
            record.production_status = _analyze_book_production(req)
            runtime_target = _resolve_book_task_runtime_target(req, record.production_status)
            if runtime_target.get("route"):
                record.route = runtime_target.get("route")
            if runtime_target.get("model"):
                record.model = runtime_target.get("model")
            _update_pressure_state_locked()
            _write_resource_tracker_locked(reason="book_task_completed")
        _refresh_ui_state_snapshot(event_type="book_task_completed")
    except AgentStackError as err:
        error_code = err.code
        should_retry = False
        retry_delay = BOOK_AUTO_RESUME_BACKOFF_SECONDS
        with _task_lock:
            record.error = f"[{error_code}] {err}"
            record.finished_at = time.time()
            record.production_status = _analyze_book_production(req)
            runtime_target = _resolve_book_task_runtime_target(req, record.production_status)
            if runtime_target.get("route"):
                record.route = runtime_target.get("route")
            if runtime_target.get("model"):
                record.model = runtime_target.get("model")
            run_dir = ((record.production_status or {}).get("run_dir") if isinstance(record.production_status, dict) else None)

            can_retry = (
                BOOK_AUTO_RESUME_ENABLED
                and not record.hold
                and record.retry_count < record.max_auto_retries
            )

            if can_retry:
                record.retry_count += 1
                retry_delay = min(
                    BOOK_AUTO_RESUME_BACKOFF_MAX_SECONDS,
                    BOOK_AUTO_RESUME_BACKOFF_SECONDS * (2 ** max(0, record.retry_count - 1)),
                )
                record.next_retry_at = time.time() + retry_delay
                record.status = "queued"
                should_retry = True
                _write_resource_tracker_locked(reason="book_task_retry_scheduled")
            else:
                record.status = "failed"
                record.next_retry_at = None
                _write_resource_tracker_locked(reason="book_task_failed")

            _update_pressure_state_locked()

        _ensure_run_journal_terminal(
            run_dir,
            task_id,
            f"[{error_code}] {err}",
        )

        _refresh_ui_state_snapshot(
            event_type="book_task_retry_scheduled" if should_retry else "book_task_failed"
        )

        if should_retry:
            def delayed_retry():
                time.sleep(retry_delay)
                with _task_lock:
                    retry_record = _tasks.get(task_id)
                    if not retry_record:
                        return
                    if retry_record.hold:
                        return
                    if retry_record.status != "queued":
                        return
                    retry_record.next_retry_at = None
                    _set_spawn_release_locked(retry_record)
                _schedule_spawn(task_id, _run_book_task, req)

            threading.Thread(target=delayed_retry, daemon=True).start()
    except Exception as err:
        wrapped = AgentUnexpectedError(
            f"Unexpected book task failure: {err}",
            details={"task_id": task_id, "profile": "book-flow", "title": req.title},
        )
        error_code = wrapped.code
        should_retry = False
        retry_delay = BOOK_AUTO_RESUME_BACKOFF_SECONDS
        with _task_lock:
            record.error = f"[{error_code}] {wrapped}"
            record.finished_at = time.time()
            record.production_status = _analyze_book_production(req)
            runtime_target = _resolve_book_task_runtime_target(req, record.production_status)
            if runtime_target.get("route"):
                record.route = runtime_target.get("route")
            if runtime_target.get("model"):
                record.model = runtime_target.get("model")
            run_dir = ((record.production_status or {}).get("run_dir") if isinstance(record.production_status, dict) else None)

            can_retry = (
                BOOK_AUTO_RESUME_ENABLED
                and not record.hold
                and record.retry_count < record.max_auto_retries
            )

            if can_retry:
                record.retry_count += 1
                retry_delay = min(
                    BOOK_AUTO_RESUME_BACKOFF_MAX_SECONDS,
                    BOOK_AUTO_RESUME_BACKOFF_SECONDS * (2 ** max(0, record.retry_count - 1)),
                )
                record.next_retry_at = time.time() + retry_delay
                record.status = "queued"
                should_retry = True
                _write_resource_tracker_locked(reason="book_task_retry_scheduled")
            else:
                record.status = "failed"
                record.next_retry_at = None
                _write_resource_tracker_locked(reason="book_task_failed")

            _update_pressure_state_locked()

        _ensure_run_journal_terminal(
            run_dir,
            task_id,
            f"[{error_code}] {wrapped}",
        )

        _refresh_ui_state_snapshot(
            event_type="book_task_retry_scheduled" if should_retry else "book_task_failed"
        )

        if should_retry:
            def delayed_retry():
                time.sleep(retry_delay)
                with _task_lock:
                    retry_record = _tasks.get(task_id)
                    if not retry_record:
                        return
                    if retry_record.hold:
                        return
                    if retry_record.status != "queued":
                        return
                    retry_record.next_retry_at = None
                    _set_spawn_release_locked(retry_record)
                _schedule_spawn(task_id, _run_book_task, req)

            threading.Thread(target=delayed_retry, daemon=True).start()


# Bootstrap persisted tasks only after task runners are defined.
_bootstrap_task_ledger()


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/api/profiles")
def profiles():
    return {
        "profiles": [
            {
                "name": p.get("name"),
                "route": p.get("route"),
                "model": p.get("model"),
                "priority": p.get("priority"),
                "intent_keywords": p.get("intent_keywords"),
            }
            for p in orchestrator.profiles
        ]
    }


@app.get("/api/health")
def health():
    _pressure_snapshot()
    resource = _resource_snapshot(reason="health_check")
    return {
        "ok": True,
        "time": time.time(),
        "server_mode": getattr(orchestrator, "server_mode", "standard"),
        "pressure_mode": dict(_pressure_mode),
        "resource_mode": _resource_switch_state.get("mode", "normal"),
        "resource_tracker": resource,
    }


@app.get("/api/resource-tracker")
def resource_tracker():
    return _resource_snapshot(reason="resource_tracker_api")


@app.get("/v1/models")
def openclaw_compat_models():
    _require_openclaw_mode()
    models = []
    seen = set()
    for p in orchestrator.profiles:
        model = p.get("model")
        if not model or model in seen:
            continue
        seen.add(model)
        models.append({"id": model, "object": "model", "owned_by": "dragonlair"})
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
def openclaw_compat_chat_completions(req: OpenClawCompatChatRequest):
    _require_openclaw_mode()
    prompt = _messages_to_prompt(req.messages, tools=req.tools)
    if not prompt:
        raise HTTPException(status_code=400, detail="messages are required")

    profile_name = _select_openclaw_profile(req)
    _pressure_guard(profile_name, context="/v1/chat/completions")

    if not req.stream:
        try:
            result = orchestrator.handle_request_with_overrides(
                prompt,
                profile_name=profile_name,
                stream_override=False,
            )
        except AgentStackError as err:
            raise _agent_error_to_http_exception(err, default_status=502) from err
        return _openclaw_compat_response(result, req.model)

    def stream_generator():
        tokens: List[str] = []
        done = False
        err = None

        def callback(token, _chunk):
            if token:
                tokens.append(token)

        def runner():
            nonlocal done, err
            try:
                orchestrator.handle_request_with_overrides(
                    prompt,
                    profile_name=profile_name,
                    stream_override=True,
                    on_stream=callback,
                )
            except AgentStackError as exc:
                err = {"code": exc.code, "message": str(exc), "details": exc.details}
            except Exception as exc:
                wrapped = AgentUnexpectedError(
                    f"Unexpected streaming failure: {exc}",
                    details={"profile": profile_name, "model": req.model},
                )
                err = {"code": wrapped.code, "message": str(wrapped), "details": wrapped.details}
            finally:
                done = True

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        offset = 0
        while True:
            while offset < len(tokens):
                token = tokens[offset]
                offset += 1
                chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex}",
                    "object": "chat.completion.chunk",
                    "created": _openclaw_compat_now(),
                    "model": req.model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": token},
                            "finish_reason": None,
                        }
                    ],
                }
                yield "data: " + json.dumps(chunk) + "\n\n"
            if done:
                break
            time.sleep(0.05)

        if err:
            err_chunk = {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion.chunk",
                "created": _openclaw_compat_now(),
                "model": req.model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": ""},
                        "finish_reason": "error",
                    }
                ],
                "error": err,
            }
            yield "data: " + json.dumps(err_chunk) + "\n\n"

        final_chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion.chunk",
            "created": _openclaw_compat_now(),
            "model": req.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield "data: " + json.dumps(final_chunk) + "\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


@app.get("/api/status")
def status(fallback_used: Optional[bool] = None, fallback_stage: Optional[str] = None):
    _pressure_snapshot()
    normalized_stage = _normalize_fallback_stage(fallback_stage)
    if normalized_stage:
        valid_stages = sorted(str(stage) for stage in _FALLBACK_STAGE_CONFIGS.keys())
        if normalized_stage not in valid_stages:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"invalid fallback_stage '{normalized_stage}'; "
                    f"valid values: {', '.join(valid_stages)}"
                ),
            )
    with _task_lock:
        records = list(_tasks.values())
    payload = _build_status_payload(records, fallback_used=fallback_used, fallback_stage=normalized_stage)
    _refresh_ui_state_snapshot(event_type="status")
    return payload


@app.get("/api/ui-state")
def ui_state():
    # Build a fresh payload on every poll so agent health reflects live task-queue
    # route activity (inflight synthesis). This avoids showing agents as idle while
    # Ollama is actively processing — the cached snapshot goes stale during long
    # LLM calls because no event fires to refresh it.
    with _task_lock:
        records = list(_tasks.values())
    return _build_status_payload(records)


@app.post("/api/recover-hung")
def recover_hung(req: RecoverHungRequest):
    with _task_lock:
        running = [t.id for t in _tasks.values() if t.status == "running"]
    if running and not req.force:
        raise HTTPException(
            status_code=409,
            detail=(
                "cannot recover while tasks are running; retry with force=true if you intend to reset anyway"
            ),
        )

    result = orchestrator.recover_hung_agents(force=req.force)
    payload = _refresh_ui_state_snapshot(event_type="agents_recovered")
    return {
        "status": "ok",
        "running_tasks": running,
        "recovery": result,
        "state": payload,
    }


@app.post("/api/clear-history")
def clear_history(_request: Request):
    with _task_lock:
        _tasks.clear()

    project_root = "/home/daravenrk/dragonlair/book_project"
    removed = {"changes_logs": 0, "diagnostics": 0}

    for log_path in glob.glob(f"{project_root}/**/changes.log", recursive=True):
        try:
            os.remove(log_path)
            removed["changes_logs"] += 1
        except OSError:
            continue

    for diag_path in glob.glob(f"{project_root}/**/diagnostics/agent_diagnostics.jsonl", recursive=True):
        try:
            os.remove(diag_path)
            removed["diagnostics"] += 1
        except OSError:
            continue
    payload = _refresh_ui_state_snapshot(event_type="history_cleared")
    return {"status": "cleared", "removed": removed, "state": payload}


@app.post("/api/tasks")
def create_task(req: SteerRequest):
    merged_prompt = _compose_prompt(req.direction, req.prompt)
    if not merged_prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    # Backend mutual exclusion: block coding if book mode active
    with _task_lock:
        for t in _tasks.values():
            if t.profile == "book-flow" and t.status in {"queued", "running"}:
                raise HTTPException(status_code=409, detail="Book mode is active. Coding tasks are blocked until book mode completes or is cancelled.")

    try:
        plan = orchestrator.plan_request(merged_prompt, profile_name=req.profile, model_override=req.model)
    except AgentStackError as err:
        raise _agent_error_to_http_exception(err, default_status=400) from err
    resolved_profile_name = (plan.get("profile") or {}).get("name")
    _pressure_guard(resolved_profile_name, context="/api/tasks")

    # Detect if this is a coding task (profile is coder or nvidia-fast/lowlatency)
    coding_profiles = {"amd-coder", "nvidia-fast", "nvidia-lowlatency"}
    if (plan.get("profile") and plan["profile"].get("name") in coding_profiles):
        # Block coding if book mode is active (already checked above)
        pass

    task_id = str(uuid.uuid4())
    record = TaskRecord(
        id=task_id,
        created_at=time.time(),
        status="queued",
        prompt=merged_prompt,
        direction=req.direction,
        profile=req.profile,
        route=plan.get("route"),
        model=plan.get("model"),
    )

    with _task_lock:
        if STRICT_ONE_MODEL_PER_ROUTE:
            conflict_model = _find_route_model_conflict(record.route, record.model)
            if conflict_model:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"route {record.route} is busy with model {conflict_model}; "
                        f"requested model {record.model} is blocked until queue drains"
                    ),
                )
        _tasks[task_id] = record
        _set_spawn_release_locked(record)
        _update_pressure_state_locked()
        _write_resource_tracker_locked(reason="task_queued")
        queue_positions = _compute_queue_positions(list(_tasks.values()))
        position = queue_positions.get(task_id)

    _refresh_ui_state_snapshot(event_type="task_queued")

    _schedule_spawn(task_id, _run_task)
    return {"task_id": task_id, "status": "queued", "queue_position": position, "route": record.route, "model": record.model}


@app.post("/api/book-flow")
def create_book_flow(req: BookFlowRequest):
    _pressure_guard("book-flow", context="/api/book-flow")
    # Backend mutual exclusion: block book mode if coding is active
    with _task_lock:
        for t in _tasks.values():
            if t.profile in {"amd-coder", "nvidia-fast", "nvidia-lowlatency"} and t.status in {"queued", "running"}:
                raise HTTPException(status_code=409, detail="Coding mode is active. Book mode is blocked until coding tasks complete or are cancelled.")

    preflight = _validate_book_flow_strategy_preflight()
    if not preflight.get("overall_ok", False):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "book_flow_strategy_preflight_failed",
                "strategy_version": preflight.get("strategy_version"),
                "results": preflight.get("results", []),
                "remediation_hints": preflight.get("remediation_hints", []),
                "hint": "Run PYTHONPATH=/home/daravenrk/dragonlair python3 -m agent_stack.scripts.validate_run_strategy --json for full diagnostics.",
            },
        )

    task_id = str(uuid.uuid4())
    label = f"book-flow: ch{req.chapter_number} {req.chapter_title} / {req.section_title}"
    initial_target = _resolve_book_task_runtime_target(req, None)
    record = TaskRecord(
        id=task_id,
        created_at=time.time(),
        status="queued",
        prompt=label,
        direction="book-mode",
        profile="book-flow",
        route=initial_target.get("route"),
        model=initial_target.get("model"),
        book_request=req.model_dump(),
        retry_count=0,
        max_auto_retries=BOOK_AUTO_RESUME_MAX_RETRIES,
        hold=False,
    )

    with _task_lock:
        _tasks[task_id] = record
        _set_spawn_release_locked(record)
        _update_pressure_state_locked()
        _write_resource_tracker_locked(reason="book_task_queued")
        queue_positions = _compute_queue_positions(list(_tasks.values()))
        position = queue_positions.get(task_id)

    _refresh_ui_state_snapshot(event_type="book_task_queued")

    _schedule_spawn(task_id, _run_book_task, req)
    return {
        "task_id": task_id,
        "status": "queued",
        "task_type": "book-flow",
        "queue_position": position,
        "route": record.route,
        "model": record.model,
        "strategy_version": BOOK_FLOW_STRATEGY_VERSION,
    }


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    with _task_lock:
        record = _tasks.get(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="task not found")
        queue_positions = _compute_queue_positions(list(_tasks.values()))
        return _task_to_dict(record, queue_position=queue_positions.get(record.id))


@app.post("/api/tasks/{task_id}/hold")
def hold_book_task(task_id: str, req: BookHoldRequest):
    trigger_resume = False
    book_req = None

    with _task_lock:
        record = _tasks.get(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="task not found")
        if record.profile != "book-flow":
            raise HTTPException(status_code=400, detail="hold control is only supported for book-flow tasks")

        record.hold = bool(req.hold)
        if record.hold:
            record.next_retry_at = None
        else:
            if record.status in {"failed", "queued"} and record.book_request:
                trigger_resume = True
                book_req = BookFlowRequest(**record.book_request)
                record.production_status = _analyze_book_production(book_req)

        _write_resource_tracker_locked(reason="book_task_hold_update")
        _update_pressure_state_locked()
        queue_positions = _compute_queue_positions(list(_tasks.values()))
        payload = _task_to_dict(record, queue_position=queue_positions.get(record.id))

    _refresh_ui_state_snapshot(event_type="book_task_hold_update")

    if trigger_resume and book_req is not None:
        with _task_lock:
            record = _tasks.get(task_id)
            if record and record.status == "queued":
                _set_spawn_release_locked(record)
        _schedule_spawn(task_id, _run_book_task, book_req)

    return {"status": "ok", "task": payload}


@app.post("/api/tasks/{task_id}/review-action")
def review_book_task(task_id: str, req: BookReviewActionRequest):
    action = _validate_review_action(req.action)
    trigger_resume = False
    book_req = None
    run_dir = None
    runtime_hint = None

    with _task_lock:
        record = _tasks.get(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="task not found")
        if record.profile != "book-flow":
            raise HTTPException(status_code=400, detail="review actions are only supported for book-flow tasks")
        if not record.book_request:
            raise HTTPException(status_code=404, detail="book request payload not found")

        book_req = BookFlowRequest(**record.book_request)
        if not isinstance(record.production_status, dict) or not record.production_status.get("run_dir"):
            record.production_status = _analyze_book_production(book_req)
        run_dir = (record.production_status or {}).get("run_dir")
        runtime_hint = _latest_book_stage_runtime_hint(record)
        orphaned_paused_task = record.status == "paused" and record.started_at is None

        if action in {"continue", "rewrite"}:
            record.hold = False
            record.next_retry_at = None
            if orphaned_paused_task:
                record.status = "queued"
                record.finished_at = None
                record.error = None
                trigger_resume = True
            elif record.status == "paused":
                record.status = "running"
        else:
            record.hold = True
            record.next_retry_at = None
            record.status = "paused"

        if action in {"continue", "rewrite"} and record.status in {"failed", "queued"}:
            trigger_resume = True

        _write_resource_tracker_locked(reason="book_task_review_action")
        _update_pressure_state_locked()
        queue_positions = _compute_queue_positions(list(_tasks.values()))
        payload = _task_to_dict(record, queue_position=queue_positions.get(record.id))

    review_gate_state = _write_review_gate_state(
        run_dir,
        {
            "status": {
                "continue": "continue_requested",
                "rewrite": "rewrite_requested",
                "defer": "deferred",
            }[action],
            "note": str(req.note or "").strip() or None,
            "reviewer": str(req.reviewer or "operator").strip() or "operator",
            "review_action": action,
            "review_action_requested_at_epoch": time.time(),
            "correlation_id": (runtime_hint or {}).get("correlation_id"),
        },
    )
    _append_run_journal_event(
        run_dir,
        "human_review_action_requested",
        {
            "task_id": task_id,
            "action": action,
            "reviewer": str(req.reviewer or "operator").strip() or "operator",
            "note": str(req.note or "").strip() or None,
            "correlation_id": (runtime_hint or {}).get("correlation_id"),
        },
    )

    _refresh_ui_state_snapshot(event_type="book_task_review_action")

    if trigger_resume and book_req is not None:
        with _task_lock:
            record = _tasks.get(task_id)
            if record and record.status == "queued":
                _set_spawn_release_locked(record, delay_seconds=0.0)
        _schedule_spawn(task_id, _run_book_task, book_req)

    return {"status": "ok", "action": action, "task": payload, "review_gate_state": review_gate_state}


@app.post("/api/book-jobs/reconcile")
def reconcile_book_jobs():
    resumed = []
    skipped = []
    interruption_events = []

    with _task_lock:
        for task_id, record in _tasks.items():
            if record.profile != "book-flow":
                continue

            req = None
            if record.book_request:
                try:
                    req = BookFlowRequest(**record.book_request)
                except ValidationError:
                    req = None

            gate_status = _book_task_review_gate_status(record, req) if req is not None else ""

            if record.status == "paused" and (not record.hold) and gate_status in REVIEW_GATE_RESUME_STATUSES:
                if req is None:
                    skipped.append({"task_id": task_id, "reason": "paused_invalid_request_payload"})
                    continue
                record.status = "queued"
                record.started_at = None
                record.finished_at = None
                record.next_retry_at = None
                record.error = None
                _set_spawn_release_locked(record, delay_seconds=0.0)
                resumed.append({
                    "task_id": task_id,
                    "retry_count": record.retry_count,
                    "reason": f"review_gate_{gate_status}",
                })
                _schedule_spawn(task_id, _run_book_task, req)
                continue

            if record.hold:
                skipped.append({"task_id": task_id, "reason": "hold"})
                continue

            if record.status == "running":
                if not record.book_request:
                    skipped.append({"task_id": task_id, "reason": "running_missing_request_payload"})
                    continue

                try:
                    req = BookFlowRequest(**record.book_request)
                except ValidationError:
                    skipped.append({"task_id": task_id, "reason": "running_invalid_request_payload"})
                    continue

                record.production_status = _analyze_book_production(req)
                fallback_integrity = (record.production_status or {}).get("fallback_integrity") or {}
                _fi_failed, _fi_issues, _fi_stages = _any_fallback_stage_failed(fallback_integrity)
                if _fi_failed:
                    run_dir = (record.production_status or {}).get("run_dir")
                    record.status = "failed"
                    record.hold = True
                    record.finished_at = time.time()
                    record.next_retry_at = None
                    record.error = (
                        "Reconcile blocked resume due to fallback integrity failure in stage(s): "
                        + ", ".join(_fi_stages) + "; operator review required"
                    )
                    skipped.append(
                        {
                            "task_id": task_id,
                            "reason": "fallback_integrity_failed",
                            "stages": _fi_stages,
                            "issues": _fi_issues,
                        }
                    )
                    _append_run_journal_event(
                        run_dir,
                        "fallback_integrity_failed",
                        {
                            "task_id": task_id,
                            "source": "reconcile_running_guard",
                            "stages": _fi_stages,
                            "issues": _fi_issues,
                        },
                    )
                    continue
                interruption = (record.production_status or {}).get("interruption") or {}
                if interruption.get("stalled"):
                    run_dir = (record.production_status or {}).get("run_dir")
                    record.status = "queued"
                    record.started_at = None
                    record.finished_at = None
                    record.next_retry_at = None
                    record.error = (
                        "Recovered stalled run during reconcile: "
                        f"last_event={interruption.get('last_event')} age={int(interruption.get('age_seconds') or 0)}s"
                    )
                    _set_spawn_release_locked(record, delay_seconds=0.0)
                    resumed.append({
                        "task_id": task_id,
                        "retry_count": record.retry_count,
                        "reason": "stalled_running_task",
                    })
                    interruption_payload = {
                        "task_id": task_id,
                        "run_dir": run_dir,
                        "interruption": interruption,
                        "action": "requeued",
                    }
                    interruption_events.append(interruption_payload)
                    _append_run_journal_event(
                        run_dir,
                        "run_interrupted",
                        {
                            "task_id": task_id,
                            "interruption": interruption,
                            "reconciled_at": time.time(),
                        },
                    )
                    _schedule_spawn(task_id, _run_book_task, req)
                else:
                    skipped.append({"task_id": task_id, "reason": "running"})
                continue

            if not record.book_request:
                skipped.append({"task_id": task_id, "reason": "missing_request_payload"})
                continue

            if record.retry_count >= record.max_auto_retries:
                skipped.append({"task_id": task_id, "reason": "retry_limit_reached"})
                continue

            if record.status not in {"failed", "queued"}:
                skipped.append({"task_id": task_id, "reason": f"status={record.status}"})
                continue

            if record.next_retry_at and record.next_retry_at > time.time():
                skipped.append({"task_id": task_id, "reason": "backoff_pending"})
                continue

            if req is None:
                skipped.append({"task_id": task_id, "reason": "invalid_request_payload"})
                continue

            record.production_status = _analyze_book_production(req)
            fallback_integrity = (record.production_status or {}).get("fallback_integrity") or {}
            _fi_failed, _fi_issues, _fi_stages = _any_fallback_stage_failed(fallback_integrity)
            if _fi_failed:
                run_dir = (record.production_status or {}).get("run_dir")
                record.status = "failed"
                record.hold = True
                record.finished_at = time.time()
                record.next_retry_at = None
                record.error = (
                    "Reconcile blocked auto-resume due to fallback integrity failure in stage(s): "
                    + ", ".join(_fi_stages) + "; operator review required"
                )
                skipped.append(
                    {
                        "task_id": task_id,
                        "reason": "fallback_integrity_failed",
                        "stages": _fi_stages,
                        "issues": _fi_issues,
                    }
                )
                _append_run_journal_event(
                    run_dir,
                    "fallback_integrity_failed",
                    {
                        "task_id": task_id,
                        "source": "reconcile_retry_guard",
                        "stages": _fi_stages,
                        "issues": _fi_issues,
                    },
                )
                continue

            record.status = "queued"
            record.next_retry_at = None
            _set_spawn_release_locked(record)
            resumed.append({"task_id": task_id, "retry_count": record.retry_count})
            _schedule_spawn(task_id, _run_book_task, req)

        # Integrity pass: for every task that is permanently failed (not going
        # to be retried), ensure the run journal has a terminal event so that
        # external readers can always identify a closed run without ambiguity.
        for task_id, record in _tasks.items():
            if record.status != "failed" or not record.book_request:
                continue
            try:
                req = BookFlowRequest(**record.book_request)
            except ValidationError:
                continue
            failed_run_dir = (record.production_status or {}).get("run_dir") if isinstance(record.production_status, dict) else None
            if failed_run_dir:
                _ensure_run_journal_terminal(
                    failed_run_dir,
                    task_id,
                    "run sealed by reconcile integrity pass",
                )

        _write_resource_tracker_locked(reason="book_jobs_reconciled")
        _update_pressure_state_locked()

    _refresh_ui_state_snapshot(event_type="book_jobs_reconciled")
    for event in interruption_events:
        _append_ui_event("book_run_interrupted", event)
        _append_resource_event("book_run_interrupted", event)
    return {
        "status": "ok",
        "resumed": resumed,
        "skipped": skipped,
        "interrupted": interruption_events,
        "resumed_count": len(resumed),
    }


@app.get("/api/tasks/{task_id}/production-status")
def get_book_task_production_status(task_id: str):
    with _task_lock:
        record = _tasks.get(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="task not found")
        if record.profile != "book-flow":
            raise HTTPException(status_code=400, detail="production status is only supported for book-flow tasks")
        if not record.book_request:
            raise HTTPException(status_code=404, detail="book request payload not found")

        req = BookFlowRequest(**record.book_request)
        status_payload = _analyze_book_production(req)
        record.production_status = status_payload
        _write_resource_tracker_locked(reason="book_task_production_status")

    _refresh_ui_state_snapshot(event_type="book_task_production_status")
    return {
        "task_id": task_id,
        "status": record.status,
        "hold": record.hold,
        "retry_count": record.retry_count,
        "max_auto_retries": record.max_auto_retries,
        "production_status": status_payload,
    }


@app.post("/api/tasks/{task_id}/spawn-control")
def spawn_control(task_id: str, req: SpawnControlRequest):
    action = (req.action or "").strip().lower()
    if action not in {"go", "stop", "pause"}:
        raise HTTPException(status_code=400, detail="action must be one of: go, stop, pause")

    delegate_cancel = False
    detail = ""
    payload = None

    with _task_lock:
        record = _tasks.get(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="task not found")

        if action == "go":
            if record.status != "queued":
                raise HTTPException(status_code=409, detail="go is only valid for queued tasks")
            record.spawn_release_at = time.time()
            detail = "Task released for immediate spawn."

        elif action == "pause":
            if record.status != "queued":
                raise HTTPException(status_code=409, detail="pause is only valid for queued tasks")
            _set_spawn_release_locked(record, delay_seconds=SPAWN_PRE_CREATE_DELAY_SECONDS)
            detail = f"Task spawn paused for {int(SPAWN_PRE_CREATE_DELAY_SECONDS)} seconds."

        elif action == "stop":
            if record.status == "running":
                delegate_cancel = True
            elif record.status == "queued":
                record.status = "cancelled"
                record.error = "Cancelled by user via spawn-control stop"
                record.finished_at = time.time()
                record.spawn_release_at = None
                record.next_retry_at = None
                _update_pressure_state_locked()
                _write_resource_tracker_locked(reason="task_cancelled")
                detail = "Queued task cancelled before spawn."
            else:
                detail = f"Task already in terminal state: {record.status}."

        queue_positions = _compute_queue_positions(list(_tasks.values()))
        payload = _task_to_dict(record, queue_position=queue_positions.get(record.id))

    if delegate_cancel:
        cancelled = cancel_task(task_id)
        _append_ui_event("spawn_control_action", {"task_id": task_id, "action": action, "delegate": "cancel"})
        return {
            "status": "ok",
            "task_id": task_id,
            "action": action,
            "detail": "Running task cancellation requested via spawn-control stop.",
            "task": cancelled,
        }

    _append_ui_event("spawn_control_action", {"task_id": task_id, "action": action})
    _refresh_ui_state_snapshot(event_type="spawn_control_action")
    return {
        "status": "ok",
        "task_id": task_id,
        "action": action,
        "detail": detail,
        "task": payload,
    }


@app.post("/api/stream")
def stream(req: StreamRequest):
    merged_prompt = _compose_prompt(req.direction, req.prompt)
    try:
        plan = orchestrator.plan_request(
            merged_prompt,
            profile_name=req.profile,
            stream_override=True,
            model_override=req.model,
        )
    except AgentStackError as err:
        raise _agent_error_to_http_exception(err, default_status=400) from err
    resolved_profile_name = (plan.get("profile") or {}).get("name")
    _pressure_guard(resolved_profile_name, context="/api/stream")

    with _task_lock:
        if STRICT_ONE_MODEL_PER_ROUTE:
            conflict_model = _find_route_model_conflict(plan.get("route"), plan.get("model"))
            if conflict_model:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"route {plan.get('route')} is busy with model {conflict_model}; "
                        f"requested model {plan.get('model')} is blocked until queue drains"
                    ),
                )
        _update_pressure_state_locked()
        _write_resource_tracker_locked(reason="stream_admission")

    def token_stream():
        def callback(token, _chunk):
            lines.append(token)

        lines = []
        done = False
        error = None

        def runner():
            nonlocal done, error
            try:
                orchestrator.handle_request_with_overrides(
                    merged_prompt,
                    profile_name=req.profile,
                    model_override=req.model,
                    stream_override=True,
                    on_stream=callback,
                )
            except AgentStackError as err:
                error = f"[{err.code}] {err}"
            except Exception as err:
                wrapped = AgentUnexpectedError(
                    f"Unexpected stream endpoint failure: {err}",
                    details={"profile": req.profile, "model": req.model},
                )
                error = f"[{wrapped.code}] {wrapped}"
            finally:
                done = True

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        offset = 0
        while True:
            while offset < len(lines):
                token = lines[offset]
                offset += 1
                yield token
            if done:
                break
            time.sleep(0.05)

        if error:
            yield f"\n[error] {error}\n"

    return StreamingResponse(token_stream(), media_type="text/plain")


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    with _task_lock:
        record = _tasks.get(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="task not found")
        if record.status in {"completed", "failed", "cancelled"}:
            return {"task_id": task_id, "status": record.status}

        if record.status == "running" and (record.route or "").startswith("ollama_"):
            if not orchestrator.is_route_call_active(record.route):
                started_at = float(record.started_at or 0.0)
                transition_age = time.time() - started_at if started_at > 0 else 0.0
                if transition_age < 15.0:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "cancel rejected: no active Ollama run detected yet for this task route; "
                            "retry in a few seconds if the task remains running"
                        ),
                    )
        # Soft cancel: write out tracking log/summary before cancelling
        try:
            # For book mode, write to book_runs/<book>/cancelled.md and update book docs
            if record.profile == "book-flow":
                import datetime
                import json
                # Try to extract book run dir from prompt or response
                run_dir = None
                summary = None
                if record.response:
                    try:
                        summary = json.loads(record.response)
                        run_dir = summary.get('run_dir')
                    except json.JSONDecodeError:
                        pass
                if not run_dir:
                    run_dir = "/home/daravenrk/dragonlair/book_runs/cancelled_runs"
                Path(run_dir).mkdir(parents=True, exist_ok=True)
                log_path = Path(run_dir) / "cancelled.md"
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"# Book Mode Cancelled\nCancelled at: {datetime.datetime.now().isoformat()}\nTask ID: {task_id}\nPrompt: {record.prompt}\nStatus: {record.status}\n\n")
                # Also update overview.md and section/chapter files with cancellation note
                try:
                    overview_path = Path(run_dir) / "overview.md"
                    with open(overview_path, "a", encoding="utf-8") as f:
                        f.write(f"\n---\nBook workflow was cancelled at {datetime.datetime.now().isoformat()} (Task ID: {task_id})\n")
                    # If summary has section_title, update that section file
                    if summary and summary.get('section_title'):
                        section_slug = summary['section_title'].strip().lower().replace(' ', '_')
                        section_path = Path(run_dir) / f"sections/{section_slug}.md"
                        section_path.parent.mkdir(parents=True, exist_ok=True)
                        with open(section_path, "a", encoding="utf-8") as f:
                            f.write(f"\n---\nSection interrupted and cancelled at {datetime.datetime.now().isoformat()} (Task ID: {task_id})\n")
                except OSError as docerr:
                    record.error = f"Cancelled by user (log/doc error: {docerr})"
            # For coding mode, write to dragonlair/coding_cancelled.md
            elif record.profile in {"amd-coder", "nvidia-fast", "nvidia-lowlatency"}:
                log_path = Path("/home/daravenrk/dragonlair/coding_cancelled.md")
                with open(log_path, "a", encoding="utf-8") as f:
                    import datetime
                    f.write(f"# Coding Task Cancelled\nCancelled at: {datetime.datetime.now().isoformat()}\nTask ID: {task_id}\nPrompt: {record.prompt}\nStatus: {record.status}\n\n")
        except OSError as logerr:
            record.error = f"Cancelled by user (log error: {logerr})"
        else:
            record.error = "Cancelled by user (soft cancel)"
        record.status = "cancelled"
        record.finished_at = time.time()
        _update_pressure_state_locked()
        _write_resource_tracker_locked(reason="task_cancelled")
        # Optionally, notify producer/creator here (not implemented)
    _refresh_ui_state_snapshot(event_type="task_cancelled")
    return {"task_id": task_id, "status": "cancelled"}
