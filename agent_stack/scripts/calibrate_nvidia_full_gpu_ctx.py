#!/usr/bin/env python3
"""Calibrate max full-GPU context per NVIDIA Ollama model.

Excludes qwen3.5:4b by default (already calibrated separately).
Writes a JSON report with per-model probe evidence.

This utility calls Ollama `/api/generate` directly and intentionally bypasses
the orchestrator runtime preset abstraction. Use it for endpoint/GPU probing,
not for validating profile-governed book-flow behavior.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib import request

OLLAMA_URL = "http://localhost:11434"


@dataclass
class ProbeResult:
    ctx: int
    request_ok: bool
    offload: str
    full_gpu: bool
    seconds: float
    error: str


def sh(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True)


def wait_for_ollama(max_wait_seconds: int = 90) -> None:
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        try:
            out = sh("docker exec ollama_nvidia ollama list")
            if "NAME" in out:
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("ollama_nvidia did not become ready in time")


def restart_nvidia() -> None:
    sh("cd agent_stack && docker compose -f docker-compose.ollama.yml restart ollama_nvidia")
    wait_for_ollama()


def post_generate(model: str, ctx: int, num_gpu: int, timeout: int = 240) -> None:
    payload = {
        "model": model,
        "prompt": "OK",
        "stream": False,
        "keep_alive": 0,
        "options": {
            "num_ctx": int(ctx),
            "num_predict": 1,
            "num_gpu": int(num_gpu),
        },
    }
    req = request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        _ = resp.read()


def parse_offload(logs: str) -> str:
    for line in logs.splitlines()[::-1]:
        m = re.search(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU", line)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
    return "n/a"


def is_full(offload: str) -> bool:
    m = re.match(r"^(\d+)/(\d+)$", offload)
    return bool(m and m.group(1) == m.group(2))


def probe(model: str, ctx: int, num_gpu: int) -> ProbeResult:
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    started = time.time()
    ok = True
    err = ""
    try:
        post_generate(model, ctx, num_gpu=num_gpu)
    except Exception as exc:  # noqa: BLE001
        ok = False
        err = str(exc)
    elapsed = round(time.time() - started, 2)
    if ok:
        logs = sh(f"docker logs --since {since} ollama_nvidia 2>&1")
        offload = parse_offload(logs)
        full_gpu = is_full(offload)
    else:
        # Never trust prior log lines when the request itself failed.
        offload = "n/a"
        full_gpu = False
    return ProbeResult(
        ctx=ctx,
        request_ok=ok,
        offload=offload,
        full_gpu=full_gpu,
        seconds=elapsed,
        error=err[:220],
    )


def get_models(exclude: set[str]) -> list[str]:
    out = sh("docker exec ollama_nvidia ollama list")
    names: list[str] = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        cols = re.split(r"\s{2,}", line)
        if cols and cols[0] not in exclude:
            names.append(cols[0])
    return names


def get_model_max_ctx(model: str) -> int:
    out = sh(f"docker exec ollama_nvidia ollama show {model}")
    m = re.search(r"^\s*context length\s+(\d+)\s*$", out, re.M)
    return int(m.group(1)) if m else 32768


def find_max_full_gpu_ctx(model: str, model_max_ctx: int, num_gpu: int) -> dict:
    probes: list[dict] = []

    baseline = min(8192, model_max_ctx)
    first = probe(model, baseline, num_gpu=num_gpu)
    probes.append(first.__dict__)
    if not first.full_gpu:
        return {
            "model_max_ctx": model_max_ctx,
            "max_full_gpu_ctx": 0,
            "status": "no_full_gpu_even_at_baseline",
            "probes": probes,
        }

    good = baseline
    bad = None
    candidate = baseline

    while candidate < model_max_ctx:
        candidate = min(candidate * 2, model_max_ctx)
        r = probe(model, candidate, num_gpu=num_gpu)
        probes.append(r.__dict__)
        if r.full_gpu:
            good = candidate
            if candidate == model_max_ctx:
                return {
                    "model_max_ctx": model_max_ctx,
                    "max_full_gpu_ctx": good,
                    "status": "full_gpu_at_model_max",
                    "probes": probes,
                }
        else:
            bad = candidate
            break

    if bad is None:
        return {
            "model_max_ctx": model_max_ctx,
            "max_full_gpu_ctx": good,
            "status": "full_gpu_up_to_last_probe",
            "probes": probes,
        }

    lo = good
    hi = bad
    for _ in range(8):
        if hi - lo <= 1024:
            break
        mid = (lo + hi) // 2
        r = probe(model, mid, num_gpu=num_gpu)
        probes.append(r.__dict__)
        if r.full_gpu:
            lo = mid
        else:
            hi = mid

    return {
        "model_max_ctx": model_max_ctx,
        "max_full_gpu_ctx": lo,
        "status": "binary_search_complete",
        "probes": probes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exclude", nargs="*", default=["qwen3.5:4b"])
    parser.add_argument("--models", nargs="*", default=[])
    parser.add_argument("--num-gpu", type=int, default=99)
    parser.add_argument(
        "--out",
        default="/home/daravenrk/dragonlair/book_project/nvidia_ctx_full_gpu_report.json",
    )
    args = parser.parse_args()

    restart_nvidia()

    exclude = set(args.exclude or [])
    models = get_models(exclude)
    if args.models:
        allow = {m.strip() for m in args.models if str(m).strip()}
        models = [m for m in models if m in allow]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "route": "ollama_nvidia",
        "excluded": sorted(exclude),
        "num_gpu": args.num_gpu,
        "models": {},
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for model in models:
        model_max_ctx = get_model_max_ctx(model)
        report["models"][model] = find_max_full_gpu_ctx(
            model,
            model_max_ctx=model_max_ctx,
            num_gpu=args.num_gpu,
        )
        # Checkpoint after each model so partial progress survives interruptions.
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(str(out_path))
    for model, data in report["models"].items():
        print(f"{model}\tmax_full_gpu_ctx={data['max_full_gpu_ctx']}\tstatus={data['status']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
