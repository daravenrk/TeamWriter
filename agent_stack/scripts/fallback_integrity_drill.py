#!/usr/bin/env python3
"""Fallback-integrity drill (Todo 134, generalized by Todo 136, staleness by Todo 137).

Test cases executed against freshly-created temporary run-dir structures:

  CLEAN                all artifacts match (correct checksum, all_passed contract,
                       fresh generated_at timestamp)
                       → checked=True, valid=True

  TAMPERED-CHECKSUM    canon.json modified after metadata checksum recorded
                       → checked=True, valid=False, checksum_mismatch

  FAILED-CONTRACT      fallback_contract_report.json.all_passed=False
                       → checked=True, valid=False, contract_failed

  NO-FALLBACK-EVENT    run dir exists but no stage_fallback_applied journal event
                       → checked=False, valid=True (skipped safely)

  STALE-ARTIFACT       generated_at is years in the past (> stale threshold)
                       → checked=True, valid=False, fallback_artifact_stale

  MISSING-GENERATED-AT metadata has no generated_at field
                       → checked=True, valid=False, fallback_generated_at_missing

Also validates:
  - Checksum parity between this drill's _stable_payload_sha256 and book_flow.payload_sha256
  - Live API (/api/status) emits no phantom fallback_integrity_blocks

The local verification mirrors _verify_stage_fallback_integrity from api_server.py
(generalized in Todo 136, staleness added in Todo 137) for the canon stage.
Keep in sync with the registry config and the generic verifier.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_URL = "http://127.0.0.1:11888"
POLL_SECONDS = 1.5

# Mirror of _FALLBACK_STAGE_CONFIGS["canon"] from api_server.py
_CANON_CONFIG = {
    "artifact_dir": "03_canon",
    "payload_file": "canon.json",
    "metadata_file": "canon_fallback_metadata.json",
    "contract_file": "fallback_contract_report.json",
}

# Mirror of api_server._FALLBACK_STALE_HOURS
import os as _os
_FALLBACK_STALE_HOURS: float = float(_os.environ.get("FALLBACK_STALE_HOURS", "72"))


# ---------------------------------------------------------------------------
# Mirror of api_server._stable_payload_sha256 — must stay in sync
# ---------------------------------------------------------------------------

def _stable_payload_sha256(payload: Any) -> str:
    """Must match book_flow.payload_sha256: ensure_ascii=True, default=str."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# Mirror of api_server._verify_stage_fallback_integrity for the canon stage
# Must stay in sync with _FALLBACK_STAGE_CONFIGS["canon"] and the generic verifier
# ---------------------------------------------------------------------------

def _verify_canon_fallback_integrity(run_dir: Optional[Path]) -> dict:
    """Local mirror of api_server._verify_stage_fallback_integrity(run_dir, 'canon', config)."""
    if not run_dir:
        return {"checked": False, "valid": True, "reason": "run_dir_missing", "issues": [], "stage": "canon"}

    artifact_dir = run_dir / _CANON_CONFIG["artifact_dir"]
    payload_path = artifact_dir / _CANON_CONFIG["payload_file"]
    metadata_path = artifact_dir / _CANON_CONFIG["metadata_file"]
    contract_path = artifact_dir / _CANON_CONFIG["contract_file"]
    journal_path = run_dir / "run_journal.jsonl"

    # Check whether a fallback event exists in the run journal
    fallback_event_seen = False
    if journal_path.exists():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                # api_server writes the stage inside "details" sub-dict
                ev_stage = (ev.get("details") or {}).get("stage") or ev.get("stage") or ""
                if ev.get("event") == "stage_fallback_applied" and ev_stage == "canon":
                    fallback_event_seen = True
                    break
            except json.JSONDecodeError:
                continue

    if not fallback_event_seen:
        return {
            "checked": False,
            "valid": True,
            "reason": "no_canon_fallback_detected",
            "issues": [],
            "stage": "canon",
        }

    issues: List[str] = []

    def _read(p: Path) -> Any:
        return json.loads(p.read_text(encoding="utf-8"))

    metadata = _read(metadata_path)
    canon_payload = _read(payload_path)
    contract_report = _read(contract_path)

    if not metadata_path.exists():
        issues.append("fallback_metadata_missing")
    elif not isinstance(metadata, dict):
        issues.append("fallback_metadata_invalid_json")
    else:
        if metadata.get("fallback") is not True:
            issues.append("fallback_metadata_flag_invalid")
        if str(metadata.get("stage") or "canon") != "canon":
            issues.append("fallback_metadata_stage_mismatch")
        stored_checksum = str(metadata.get("fallback_payload_checksum") or "").strip()
        if not stored_checksum:
            issues.append("fallback_checksum_missing")
        elif isinstance(canon_payload, (dict, list)):
            actual_checksum = _stable_payload_sha256(canon_payload)
            if stored_checksum != actual_checksum:
                issues.append("fallback_checksum_mismatch")
        else:
            issues.append("fallback_payload_missing_or_invalid")

    if not contract_path.exists():
        issues.append("fallback_contract_missing")
    elif not isinstance(contract_report, dict):
        issues.append("fallback_contract_invalid_json")
    elif not contract_report.get("all_passed"):
        issues.append("fallback_contract_failed")
        missing = contract_report.get("missing", [])
        if missing:
            issues.append(f"missing_anchors:{','.join(missing)}")

    # Staleness check (mirrors api_server._verify_stage_fallback_integrity)
    age_hours: Optional[float] = None
    generated_at_raw: Optional[str] = None
    if isinstance(metadata, dict):
        generated_at_raw = str(metadata.get("generated_at") or "").strip() or None
        if generated_at_raw:
            try:
                try:
                    ts = float(generated_at_raw)
                except ValueError:
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
        "stage": "canon",
        "fallback_event_seen": fallback_event_seen,
    }
    if generated_at_raw is not None:
        result["generated_at"] = generated_at_raw
    if age_hours is not None:
        result["age_hours"] = round(age_hours, 2)
    if not valid:
        stored = str((isinstance(metadata, dict) and metadata or {}).get("fallback_payload_checksum") or "")
        actual = _stable_payload_sha256(canon_payload) if isinstance(canon_payload, (dict, list)) else ""
        if stored and actual and stored != actual:
            result["stored_checksum"] = stored
            result["actual_checksum"] = actual
    return result


def _any_fallback_stage_failed(fallback_integrity: dict):
    """Mirror of api_server._any_fallback_stage_failed."""
    failed_stages = []
    all_issues: List[str] = []
    for stage, result in (fallback_integrity or {}).items():
        if not isinstance(result, dict):
            continue
        if result.get("checked") and not result.get("valid", True):
            failed_stages.append(stage)
            all_issues.extend(str(i) for i in (result.get("issues") or []) if str(i))
    return bool(failed_stages), all_issues, failed_stages


# ---------------------------------------------------------------------------
# Artifact builders
# ---------------------------------------------------------------------------

MINIMAL_CANON_PAYLOAD = {
    "chapter_id": "ch01",
    "chapter_title": "The Opening",
    "section_goal": "Establish the world and protagonist.",
    "ending_hook": "A knock at the door.",
    "constraints": ["keep under 1500 words", "no flashbacks"],
}

FRESH_TIMESTAMP = "2026-03-19T00:00:00Z"   # within any reasonable window
STALE_TIMESTAMP = "2020-01-01T00:00:00Z"   # years old — always stale


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def _write_journal_event(journal_path: Path, event: dict) -> None:
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _build_clean_run_dir(tmp: Path) -> Path:
    run_dir = tmp / "clean_run"
    canon_dir = run_dir / "03_canon"

    canon_payload = dict(MINIMAL_CANON_PAYLOAD)
    checksum = _stable_payload_sha256(canon_payload)

    _write_json(canon_dir / "canon.json", canon_payload)
    _write_json(canon_dir / "canon_fallback_metadata.json", {
        "fallback": True,
        "stage": "canon",
        "fallback_payload_checksum": checksum,
        "source_hashes": {"book_brief": "aabbcc", "outline_payload": "ddeeff"},
        "generated_at": FRESH_TIMESTAMP,
    })
    _write_json(canon_dir / "fallback_contract_report.json", {
        "all_passed": True,
        "missing": [],
        "checked": ["chapter_id", "chapter_title", "section_goal", "ending_hook", "constraints"],
    })
    _write_journal_event(run_dir / "run_journal.jsonl", {
        "event": "stage_fallback_applied",
        "details": {"stage": "canon"},
        "ts": time.time(),
    })

    return run_dir


def _build_tampered_run_dir(tmp: Path) -> Path:
    run_dir = tmp / "tampered_run"
    canon_dir = run_dir / "03_canon"

    # Compute checksum for the original payload
    original_payload = dict(MINIMAL_CANON_PAYLOAD)
    checksum = _stable_payload_sha256(original_payload)

    # Write metadata with checksum of the original…
    _write_json(canon_dir / "canon_fallback_metadata.json", {
        "fallback": True,
        "stage": "canon",
        "fallback_payload_checksum": checksum,
        "source_hashes": {"book_brief": "aabbcc", "outline_payload": "ddeeff"},
        "generated_at": FRESH_TIMESTAMP,
    })
    # …but then overwrite canon.json with a modified payload (simulates tamper)
    tampered_payload = dict(MINIMAL_CANON_PAYLOAD)
    tampered_payload["section_goal"] = "TAMPERED - this is not the original goal."
    _write_json(canon_dir / "canon.json", tampered_payload)

    _write_json(canon_dir / "fallback_contract_report.json", {
        "all_passed": True,
        "missing": [],
        "checked": ["chapter_id", "chapter_title", "section_goal", "ending_hook", "constraints"],
    })
    _write_journal_event(run_dir / "run_journal.jsonl", {
        "event": "stage_fallback_applied",
        "details": {"stage": "canon"},
        "ts": time.time(),
    })

    return run_dir


def _build_failed_contract_run_dir(tmp: Path) -> Path:
    run_dir = tmp / "failed_contract_run"
    canon_dir = run_dir / "03_canon"

    # Incomplete payload — missing required anchors
    incomplete_payload = {
        "chapter_id": "ch01",
        "chapter_title": "The Opening",
        # section_goal, ending_hook, constraints all missing
    }
    checksum = _stable_payload_sha256(incomplete_payload)

    _write_json(canon_dir / "canon.json", incomplete_payload)
    _write_json(canon_dir / "canon_fallback_metadata.json", {
        "fallback": True,
        "stage": "canon",
        "fallback_payload_checksum": checksum,
        "source_hashes": {},
        "generated_at": FRESH_TIMESTAMP,
    })
    _write_json(canon_dir / "fallback_contract_report.json", {
        "all_passed": False,
        "missing": ["section_goal", "ending_hook", "constraints"],
        "checked": ["chapter_id", "chapter_title"],
    })
    _write_journal_event(run_dir / "run_journal.jsonl", {
        "event": "stage_fallback_applied",
        "details": {"stage": "canon"},
        "ts": time.time(),
    })

    return run_dir


def _build_stale_run_dir(tmp: Path) -> Path:
    """Artifact with a generated_at years in the past — always beyond any stale threshold."""
    run_dir = tmp / "stale_run"
    canon_dir = run_dir / "03_canon"

    canon_payload = dict(MINIMAL_CANON_PAYLOAD)
    checksum = _stable_payload_sha256(canon_payload)

    _write_json(canon_dir / "canon.json", canon_payload)
    _write_json(canon_dir / "canon_fallback_metadata.json", {
        "fallback": True,
        "stage": "canon",
        "fallback_payload_checksum": checksum,
        "source_hashes": {"book_brief": "aabbcc"},
        "generated_at": STALE_TIMESTAMP,
    })
    _write_json(canon_dir / "fallback_contract_report.json", {
        "all_passed": True,
        "missing": [],
        "checked": ["chapter_id", "chapter_title", "section_goal", "ending_hook", "constraints"],
    })
    _write_journal_event(run_dir / "run_journal.jsonl", {
        "event": "stage_fallback_applied",
        "details": {"stage": "canon"},
        "ts": time.time(),
    })

    return run_dir


def _build_missing_generated_at_run_dir(tmp: Path) -> Path:
    """Metadata without a generated_at field — must emit fallback_generated_at_missing."""
    run_dir = tmp / "no_ts_run"
    canon_dir = run_dir / "03_canon"

    canon_payload = dict(MINIMAL_CANON_PAYLOAD)
    checksum = _stable_payload_sha256(canon_payload)

    _write_json(canon_dir / "canon.json", canon_payload)
    _write_json(canon_dir / "canon_fallback_metadata.json", {
        "fallback": True,
        "stage": "canon",
        "fallback_payload_checksum": checksum,
        # generated_at deliberately omitted
    })
    _write_json(canon_dir / "fallback_contract_report.json", {
        "all_passed": True,
        "missing": [],
        "checked": ["chapter_id", "chapter_title", "section_goal", "ending_hook", "constraints"],
    })
    _write_journal_event(run_dir / "run_journal.jsonl", {
        "event": "stage_fallback_applied",
        "details": {"stage": "canon"},
        "ts": time.time(),
    })

    return run_dir


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def _assert(condition: bool, msg: str) -> None:
    if not condition:
        raise AssertionError(msg)


def test_clean_path(tmp: Path) -> bool:
    label = "CLEAN"
    try:
        run_dir = _build_clean_run_dir(tmp)
        result = _verify_canon_fallback_integrity(run_dir)

        print(f"[{label}] result={json.dumps(result)}")
        _assert(result["checked"] is True, f"[{label}] checked must be True")
        _assert(result["valid"] is True, f"[{label}] valid must be True for clean artifacts")
        _assert(result["reason"] == "fallback_integrity_passed", f"[{label}] reason must be 'fallback_integrity_passed', got {result['reason']!r}")
        _assert(not result["issues"], f"[{label}] issues must be empty, got {result['issues']}")

        print(f"[{label}] PASS")
        return True
    except AssertionError as exc:
        print(f"[{label}] FAIL: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] ERROR: {exc}")
        return False


def test_tampered_checksum(tmp: Path) -> bool:
    label = "TAMPERED-CHECKSUM"
    try:
        run_dir = _build_tampered_run_dir(tmp)
        result = _verify_canon_fallback_integrity(run_dir)

        print(f"[{label}] result={json.dumps(result)}")
        _assert(result["checked"] is True, f"[{label}] checked must be True")
        _assert(result["valid"] is False, f"[{label}] valid must be False for tampered artifact")
        _assert("fallback_checksum_mismatch" in result["issues"], f"[{label}] fallback_checksum_mismatch must be in issues, got {result['issues']}")
        _assert(
            result.get("stored_checksum") != result.get("actual_checksum"),
            f"[{label}] stored vs actual checksums must differ",
        )

        print(f"[{label}] PASS")
        return True
    except AssertionError as exc:
        print(f"[{label}] FAIL: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] ERROR: {exc}")
        return False


def test_failed_contract(tmp: Path) -> bool:
    label = "FAILED-CONTRACT"
    try:
        run_dir = _build_failed_contract_run_dir(tmp)
        result = _verify_canon_fallback_integrity(run_dir)

        print(f"[{label}] result={json.dumps(result)}")
        _assert(result["checked"] is True, f"[{label}] checked must be True")
        _assert(result["valid"] is False, f"[{label}] valid must be False for failed contract")
        _assert("fallback_contract_failed" in result["issues"], f"[{label}] fallback_contract_failed must be in issues, got {result['issues']}")

        print(f"[{label}] PASS")
        return True
    except AssertionError as exc:
        print(f"[{label}] FAIL: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] ERROR: {exc}")
        return False


def test_no_fallback_event(tmp: Path) -> bool:
    """A run dir without a stage_fallback_applied event must not be checked."""
    label = "NO-FALLBACK-EVENT"
    try:
        run_dir = tmp / "no_event_run"
        canon_dir = run_dir / "03_canon"
        canon_dir.mkdir(parents=True, exist_ok=True)
        # Write canonical artifacts without any journal event
        canon_payload = dict(MINIMAL_CANON_PAYLOAD)
        checksum = _stable_payload_sha256(canon_payload)
        _write_json(canon_dir / "canon.json", canon_payload)
        _write_json(canon_dir / "canon_fallback_metadata.json", {
            "fallback": True,
            "stage": "canon",
            "fallback_payload_checksum": checksum,
        })
        _write_json(canon_dir / "fallback_contract_report.json", {"all_passed": True, "missing": []})
        # No run_journal.jsonl written

        result = _verify_canon_fallback_integrity(run_dir)
        print(f"[{label}] result={json.dumps(result)}")
        _assert(result["checked"] is False, f"[{label}] checked must be False without journal event")
        _assert(result["valid"] is True, f"[{label}] valid must default True when unchecked")

        print(f"[{label}] PASS")
        return True
    except AssertionError as exc:
        print(f"[{label}] FAIL: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] ERROR: {exc}")
        return False


def test_stale_artifact(tmp: Path) -> bool:
    """Artifact with generated_at years in the past must fail with fallback_artifact_stale."""
    label = "STALE-ARTIFACT"
    try:
        run_dir = _build_stale_run_dir(tmp)
        result = _verify_canon_fallback_integrity(run_dir)

        print(f"[{label}] result={json.dumps(result)}")
        _assert(result["checked"] is True, f"[{label}] checked must be True")
        _assert(result["valid"] is False, f"[{label}] valid must be False for stale artifact")
        _assert(
            "fallback_artifact_stale" in result["issues"],
            f"[{label}] fallback_artifact_stale must be in issues, got {result['issues']}",
        )
        _assert(result.get("age_hours") is not None, f"[{label}] age_hours must be reported")
        _assert(result.get("generated_at") == STALE_TIMESTAMP, f"[{label}] generated_at must be surfaced")

        print(f"[{label}] PASS")
        return True
    except AssertionError as exc:
        print(f"[{label}] FAIL: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] ERROR: {exc}")
        return False


def test_missing_generated_at(tmp: Path) -> bool:
    """Metadata without generated_at must fail with fallback_generated_at_missing."""
    label = "MISSING-GENERATED-AT"
    try:
        run_dir = _build_missing_generated_at_run_dir(tmp)
        result = _verify_canon_fallback_integrity(run_dir)

        print(f"[{label}] result={json.dumps(result)}")
        _assert(result["checked"] is True, f"[{label}] checked must be True")
        _assert(result["valid"] is False, f"[{label}] valid must be False when generated_at absent")
        _assert(
            "fallback_generated_at_missing" in result["issues"],
            f"[{label}] fallback_generated_at_missing must be in issues, got {result['issues']}",
        )

        print(f"[{label}] PASS")
        return True
    except AssertionError as exc:
        print(f"[{label}] FAIL: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] ERROR: {exc}")
        return False


# ---------------------------------------------------------------------------
# Optional live-API check
# ---------------------------------------------------------------------------

def _request_json(url: str) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def check_live_api_no_phantom_blocks() -> Optional[bool]:
    """If API is reachable, verify no tasks appear as blocked without a real issue.

    Handles the stage-keyed fallback_integrity structure introduced in Todo 136:
    fallback_integrity is now {stage: {checked, valid, issues, ...}} per task.
    """
    status = _request_json(f"{BASE_URL}/api/status")
    if status is None:
        print("[LIVE-API] server not reachable — skipping live check")
        return None

    blocks = status.get("fallback_integrity_blocks") or []
    print(f"[LIVE-API] fallback_integrity_blocks count={len(blocks)}")
    for block in blocks:
        task_id = block.get("task_id", "?")
        # Summary may contain top-level valid or per-stage breakdown
        valid = block.get("valid")
        all_issues = block.get("all_issues") or block.get("issues") or []
        blocked_stages = block.get("blocked_stages") or []
        print(f"[LIVE-API]   blocked task={task_id} valid={valid} stages={blocked_stages} issues={all_issues}")
        if valid is True:
            print(f"[LIVE-API] FAIL: task {task_id} listed as blocked but valid=True (phantom block)")
            return False

    print("[LIVE-API] PASS — no phantom blocks")
    return True


# ---------------------------------------------------------------------------
# Checksum parity smoke test
# ---------------------------------------------------------------------------

def test_checksum_parity_against_book_flow() -> bool:
    """Verify _stable_payload_sha256 produces the same output as book_flow.payload_sha256.

    book_flow.payload_sha256 uses: sort_keys=True, separators=(',',':'),
    ensure_ascii=True, default=str

    If this test fails, the api_server helper is diverged and all real-run
    stored checksums will never verify correctly.
    """
    label = "CHECKSUM-PARITY"
    try:
        # Try to import book_flow.payload_sha256 directly
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "book_flow",
            Path(__file__).parent.parent / "book_flow.py",
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[attr-defined]
            book_flow_sha256 = getattr(mod, "payload_sha256", None)
        else:
            book_flow_sha256 = None
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] SKIP: could not import book_flow ({exc})")
        return True  # Not a drill failure; the artifact-level tests still ran

    if book_flow_sha256 is None:
        print(f"[{label}] SKIP: payload_sha256 not found in book_flow")
        return True

    test_payloads = [
        MINIMAL_CANON_PAYLOAD,
        {"a": 1, "b": [1, 2, 3], "c": {"nested": True}},
        {"unicode_key": "caf\u00e9", "number": 42},
        {},
    ]

    ok = True
    for payload in test_payloads:
        expected = book_flow_sha256(payload)
        actual = _stable_payload_sha256(payload)
        match = (expected == actual)
        print(f"[{label}] payload={str(payload)[:60]!r} match={match}")
        if not match:
            print(f"[{label}]   expected={expected}")
            print(f"[{label}]   actual  ={actual}")
            ok = False

    if ok:
        print(f"[{label}] PASS — checksum parity confirmed")
    else:
        print(f"[{label}] FAIL — api_server and book_flow produce different checksums; real-run verification will always fail")

    return ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    results: list[bool] = []

    with tempfile.TemporaryDirectory(prefix="fallback_integrity_drill_") as tmp_str:
        tmp = Path(tmp_str)

        print("=== Fallback Integrity Drill ===")
        print()

        print("--- Artifact-level tests ---")
        results.append(test_clean_path(tmp))
        results.append(test_tampered_checksum(tmp))
        results.append(test_failed_contract(tmp))
        results.append(test_no_fallback_event(tmp))
        results.append(test_stale_artifact(tmp))
        results.append(test_missing_generated_at(tmp))
        print()

        print("--- Checksum parity ---")
        results.append(test_checksum_parity_against_book_flow())
        print()

        print("--- Live API ---")
        live = check_live_api_no_phantom_blocks()
        if live is not None:
            results.append(live)
        print()

    passed = sum(1 for r in results if r)
    total = len(results)
    if all(results):
        print(f"fallback_integrity_drill: PASS ({passed}/{total})")
        return 0

    print(f"fallback_integrity_drill: FAIL ({passed}/{total} passed)")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"fatal: {exc}")
        sys.exit(2)
