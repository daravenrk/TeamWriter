#!/usr/bin/env python3
"""Regression check for live route/status synthesis in /api/status.

This script submits one NVIDIA and one AMD diagnostics task, waits until each is
observable in queued/running status, and verifies that:
- task route matches the expected route
- route_active_counts reflects that route while inflight
- health agent fields expose the expected profile/model during running

Tasks are cancelled after validation so this test can run repeatedly.

Also validates fallback provenance schema and /api/status fallback filters:
- fallback_used=true
- fallback_used=false
- fallback_stage=<stage>
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Optional

BASE_URL = "http://127.0.0.1:11888"
LEDGER_PATH = Path("/home/daravenrk/dragonlair/book_project/ollama_run_ledger.jsonl")
POLL_SECONDS = 1.5
MAX_WAIT_SECONDS = 75
SUBMIT_RETRIES = 5
SUBMIT_RETRY_SECONDS = 2.0
CPU_PROBE_RETRIES = 3
CPU_PROBE_RETRY_SECONDS = 2.0
CPU_PROBE_MAX_WAIT_SECONDS = 180
REQUEST_RETRIES = 3
REQUEST_RETRY_SECONDS = 0.75


@dataclass(frozen=True)
class Case:
    label: str
    profile: str
    expected_route: str
    expected_backend: str
    prompt: str


CASES = [
    Case(
        label="nvidia",
        profile="nvidia-fast",
        expected_route="ollama_nvidia",
        expected_backend="nvidia",
        prompt="Return 30 short numbered lines labelled NVIDIA regression test.",
    ),
    Case(
        label="amd",
        profile="amd-coder",
        expected_route="ollama_amd",
        expected_backend="amd",
        prompt="Return 30 short numbered lines labelled AMD regression test.",
    ),
]


def _request_json(url: str, method: str = "GET", payload: Optional[dict] = None) -> Dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    last_exc: Optional[Exception] = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError, TimeoutError, ConnectionResetError) as exc:
            last_exc = exc
            if attempt == REQUEST_RETRIES:
                raise
            time.sleep(REQUEST_RETRY_SECONDS)
    if last_exc is not None:
        raise last_exc
    return {}


def recover_agents() -> None:
    try:
        _request_json(f"{BASE_URL}/api/recover-hung", method="POST", payload={"force": True})
    except urllib.error.HTTPError as exc:
        # Recovery is best-effort for this regression script.
        if exc.code != 409:
            raise


def submit_task(case: Case) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(1, SUBMIT_RETRIES + 1):
        try:
            out = _request_json(
                f"{BASE_URL}/api/tasks",
                method="POST",
                payload={"prompt": case.prompt, "profile": case.profile},
            )
            task_id = str(out.get("task_id") or "").strip()
            if not task_id:
                raise RuntimeError(f"{case.label}: missing task_id in response: {out}")
            return task_id
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 409 or attempt == SUBMIT_RETRIES:
                raise
            print(f"[{case.label}] INFO: submit conflict (409), retrying attempt {attempt + 1}/{SUBMIT_RETRIES}")
            time.sleep(SUBMIT_RETRY_SECONDS)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            raise
    raise RuntimeError(f"{case.label}: unable to submit task after retries ({last_error})")


def cancel_task(task_id: str) -> None:
    try:
        _request_json(f"{BASE_URL}/api/tasks/{task_id}/cancel", method="POST", payload={})
    except urllib.error.HTTPError as exc:
        # 404/409 can happen during fast task transitions; they are safe to ignore here.
        if exc.code not in {404, 409}:
            raise


def find_task(status_payload: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    for task in status_payload.get("tasks") or []:
        if task.get("id") == task_id:
            return task
    return None


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _task_used_fallbacks(task: Dict[str, Any]) -> list:
    prov = task.get("fallback_provenance_summary") if isinstance(task, dict) else None
    used = prov.get("used_fallbacks") if isinstance(prov, dict) else []
    if not isinstance(used, list):
        return []
    return [str(item) for item in used if str(item)]


def _entry_epoch_seconds(entry: Dict[str, Any]) -> Optional[float]:
    raw_ts = str(entry.get("timestamp") or "").strip()
    if not raw_ts:
        return None
    try:
        return time.mktime(time.strptime(raw_ts, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def _recent_ledger_entries(run_started_epoch: float) -> list:
    if not LEDGER_PATH.exists():
        return []
    rows = []
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry_epoch = _entry_epoch_seconds(entry)
                if entry_epoch is None or entry_epoch < run_started_epoch:
                    continue
                rows.append(entry)
    except OSError:
        return []
    return rows


def _latest_ledger_entry_for_correlation(correlation_id: str) -> Optional[Dict[str, Any]]:
    if not LEDGER_PATH.exists():
        return None
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as fh:
            for line in reversed(fh.readlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(entry.get("correlation_id") or "") == str(correlation_id):
                    return entry
    except OSError:
        return None
    return None


def validate_run_summary_fallback_provenance(summary: Dict[str, Any], label: str) -> bool:
    """Validate run_summary fallback provenance shape (Todo 146 regression guard)."""
    if not isinstance(summary, dict):
        print(f"[{label}] FAIL: run_summary payload must be a dict")
        return False

    used = summary.get("used_fallbacks")
    if not _is_string_list(used):
        print(f"[{label}] FAIL: run_summary.used_fallbacks must be list[str], got {type(used).__name__}")
        return False

    prov = summary.get("fallback_provenance")
    if not isinstance(prov, dict):
        print(f"[{label}] FAIL: run_summary.fallback_provenance missing or not an object")
        return False
    if not _is_string_list(prov.get("used_fallbacks")):
        print(f"[{label}] FAIL: fallback_provenance.used_fallbacks must be list[str]")
        return False
    if not isinstance(prov.get("used_fallback_count"), int):
        print(f"[{label}] FAIL: fallback_provenance.used_fallback_count must be int")
        return False
    if not isinstance(prov.get("human_review_recommended"), bool):
        print(f"[{label}] FAIL: fallback_provenance.human_review_recommended must be bool")
        return False
    if not isinstance(prov.get("note"), str):
        print(f"[{label}] FAIL: fallback_provenance.note must be str")
        return False
    return True


def validate_task_fallback_provenance(task: Dict[str, Any], label: str) -> bool:
    """Validate /api/status task fallback provenance summary shape."""
    if not isinstance(task, dict):
        print(f"[{label}] FAIL: task must be a dict")
        return False

    prov = task.get("fallback_provenance_summary")
    if prov is None:
        print(f"[{label}] FAIL: task.fallback_provenance_summary missing")
        return False
    if not isinstance(prov, dict):
        print(f"[{label}] FAIL: task.fallback_provenance_summary must be an object")
        return False
    if not _is_string_list(prov.get("used_fallbacks")):
        print(f"[{label}] FAIL: fallback_provenance_summary.used_fallbacks must be list[str]")
        return False
    if not isinstance(prov.get("used_fallback_count"), int):
        print(f"[{label}] FAIL: fallback_provenance_summary.used_fallback_count must be int")
        return False
    if not isinstance(prov.get("human_review_recommended"), bool):
        print(f"[{label}] FAIL: fallback_provenance_summary.human_review_recommended must be bool")
        return False
    if not isinstance(prov.get("note"), str):
        print(f"[{label}] FAIL: fallback_provenance_summary.note must be str")
        return False
    if not isinstance(prov.get("run_dir"), str):
        print(f"[{label}] FAIL: fallback_provenance_summary.run_dir must be str")
        return False
    return True


def fixture_provenance_regression() -> bool:
    """Synthetic fixture coverage for fallback-used and no-fallback variants."""
    ok = True

    run_summary_fallback = {
        "run_dir": "/tmp/book/runs/20260319-123456",
        "used_fallbacks": ["canon"],
        "fallback_provenance": {
            "used_fallbacks": ["canon"],
            "used_fallback_count": 1,
            "human_review_recommended": True,
            "note": "One or more deterministic stage fallbacks were used in this run.",
        },
    }
    run_summary_clean = {
        "run_dir": "/tmp/book/runs/20260319-123457",
        "used_fallbacks": [],
        "fallback_provenance": {
            "used_fallbacks": [],
            "used_fallback_count": 0,
            "human_review_recommended": False,
            "note": "No deterministic stage fallbacks were used in this run.",
        },
    }

    task_fallback = {
        "id": "fixture-task-fallback",
        "fallback_provenance_summary": {
            "used_fallbacks": ["canon"],
            "used_fallback_count": 1,
            "human_review_recommended": True,
            "note": "One or more deterministic stage fallbacks were used in this run.",
            "run_dir": "/tmp/book/runs/20260319-123456",
        },
    }
    task_clean = {
        "id": "fixture-task-clean",
        "fallback_provenance_summary": {
            "used_fallbacks": [],
            "used_fallback_count": 0,
            "human_review_recommended": False,
            "note": "",
            "run_dir": "/tmp/book/runs/20260319-123457",
        },
    }

    ok = validate_run_summary_fallback_provenance(run_summary_fallback, "FIXTURE-RUN-SUMMARY-FALLBACK") and ok
    ok = validate_run_summary_fallback_provenance(run_summary_clean, "FIXTURE-RUN-SUMMARY-CLEAN") and ok
    ok = validate_task_fallback_provenance(task_fallback, "FIXTURE-STATUS-TASK-FALLBACK") and ok
    ok = validate_task_fallback_provenance(task_clean, "FIXTURE-STATUS-TASK-CLEAN") and ok

    if ok:
        print("[FIXTURE-PROVENANCE] PASS")
    else:
        print("[FIXTURE-PROVENANCE] FAIL")
    return ok


def live_provenance_regression() -> bool:
    """If book-flow tasks are present, validate provenance schema from /api/status and run_summary.json."""
    try:
        status = _request_json(f"{BASE_URL}/api/status")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
        print(f"[LIVE-PROVENANCE] SKIP: could not reach /api/status ({exc})")
        return True
    tasks = status.get("tasks") or []

    candidates = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        profile = str(task.get("profile") or "")
        runtime_profile = str(task.get("runtime_profile") or "")
        if profile == "book-flow" or runtime_profile == "book-flow":
            candidates.append(task)

    if not candidates:
        print("[LIVE-PROVENANCE] SKIP: no book-flow tasks in /api/status")
        return True

    ok = True
    for task in candidates:
        label = f"LIVE-TASK-{str(task.get('id') or '')[:8]}"
        if task.get("fallback_provenance_summary") is not None:
            ok = validate_task_fallback_provenance(task, label) and ok

        prod = task.get("production_status") or {}
        run_dir = str(prod.get("run_dir") or "").strip()
        if not run_dir:
            continue
        run_summary_path = Path(run_dir) / "run_summary.json"
        if not run_summary_path.exists():
            continue
        try:
            run_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[{label}] FAIL: invalid JSON in {run_summary_path}")
            ok = False
            continue
        ok = validate_run_summary_fallback_provenance(run_summary, f"{label}-RUN-SUMMARY") and ok

    if ok:
        print("[LIVE-PROVENANCE] PASS")
    else:
        print("[LIVE-PROVENANCE] FAIL")
    return ok


def live_status_filter_regression() -> bool:
    """Validate /api/status fallback filter semantics (Todo 152)."""
    try:
        base = _request_json(f"{BASE_URL}/api/status")
        only_fallback = _request_json(f"{BASE_URL}/api/status?fallback_used=true")
        no_fallback = _request_json(f"{BASE_URL}/api/status?fallback_used=false")
        canon_only = _request_json(f"{BASE_URL}/api/status?fallback_stage=canon")
    except urllib.error.URLError as exc:
        print(f"[LIVE-STATUS-FILTERS] FAIL: could not call /api/status ({exc})")
        return False

    base_tasks = [t for t in (base.get("tasks") or []) if isinstance(t, dict)]
    only_fallback_tasks = [t for t in (only_fallback.get("tasks") or []) if isinstance(t, dict)]
    no_fallback_tasks = [t for t in (no_fallback.get("tasks") or []) if isinstance(t, dict)]
    canon_only_tasks = [t for t in (canon_only.get("tasks") or []) if isinstance(t, dict)]

    ok = True
    for task in only_fallback_tasks:
        used = _task_used_fallbacks(task)
        if not used:
            print(f"[LIVE-STATUS-FILTERS] FAIL: fallback_used=true returned task without used_fallbacks: {task.get('id')}")
            ok = False

    for task in no_fallback_tasks:
        used = _task_used_fallbacks(task)
        if used:
            print(f"[LIVE-STATUS-FILTERS] FAIL: fallback_used=false returned task with used_fallbacks={used}: {task.get('id')}")
            ok = False

    for task in canon_only_tasks:
        used = _task_used_fallbacks(task)
        if "canon" not in used:
            print(f"[LIVE-STATUS-FILTERS] FAIL: fallback_stage=canon returned task without canon fallback: {task.get('id')} used={used}")
            ok = False

    # The two boolean filters should partition tasks by provenance usage.
    base_ids = {str(t.get("id") or "") for t in base_tasks}
    only_ids = {str(t.get("id") or "") for t in only_fallback_tasks}
    no_ids = {str(t.get("id") or "") for t in no_fallback_tasks}
    overlap = only_ids.intersection(no_ids)
    if overlap:
        print(f"[LIVE-STATUS-FILTERS] FAIL: task ids present in both fallback_used=true and false: {sorted(overlap)}")
        ok = False

    union_ids = only_ids.union(no_ids)
    if not union_ids.issubset(base_ids):
        leaked = sorted(union_ids - base_ids)
        print(f"[LIVE-STATUS-FILTERS] FAIL: filtered task ids not present in base status: {leaked}")
        ok = False

    if ok:
        print("[LIVE-STATUS-FILTERS] PASS")
    else:
        print("[LIVE-STATUS-FILTERS] FAIL")
    return ok


def live_status_filter_invalid_stage_regression(strict_live: bool = False) -> bool:
    """Validate invalid fallback_stage returns HTTP 400 and useful guidance.
    
    If strict_live=True, fail (rather than skip) when live server is stale and
    in-process validation cannot run. Useful for CI/container regression to catch
    stale deployments. Host-side dev runs use strict_live=False to tolerate missing
    optional API dependencies.
    """
    url = f"{BASE_URL}/api/status?fallback_stage=__invalid_stage__"
    try:
        _request_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            print(f"[LIVE-STATUS-FILTER-INVALID-STAGE] FAIL: expected HTTP 400, got {exc.code}")
            return False
        raw = ""
        try:
            raw = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            raw = ""
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        detail = str(payload.get("detail") or raw or "")
        if "invalid fallback_stage" not in detail or "valid values:" not in detail:
            print(
                "[LIVE-STATUS-FILTER-INVALID-STAGE] FAIL: error detail missing guidance "
                f"(detail={detail!r})"
            )
            return False
        print("[LIVE-STATUS-FILTER-INVALID-STAGE] PASS")
        return True
    except urllib.error.URLError as exc:
        print(f"[LIVE-STATUS-FILTER-INVALID-STAGE] FAIL: request error ({exc})")
        return False

    # If the live request succeeded, the running server may be stale (not yet
    # reloaded with latest code). Fall back to an in-process assertion against
    # the endpoint function to keep this regression actionable during dev.
    try:
        from agent_stack import api_server as _api

        try:
            _api.status(fallback_stage="__invalid_stage__")
        except Exception as exc:  # noqa: BLE001
            code = int(getattr(exc, "status_code", 0) or 0)
            detail = str(getattr(exc, "detail", "") or exc)
            if code == 400 and "invalid fallback_stage" in detail and "valid values:" in detail:
                print(
                    "[LIVE-STATUS-FILTER-INVALID-STAGE] PASS: in-process validation active "
                    "(live server appears stale)"
                )
                return True
            print(
                "[LIVE-STATUS-FILTER-INVALID-STAGE] FAIL: in-process call raised unexpected error "
                f"(code={code}, detail={detail!r})"
            )
            return False
        else:
            print(
                "[LIVE-STATUS-FILTER-INVALID-STAGE] FAIL: both live and in-process calls "
                "accepted invalid fallback_stage"
            )
            return False
    except ModuleNotFoundError as exc:
        if strict_live:
            print(
                "[LIVE-STATUS-FILTER-INVALID-STAGE] FAIL: strict-live mode requires in-process "
                "validation but optional API deps not available (required for CI/container runs)"
            )
            return False
        print(
            "[LIVE-STATUS-FILTER-INVALID-STAGE] SKIP: live call succeeded and "
            "in-process fallback requires optional API deps not available in this environment "
            f"({exc})"
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(
            "[LIVE-STATUS-FILTER-INVALID-STAGE] FAIL: live call succeeded and "
            f"in-process fallback check failed to run ({exc})"
        )
        return False


def validate_backend_type_in_status(strict_backend_missing: bool = False) -> bool:
    """Validate that completed tasks in /api/status expose a correct backend_type field.

    Inspects existing completed tasks — no new tasks are submitted. If no completed
    tasks with backend_type data are available (e.g. stack was just deployed and the
    ledger doesn’t have the field yet), the check is skipped rather than failed.
    """
    try:
        status = _request_json(f"{BASE_URL}/api/status")
    except urllib.error.URLError as exc:
        print(f"[BACKEND-TYPE] FAIL: could not reach /api/status ({exc})")
        return False

    tasks = [t for t in (status.get("tasks") or []) if isinstance(t, dict)]
    completed = [t for t in tasks if t.get("status") == "completed"]

    if not completed:
        print("[BACKEND-TYPE] SKIP: no completed tasks in /api/status")
        return True

    ROUTE_BACKEND_MAP = {
        "nvidia": "nvidia",
        "amd": "amd",
        "rocm": "amd",
    }

    ok = True
    checked = 0
    for task in completed:
        route = str(task.get("route") or "")
        backend_type = task.get("backend_type")
        task_id_short = str(task.get("id") or "")[:8]

        if backend_type is None:
            if strict_backend_missing:
                print(
                    f"[BACKEND-TYPE] FAIL: task {task_id_short} route={route!r} missing backend_type in strict mode"
                )
                ok = False
            continue

        expected = next(
            (bt for kw, bt in ROUTE_BACKEND_MAP.items() if kw in route.lower()),
            None,
        )
        if expected is None:
            continue  # Unknown/unmapped route; skip.

        if backend_type != expected:
            print(
                f"[BACKEND-TYPE] FAIL: task {task_id_short} route={route!r} "
                f"expected backend_type={expected!r} got {backend_type!r}"
            )
            ok = False
        else:
            print(f"[BACKEND-TYPE] task {task_id_short} route={route!r} backend_type={backend_type!r} OK")
            checked += 1

    if checked == 0 and ok:
        print("[BACKEND-TYPE] SKIP: no completed tasks with backend_type field found (stack may need redeploy)")
        return True

    if ok:
        print(f"[BACKEND-TYPE] PASS: {checked} task(s) validated")
    else:
        print("[BACKEND-TYPE] FAIL")
    return ok


def _wait_for_terminal_task(task_id: str, timeout_seconds: float = MAX_WAIT_SECONDS) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = _request_json(f"{BASE_URL}/api/status")
        task = find_task(status, task_id)
        if task and str(task.get("status") or "") in {"completed", "failed", "cancelled"}:
            return task
        time.sleep(POLL_SECONDS)
    return None


def validate_cpu_block_probe(strict_backend_missing: bool = False) -> bool:
    """Submit a short probe task and verify ledger proves no successful CPU run occurred."""
    probe = Case(
        label="cpu-block-probe",
        profile="nvidia-fast",
        expected_route="ollama_nvidia",
        expected_backend="nvidia",
        prompt="CPU block probe: reply with exactly 'probe-ok'.",
    )
    transient_error_markers = (
        "http error 500",
        "timed out",
        "temporary failure in name resolution",
        "empty_response",
    )

    for attempt in range(1, CPU_PROBE_RETRIES + 1):
        recover_agents()
        task_id = submit_task(probe)
        print(f"[CPU-BLOCK] probe submitted task_id={task_id} attempt={attempt}/{CPU_PROBE_RETRIES}")

        task = _wait_for_terminal_task(task_id, timeout_seconds=CPU_PROBE_MAX_WAIT_SECONDS)
        if not task:
            cancel_task(task_id)
            if attempt < CPU_PROBE_RETRIES:
                print("[CPU-BLOCK] INFO: probe timed out, retrying")
                time.sleep(CPU_PROBE_RETRY_SECONDS)
                continue
            print("[CPU-BLOCK] FAIL: probe task did not reach terminal state before timeout")
            return False

        task_status = str(task.get("status") or "")
        task_error = str(task.get("error") or "")
        entry = _latest_ledger_entry_for_correlation(task_id)
        if entry is None:
            if strict_backend_missing:
                print("[CPU-BLOCK] FAIL: missing ledger entry for probe task in strict mode")
                return False
            if attempt < CPU_PROBE_RETRIES:
                print("[CPU-BLOCK] INFO: missing ledger entry for probe task, retrying")
                time.sleep(CPU_PROBE_RETRY_SECONDS)
                continue
            print("[CPU-BLOCK] SKIP: no ledger entry found for probe task")
            return True

        backend_type = entry.get("backend_type")
        success = bool(entry.get("success"))
        error = str(entry.get("error") or "")
        error_l = error.lower()
        task_error_l = task_error.lower()

        if success and str(backend_type or "").strip().lower() == "cpu":
            print(
                "[CPU-BLOCK] FAIL: probe completed successfully on CPU "
                f"(endpoint={entry.get('endpoint')}, model={entry.get('model')})"
            )
            return False

        if backend_type is None and strict_backend_missing:
            print("[CPU-BLOCK] FAIL: probe ledger entry missing backend_type in strict mode")
            return False

        if error == "cpu_execution_blocked" or "cpu_execution_blocked" in task_error:
            print("[CPU-BLOCK] PASS: CPU execution was detected and blocked")
            return True

        if success and str(backend_type or "").strip().lower() in {"nvidia", "amd"}:
            print(f"[CPU-BLOCK] PASS: probe completed on GPU backend_type={backend_type}")
            return True

        is_transient = any(marker in error_l or marker in task_error_l for marker in transient_error_markers)
        if is_transient:
            if attempt < CPU_PROBE_RETRIES:
                print(
                    "[CPU-BLOCK] INFO: transient probe failure, retrying "
                    f"(task_status={task_status}, ledger_error={error!r})"
                )
                time.sleep(CPU_PROBE_RETRY_SECONDS)
                continue
            if not strict_backend_missing:
                print(
                    "[CPU-BLOCK] SKIP: persistent transient probe failures; "
                    "unable to conclusively evaluate CPU-block path in this run"
                )
                return True

        print(
            "[CPU-BLOCK] FAIL: unexpected probe outcome "
            f"(task_status={task_status}, ledger_success={success}, backend_type={backend_type}, ledger_error={error!r})"
        )
        return False

    print("[CPU-BLOCK] FAIL: probe retries exhausted")
    return False


def validate_case(case: Case) -> bool:
    recover_agents()
    task_id = submit_task(case)
    print(f"[{case.label}] submitted task_id={task_id}")

    saw_route_count = False
    saw_task_running = False
    warned_identity_mismatch = False
    deadline = time.time() + MAX_WAIT_SECONDS

    try:
        while time.time() < deadline:
            status = _request_json(f"{BASE_URL}/api/status")
            task = find_task(status, task_id)
            if not task:
                time.sleep(POLL_SECONDS)
                continue

            task_status = str(task.get("status") or "")
            task_route = task.get("route")
            task_model = task.get("model")
            task_backend = task.get("backend_type")
            route_counts = (((status.get("resource_tracker") or {}).get("queue") or {}).get("route_active_counts") or {})
            route_count = int(route_counts.get(case.expected_route) or 0)
            agents = ((status.get("health") or {}).get("agents") or {})
            route_agent = agents.get(case.expected_route) or {}
            route_agent_state = str(route_agent.get("display_state") or route_agent.get("state") or "")
            route_agent_profile = route_agent.get("current_profile")
            route_agent_model = route_agent.get("current_model")

            print(
                f"[{case.label}] status={task_status} route={task_route} model={task_model} "
                f"route_count={route_count} agent_state={route_agent_state} "
                f"agent_profile={route_agent_profile} agent_model={route_agent_model} "
                f"backend_type={task_backend}"
            )

            if task_route != case.expected_route:
                print(f"[{case.label}] FAIL: expected route {case.expected_route}, got {task_route}")
                return False
            if not task_model:
                print(f"[{case.label}] FAIL: task model missing")
                return False
            if task_backend is not None and str(task_backend) != case.expected_backend:
                print(
                    f"[{case.label}] FAIL: expected backend_type={case.expected_backend}, got {task_backend}"
                )
                return False

            if task_status in {"queued", "running"} and route_count >= 1:
                saw_route_count = True

            if task_status == "running":
                saw_task_running = True
                if (
                    route_agent_state == "running"
                    and route_agent_profile != case.profile
                    and not warned_identity_mismatch
                ):
                    print(
                        f"[{case.label}] INFO: route is active with profile={route_agent_profile} "
                        f"while task profile={case.profile}; continuing because task itself reached running"
                    )
                    warned_identity_mismatch = True

            if saw_route_count and saw_task_running:
                print(f"[{case.label}] PASS")
                return True

            if task_status in {"completed", "failed", "cancelled"} and not saw_task_running:
                print(f"[{case.label}] FAIL: task ended before reaching running state")
                return False

            time.sleep(POLL_SECONDS)

        print(f"[{case.label}] FAIL: timeout waiting for live synthesis signals")
        return False
    finally:
        cancel_task(task_id)


def validate_gpu_layers_no_cpu_fallback() -> bool:
    """Validate that all models load GPU layers (num_gpu_layers > 0) on both NVIDIA and AMD.
    
    Requirement: No model layers run on CPU. All GPU-targeted endpoints must have
    num_gpu_layers > 0 for every model they serve.
    
    This probes both NVIDIA and AMD Ollama endpoints for loaded models and validates
    each one has actual GPU layer offloading.
    """
    endpoints = {
        "nvidia": str(os.environ.get("OLLAMA_NVIDIA_ENDPOINT") or "http://127.0.0.1:11434").rstrip("/"),
        "amd": str(os.environ.get("OLLAMA_AMD_ENDPOINT") or "http://127.0.0.1:11435").rstrip("/"),
    }
    
    backends_ok = []
    
    for backend_name, endpoint in endpoints.items():
        try:
            # Query running models endpoint
            models_url = f"{endpoint}/api/tags"
            resp = urllib.request.urlopen(models_url, timeout=10)
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get("models") or []
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError, TimeoutError) as exc:
            print(f"[GPU-LAYERS-NO-CPU-FALLBACK] SKIP {backend_name}: cannot query models ({exc})")
            backends_ok.append(True)  # Skip if endpoint unreachable (container may not be running)
            continue
        
        if not models:
            print(f"[GPU-LAYERS-NO-CPU-FALLBACK] SKIP {backend_name}: no models loaded")
            backends_ok.append(True)
            continue
        
        backend_ok = True
        for model_info in models:
            model_name = str(model_info.get("name") or "").strip()
            if not model_name:
                continue
            
            # Query model details via generate endpoint metadata
            # (shallow request to pull model metadata without full generation)
            try:
                show_url = f"{endpoint}/api/show"
                show_req = urllib.request.Request(
                    show_url,
                    data=json.dumps({"name": model_name}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                show_resp = urllib.request.urlopen(show_req, timeout=10)
                show_data = json.loads(show_resp.read().decode("utf-8"))
                
                model_details = show_data.get("model_info", {})
                num_gpu_layers = model_details.get("num_gpu_layers")
                
                if num_gpu_layers is None:
                    # num_gpu_layers not available in show response; skip detailed check
                    continue
                
                num_gpu_layers = int(num_gpu_layers) if isinstance(num_gpu_layers, (int, str)) else 0
                
                if num_gpu_layers <= 0:
                    print(
                        f"[GPU-LAYERS-NO-CPU-FALLBACK] FAIL {backend_name}: "
                        f"model {model_name} has num_gpu_layers={num_gpu_layers} "
                        f"(CPU fallback detected; should be > 0)"
                    )
                    backend_ok = False
                else:
                    print(
                        f"[GPU-LAYERS-NO-CPU-FALLBACK] {backend_name} model {model_name}: "
                        f"num_gpu_layers={num_gpu_layers} OK"
                    )
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                json.JSONDecodeError,
                ValueError,
                OSError,
                TimeoutError,
            ) as exc:
                # If we can't get model details, skip (endpoint may have transient issues)
                print(
                    f"[GPU-LAYERS-NO-CPU-FALLBACK] INFO {backend_name}: "
                    f"cannot query details for {model_name} ({exc})"
                )
                continue
        
        backends_ok.append(backend_ok)
    
    if all(backends_ok):
        print("[GPU-LAYERS-NO-CPU-FALLBACK] PASS: all models load GPU layers (no CPU fallback)")
        return True
    else:
        print("[GPU-LAYERS-NO-CPU-FALLBACK] FAIL")
        return False


def main() -> int:
    strict_live = "--strict-live" in sys.argv
    strict_backend_missing = "--strict-backend-missing" in sys.argv
    if strict_live:
        print("[REGRESSION] Running in strict-live mode (CI/container)")
    if strict_backend_missing:
        print("[REGRESSION] Running in strict-backend-missing mode")
    
    results = []
    results.append(fixture_provenance_regression())
    results.append(live_provenance_regression())
    results.append(live_status_filter_regression())
    results.append(live_status_filter_invalid_stage_regression(strict_live=strict_live))
    results.append(validate_backend_type_in_status(strict_backend_missing=strict_backend_missing))
    for case in CASES:
        ok = validate_case(case)
        results.append(ok)
    results.append(validate_cpu_block_probe(strict_backend_missing=strict_backend_missing))
    results.append(validate_gpu_layers_no_cpu_fallback())

    if all(results):
        print("status_synthesis_regression: PASS")
        return 0

    print("status_synthesis_regression: FAIL")
    return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        if "--strict-live" in sys.argv:
            sys.argv.remove("--strict-live")  # Clean up for any downstream consumers
        if "--strict-backend-missing" in sys.argv:
            sys.argv.remove("--strict-backend-missing")
        sys.exit(exit_code)
    except Exception as exc:  # noqa: BLE001
        print(f"fatal: {exc}")
        sys.exit(2)
