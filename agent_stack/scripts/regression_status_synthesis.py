#!/usr/bin/env python3
"""Regression check for live route/status synthesis in /api/status.

This script submits one NVIDIA and one AMD diagnostics task, waits until each is
observable in queued/running status, and verifies that:
- task route matches the expected route
- route_active_counts reflects that route while inflight
- health agent fields expose the expected profile/model during running

Tasks are cancelled after validation so this test can run repeatedly.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

BASE_URL = "http://127.0.0.1:11888"
POLL_SECONDS = 1.5
MAX_WAIT_SECONDS = 75


@dataclass(frozen=True)
class Case:
    label: str
    profile: str
    expected_route: str
    prompt: str


CASES = [
    Case(
        label="nvidia",
        profile="nvidia-fast",
        expected_route="ollama_nvidia",
        prompt="Return 30 short numbered lines labelled NVIDIA regression test.",
    ),
    Case(
        label="amd",
        profile="amd-coder",
        expected_route="ollama_amd",
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
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def recover_agents() -> None:
    try:
        _request_json(f"{BASE_URL}/api/recover-hung", method="POST", payload={"force": True})
    except urllib.error.HTTPError as exc:
        # Recovery is best-effort for this regression script.
        if exc.code != 409:
            raise


def submit_task(case: Case) -> str:
    out = _request_json(
        f"{BASE_URL}/api/tasks",
        method="POST",
        payload={"prompt": case.prompt, "profile": case.profile},
    )
    task_id = str(out.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"{case.label}: missing task_id in response: {out}")
    return task_id


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


def validate_case(case: Case) -> bool:
    recover_agents()
    task_id = submit_task(case)
    print(f"[{case.label}] submitted task_id={task_id}")

    saw_route_count = False
    saw_running_agent_details = False
    saw_agent_identity = False
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
                f"agent_profile={route_agent_profile} agent_model={route_agent_model}"
            )

            if task_route != case.expected_route:
                print(f"[{case.label}] FAIL: expected route {case.expected_route}, got {task_route}")
                return False
            if not task_model:
                print(f"[{case.label}] FAIL: task model missing")
                return False

            if task_status in {"queued", "running"} and route_count >= 1:
                saw_route_count = True

            if route_agent_profile == case.profile and bool(route_agent_model):
                saw_agent_identity = True

            if task_status == "running":
                if route_agent_state == "running" and route_agent_profile == case.profile and bool(route_agent_model):
                    saw_running_agent_details = True

            if saw_route_count and (saw_running_agent_details or saw_agent_identity):
                print(f"[{case.label}] PASS")
                return True

            if task_status in {"completed", "failed", "cancelled"} and not (saw_running_agent_details or saw_agent_identity):
                print(f"[{case.label}] FAIL: task ended before route agent identity was observed")
                return False

            time.sleep(POLL_SECONDS)

        print(f"[{case.label}] FAIL: timeout waiting for live synthesis signals")
        return False
    finally:
        cancel_task(task_id)


def main() -> int:
    results = []
    for case in CASES:
        ok = validate_case(case)
        results.append(ok)

    if all(results):
        print("status_synthesis_regression: PASS")
        return 0

    print("status_synthesis_regression: FAIL")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"fatal: {exc}")
        sys.exit(2)
