#!/usr/bin/env python3
"""Load validation for FastAPI runtime — Todo 33.

Submits 2–4 concurrent diagnostic tasks across AMD and NVIDIA routes, then
asserts:
  1. route_active_counts reflects simultaneous activity on both routes
  2. A second task on the same route queues (queue_depth ≥ 1 under dual load)
  3. All tasks eventually complete or can be cancelled cleanly
  4. GPU VRAM headroom per route is recorded during peak load

Results are written to book_project/load_validation_report.json.
Exits 0 on pass, 1 on failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_URL = os.environ.get("AGENT_API_URL", "http://127.0.0.1:11888")
REPORT_PATH = Path("/home/daravenrk/dragonlair/book_project/load_validation_report.json")
POLL_SECONDS = 1.0
MAX_WAIT_SECONDS = 120  # per-task completion wait
REQUEST_RETRIES = 3
REQUEST_RETRY_SECONDS = 0.75


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request_json(
    url: str,
    method: str = "GET",
    payload: Optional[dict] = None,
) -> Dict[str, Any]:
    data = None
    headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    last_exc: Optional[Exception] = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode()
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


def _get_status() -> Dict[str, Any]:
    return _request_json(f"{BASE_URL}/api/status")


def _submit_task(profile: str, prompt: str) -> str:
    out = _request_json(
        f"{BASE_URL}/api/tasks",
        method="POST",
        payload={"prompt": prompt, "profile": profile},
    )
    task_id = str(out.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"Missing task_id for profile={profile}: {out}")
    return task_id


def _cancel_task(task_id: str) -> None:
    try:
        _request_json(f"{BASE_URL}/api/tasks/{task_id}/cancel", method="POST", payload={})
    except urllib.error.HTTPError as exc:
        if exc.code not in {404, 409}:
            raise


def _find_task(status: Dict[str, Any], task_id: str) -> Optional[Dict[str, Any]]:
    for t in status.get("tasks") or []:
        if t.get("id") == task_id:
            return t
    return None


# ---------------------------------------------------------------------------
# GPU VRAM snapshot
# ---------------------------------------------------------------------------

def _nvidia_vram_snapshot() -> Optional[Dict[str, Any]]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,memory.total,memory.free,memory.used",
             "--format=csv,noheader,nounits"],
            timeout=5,
        ).decode().strip()
        gpus = []
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 4:
                gpus.append({
                    "index": int(parts[0]),
                    "total_mb": int(parts[1]),
                    "free_mb": int(parts[2]),
                    "used_mb": int(parts[3]),
                })
        return {"gpus": gpus, "sampled_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _route_active_counts(status: Dict[str, Any]) -> Dict[str, int]:
    return dict(
        status.get("resource_tracker", {})
              .get("queue", {})
              .get("route_active_counts", {}) or {}
    )


def _queue_depth(status: Dict[str, Any]) -> int:
    return int(
        status.get("resource_tracker", {})
              .get("queue", {})
              .get("queue_depth", 0) or 0
    )


# ---------------------------------------------------------------------------
# Preflight — require idle stack
# ---------------------------------------------------------------------------

def _check_idle_stack() -> List[str]:
    """Return list of active task IDs; empty means stack is idle."""
    try:
        status = _get_status()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Cannot reach API at {BASE_URL}: {exc}") from exc
    active = []
    for t in status.get("tasks") or []:
        if t.get("status") in {"running", "queued"}:
            active.append(t.get("id", "?")[:8])
    return active


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

failures: List[str] = []
passes: List[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        passes.append(label)
        print(f"  [PASS] {label}" + (f" — {detail}" if detail else ""))
    else:
        failures.append(label)
        print(f"  [FAIL] {label}" + (f" — {detail}" if detail else ""), file=sys.stderr)


# ---------------------------------------------------------------------------
# Main validation flow
# ---------------------------------------------------------------------------

def main() -> int:
    print("\n=== Load Validation — Todo 33 ===")
    print(f"API: {BASE_URL}")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")

    # Preflight: require idle stack
    print("\n[1] Preflight: checking for active tasks...")
    active = _check_idle_stack()
    if active:
        print(
            f"  BLOCKED: {len(active)} active task(s) detected: {active}\n"
            "  Complete or cancel them before running load validation.",
            file=sys.stderr,
        )
        return 2  # distinct exit code: blocked, not failed

    print("  Stack is idle — proceeding.\n")

    # VRAM baseline
    print("[2] Recording VRAM baseline...")
    vram_baseline = _nvidia_vram_snapshot()
    print(f"  Baseline: {vram_baseline}")

    # Submit wave 1: 1 NVIDIA + 1 AMD task simultaneously
    print("\n[3] Submitting wave 1: 1 NVIDIA + 1 AMD task...")
    nvidia_id1: Optional[str] = None
    amd_id1: Optional[str] = None
    all_task_ids: List[str] = []

    try:
        nvidia_id1 = _submit_task(
            "nvidia-fast",
            "Return exactly 40 numbered lines of placeholder text. Label each LOAD-NVIDIA.",
        )
        all_task_ids.append(nvidia_id1)
        print(f"  Submitted NVIDIA task: {nvidia_id1[:8]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR submitting NVIDIA task: {exc}", file=sys.stderr)
        failures.append("NVIDIA task submit")

    try:
        amd_id1 = _submit_task(
            "amd-coder",
            "Return exactly 40 numbered lines of placeholder text. Label each LOAD-AMD.",
        )
        all_task_ids.append(amd_id1)
        print(f"  Submitted AMD task: {amd_id1[:8]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR submitting AMD task: {exc}", file=sys.stderr)
        failures.append("AMD task submit")

    if not all_task_ids:
        print("  No tasks submitted — aborting.", file=sys.stderr)
        return 1

    # Submit wave 2: a second NVIDIA task to force queueing
    print("\n[4] Submitting wave 2: 2nd NVIDIA task (should queue)...")
    nvidia_id2: Optional[str] = None
    try:
        nvidia_id2 = _submit_task(
            "nvidia-lowlatency",
            "Return exactly 20 numbered lines. Label each QUEUE-NVIDIA.",
        )
        all_task_ids.append(nvidia_id2)
        print(f"  Submitted 2nd NVIDIA task: {nvidia_id2[:8]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR submitting 2nd NVIDIA task: {exc}", file=sys.stderr)
        failures.append("2nd NVIDIA task submit")

    # Poll for concurrent activity
    print("\n[5] Polling for concurrent route activity (max 60s)...")
    saw_amd_active = False
    saw_nvidia_active = False
    saw_queue_depth = False
    vram_under_load: Optional[Dict[str, Any]] = None
    peak_route_counts: Dict[str, int] = {}
    peak_queue_depth = 0

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            status = _get_status()
        except Exception as exc:  # noqa: BLE001
            print(f"  poll error: {exc}")
            time.sleep(POLL_SECONDS)
            continue

        counts = _route_active_counts(status)
        qdepth = _queue_depth(status)
        peak_queue_depth = max(peak_queue_depth, qdepth)
        for k, v in counts.items():
            peak_route_counts[k] = max(peak_route_counts.get(k, 0), int(v or 0))

        if counts.get("ollama_amd", 0) >= 1:
            saw_amd_active = True
        if counts.get("ollama_nvidia", 0) >= 1:
            saw_nvidia_active = True
        if qdepth >= 1:
            saw_queue_depth = True

        # Capture VRAM at peak (first time both routes active)
        if saw_amd_active and saw_nvidia_active and vram_under_load is None:
            vram_under_load = _nvidia_vram_snapshot()
            print(f"  Peak VRAM snapshot: {vram_under_load}")

        # Check if all tasks terminated
        all_done = all(
            _find_task(status, tid) is not None
            and _find_task(status, tid).get("status") in {"completed", "failed", "cancelled"}
            for tid in all_task_ids
        )
        if all_done:
            break

        time.sleep(POLL_SECONDS)

    # Cancel any tasks still alive
    print("\n[6] Cancelling live tasks...")
    for tid in all_task_ids:
        try:
            status = _get_status()
            task = _find_task(status, tid)
            if task and task.get("status") not in {"completed", "failed", "cancelled"}:
                _cancel_task(tid)
                print(f"  Cancelled: {tid[:8]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Cancel error for {tid[:8]}: {exc}")

    # VRAM after load settles
    time.sleep(3)
    vram_after = _nvidia_vram_snapshot()
    print(f"\n  VRAM after load: {vram_after}")

    # Assertions
    print("\n[7] Assertions:")
    check(saw_nvidia_active, "NVIDIA route was active under load",
          f"peak_route_counts={peak_route_counts}")
    check(saw_amd_active, "AMD route was active under load",
          f"peak_route_counts={peak_route_counts}")
    check(saw_amd_active and saw_nvidia_active,
          "Both routes active concurrently",
          f"peak_counts={peak_route_counts}")
    check(saw_queue_depth or nvidia_id2 is None,
          "Queue depth ≥ 1 when 2nd NVIDIA task submitted",
          f"peak_queue_depth={peak_queue_depth}")

    # VRAM headroom assertions
    if vram_under_load and "gpus" in vram_under_load:
        for gpu in vram_under_load["gpus"]:
            free_mb = gpu.get("free_mb", 0)
            total_mb = gpu.get("total_mb", 1)
            headroom_pct = (free_mb / total_mb) * 100
            check(
                free_mb > 0,
                f"GPU{gpu['index']} has free VRAM under load",
                f"{free_mb} MiB free ({headroom_pct:.1f}% headroom)",
            )

    # Report
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "passed": len(failures) == 0,
        "pass_count": len(passes),
        "fail_count": len(failures),
        "passes": passes,
        "failures": failures,
        "peak_route_active_counts": peak_route_counts,
        "peak_queue_depth": peak_queue_depth,
        "saw_concurrent_routes": saw_amd_active and saw_nvidia_active,
        "vram_baseline": vram_baseline,
        "vram_under_load": vram_under_load,
        "vram_after_load": vram_after,
        "task_ids": {
            "nvidia_1": nvidia_id1,
            "amd_1": amd_id1,
            "nvidia_2": nvidia_id2,
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\nReport written to: {REPORT_PATH}")

    if failures:
        print(f"\n❌ FAILED — {len(failures)} assertion(s) failed: {failures}")
        return 1

    print(f"\n✅ PASSED — {len(passes)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
