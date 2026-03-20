#!/usr/bin/env python3
"""Preflight strategy validator for book-flow critical profiles.

Validates effective route/model planning against a pinned strategy matrix and
GPU policy expectations before launching runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from agent_stack.orchestrator import OrchestratorAgent

DEFAULT_STRATEGY_VERSION = str(
    os.environ.get("AGENT_STRATEGY_VERSION", "2026-03-20-strategy-v1")
).strip() or "2026-03-20-strategy-v1"

DEFAULT_MATRIX: Dict[str, Dict[str, str]] = {
    "book-publisher-brief": {"route": "ollama_nvidia", "model": "qwen3.5:4b"},
    "writing-assistant": {"route": "ollama_nvidia", "model": "qwen3.5:4b"},
    "book-canon": {"route": "ollama_nvidia", "model": "qwen3.5:4b"},
    "book-writer": {"route": "ollama_nvidia", "model": "qwen3.5:4b"},
}


@dataclass
class ValidationResult:
    profile: str
    ok: bool
    route: Optional[str]
    model: Optional[str]
    checks: List[str]
    hints: List[str]


def _hints_for_checks(profile_name: str, checks: List[str]) -> List[str]:
    hints: List[str] = []
    for check in checks:
        text = str(check)
        if text.startswith("expected_route_mismatch"):
            hints.append(
                f"Update {profile_name} route/allowed_routes frontmatter to match strategy matrix."
            )
        elif text.startswith("expected_model_mismatch"):
            hints.append(
                f"Update {profile_name} model/model_allowlist or intentionally change matrix for this strategy version."
            )
        elif text.startswith("allowed_routes_violation"):
            hints.append(f"Fix {profile_name} allowed_routes to include the planned route.")
        elif text.startswith("model_allowlist_violation"):
            hints.append(f"Fix {profile_name} model_allowlist or adjust planned model.")
        elif text == "cpu_backend_block_disabled":
            hints.append("Set AGENT_BLOCK_CPU_BACKEND=true.")
        elif text == "force_full_gpu_layers_disabled":
            hints.append("Set AGENT_FORCE_FULL_GPU_LAYERS=true.")
        elif text.startswith("invalid_gpu_layers"):
            hints.append("Verify AGENT_*_NUM_GPU_BY_MODEL values and full GPU offload policy.")
        elif text.startswith("plan_request_failed"):
            hints.append("Run profile lint and fix profile frontmatter/runtime config errors.")
    unique: List[str] = []
    for hint in hints:
        if hint not in unique:
            unique.append(hint)
    return unique


def _validate_profile(
    orch: OrchestratorAgent,
    profile_name: str,
    expected_route: Optional[str],
    expected_model: Optional[str],
) -> ValidationResult:
    checks: List[str] = []
    route = None
    model = None
    ok = True

    try:
        plan = orch.plan_request("strategy-preflight", profile_name=profile_name)
    except Exception as exc:  # noqa: BLE001
        return ValidationResult(
            profile=profile_name,
            ok=False,
            route=None,
            model=None,
            checks=[f"plan_request_failed:{exc}"],
            hints=["Run profile lint and fix profile frontmatter/runtime config errors."],
        )

    profile = plan.get("profile") or {}
    route = str(plan.get("route") or "").strip() or None
    model = str(plan.get("model") or "").strip() or None

    if not route:
        ok = False
        checks.append("missing_route")
    else:
        checks.append(f"route={route}")

    if not model:
        ok = False
        checks.append("missing_model")
    else:
        checks.append(f"model={model}")

    if expected_route and route != expected_route:
        ok = False
        checks.append(f"expected_route_mismatch:{route}!={expected_route}")

    if expected_model and model != expected_model:
        ok = False
        checks.append(f"expected_model_mismatch:{model}!={expected_model}")

    allowed_routes = {str(item).strip() for item in (profile.get("allowed_routes") or []) if str(item).strip()}
    if allowed_routes and route not in allowed_routes:
        ok = False
        checks.append(f"allowed_routes_violation:{route} not in {sorted(allowed_routes)}")

    model_allowlist = {str(item).strip() for item in (profile.get("model_allowlist") or []) if str(item).strip()}
    if model_allowlist and model not in model_allowlist:
        ok = False
        checks.append(f"model_allowlist_violation:{model} not in {sorted(model_allowlist)}")

    # GPU policy check for routed plans.
    if route in {"ollama_nvidia", "ollama_amd"}:
        if not bool(getattr(orch, "block_cpu_backend", False)):
            ok = False
            checks.append("cpu_backend_block_disabled")
        if not bool(getattr(orch, "force_full_gpu_layers", False)):
            ok = False
            checks.append("force_full_gpu_layers_disabled")
        resolved_layers = orch._resolve_model_num_gpu_layers(model, route)  # pylint: disable=protected-access
        if resolved_layers in (None, 0):
            ok = False
            checks.append(f"invalid_gpu_layers:{resolved_layers}")
        else:
            checks.append(f"num_gpu={resolved_layers}")

    return ValidationResult(
        profile=profile_name,
        ok=ok,
        route=route,
        model=model,
        checks=checks,
        hints=_hints_for_checks(profile_name, checks),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate run strategy preflight for critical profiles")
    parser.add_argument(
        "--strategy-version",
        default=DEFAULT_STRATEGY_VERSION,
        help="Strategy version label for this validation run",
    )
    parser.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        default=None,
        help="Profile name to validate (repeatable). Defaults to critical strategy matrix profiles.",
    )
    parser.add_argument(
        "--matrix-json",
        default="",
        help="Optional JSON object: {profile:{route,model}} overriding default matrix.",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Optional path to write policy compliance report JSON.",
    )
    parser.add_argument(
        "--self-test-drift",
        action="store_true",
        help="Run a synthetic mismatch check to verify drift detection behavior.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    matrix = dict(DEFAULT_MATRIX)
    if args.matrix_json:
        parsed = json.loads(args.matrix_json)
        if not isinstance(parsed, dict):
            raise SystemExit("--matrix-json must decode to an object")
        matrix = {
            str(k): {
                "route": str((v or {}).get("route") or "").strip(),
                "model": str((v or {}).get("model") or "").strip(),
            }
            for k, v in parsed.items()
        }

    profiles = args.profiles or list(matrix.keys())
    orch = OrchestratorAgent()

    results: List[ValidationResult] = []
    for profile in profiles:
        expected = matrix.get(profile, {})
        results.append(
            _validate_profile(
                orch,
                profile,
                expected_route=str(expected.get("route") or "").strip() or None,
                expected_model=str(expected.get("model") or "").strip() or None,
            )
        )

    ok = all(item.ok for item in results)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strategy_version": str(args.strategy_version),
        "overall_ok": ok,
        "checked_profiles": len(results),
        "results": [
            {
                "profile": item.profile,
                "ok": item.ok,
                "route": item.route,
                "model": item.model,
                "checks": item.checks,
                "hints": item.hints,
            }
            for item in results
        ],
    }

    if args.self_test_drift:
        synthetic = _validate_profile(
            orch,
            "book-canon",
            expected_route="ollama_amd",
            expected_model="qwen3.5:27b",
        )
        payload["self_test_drift"] = {
            "expected_to_fail": True,
            "detected": not synthetic.ok,
            "profile": synthetic.profile,
            "route": synthetic.route,
            "model": synthetic.model,
            "checks": synthetic.checks,
            "hints": synthetic.hints,
        }

    if args.report_path:
        out_path = Path(str(args.report_path)).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Strategy Version: {payload['strategy_version']}")
        print(f"Overall: {'PASS' if ok else 'FAIL'}")
        for row in payload["results"]:
            status = "PASS" if row["ok"] else "FAIL"
            print(f"- {row['profile']}: {status} route={row['route']} model={row['model']}")
            for check in row["checks"]:
                print(f"    - {check}")
            for hint in row["hints"]:
                print(f"    * fix: {hint}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
