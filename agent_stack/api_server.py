import os
import threading
import time
import uuid
import glob
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from types import SimpleNamespace
from typing import Dict, Optional, List, Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

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
RESOURCE_TRACKER_PATH = Path(
    os.environ.get("AGENT_RESOURCE_TRACKER_PATH", "/home/daravenrk/dragonlair/book_project/resource_tracker.json")
)
RESOURCE_EVENTS_PATH = Path(
    os.environ.get("AGENT_RESOURCE_EVENTS_PATH", "/home/daravenrk/dragonlair/book_project/resource_events.jsonl")
)
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


def _ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_json_atomic(path: Path, payload: dict):
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


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
    status_counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0}
    route_counts: Dict[str, int] = {}
    model_counts: Dict[str, int] = {}

    for rec in records:
        if rec.status in status_counts:
            status_counts[rec.status] += 1
        if rec.status not in {"queued", "running"}:
            continue
        route = rec.route or "unknown"
        route_counts[route] = route_counts.get(route, 0) + 1
        if rec.model:
            model_counts[rec.model] = model_counts.get(rec.model, 0) + 1

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


def _build_status_payload(records: List[TaskRecord]):
    queue_positions = _compute_queue_positions(records)
    tasks = [
        _task_to_dict(v, queue_position=queue_positions.get(v.id))
        for v in sorted(records, key=lambda t: t.created_at, reverse=True)[:50]
    ]

    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0}
    for task in tasks:
        st = task["status"]
        if st in counts:
            counts[st] += 1

    pending_spawn_groups = _build_pending_spawn_groups(records)

    # Find latest actionable stage event and gate failure from changes.log for book-flow tasks.
    latest_failure = None
    latest_stage_event = None
    for rec in records:
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
            if latest_failure is not None or latest_stage_event is not None:
                break

    health_report = orchestrator.get_agent_health_report()

    # Synthesize inflight running state from task queue route counts.
    # When a route has active (queued/running) tasks but the agent health shows idle,
    # the agent is actually busy waiting for or executing an LLM call — the health
    # dict only reflects in-RPC state which is momentarily idle between book-flow stages.
    route_active: Dict[str, List[str]] = {}
    for rec in records:
        if rec.status in {"queued", "running"} and rec.route:
            route_active.setdefault(rec.route, []).append(rec.id)
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
            detail = f"inflight via task queue ({len(active_task_ids)} active)"
            if sample_task and sample_task.model:
                detail += f" model={sample_task.model}"
            if sample_task and sample_task.status == "running":
                detail += f" task={active_task_ids[0][:8]}"
            info["display_state"] = "running"
            info["status_detail"] = detail
            info["route_inflight"] = len(active_task_ids)

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
        "latest_gate_failure": latest_failure,
        "latest_stage_event": latest_stage_event,
        "latest_ollama_call": _latest_ollama_ledger_entry(),
        "recent_quarantine_events": _recent_quarantine_events(),
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


def _bootstrap_task_ledger():
    loaded = _load_tasks_from_disk()
    if not loaded:
        return

    queued_for_resume: List[tuple] = []
    with _task_lock:
        _tasks.clear()
        _tasks.update(loaded)

        for task_id, record in _tasks.items():
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


def _task_to_dict(record: TaskRecord, queue_position=None):
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
        "retry_count": record.retry_count,
        "max_auto_retries": record.max_auto_retries,
        "hold": record.hold,
        "next_retry_at": record.next_retry_at,
        "production_status": record.production_status,
        "queue_position": queue_position,
        "spawn_release_at": record.spawn_release_at,
        "spawn_requested_at": record.spawn_requested_at,
    }


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
            writer_profile=req.writer_profile,
            editor_profile=req.editor_profile,
            publisher_brief_profile=req.publisher_brief_profile,
            publisher_profile=req.publisher_profile,
            output_dir=req.output_dir,
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
def status():
    _pressure_snapshot()
    return _refresh_ui_state_snapshot(event_type="status_refresh")


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


@app.post("/api/book-jobs/reconcile")
def reconcile_book_jobs():
    resumed = []
    skipped = []
    interruption_events = []

    with _task_lock:
        for task_id, record in _tasks.items():
            if record.profile != "book-flow":
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

            try:
                req = BookFlowRequest(**record.book_request)
            except ValidationError:
                skipped.append({"task_id": task_id, "reason": "invalid_request_payload"})
                continue

            record.production_status = _analyze_book_production(req)

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
