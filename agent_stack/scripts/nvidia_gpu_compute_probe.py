#!/usr/bin/env python3

"""Direct NVIDIA endpoint probe.

This utility talks to Ollama `/api/generate` directly and intentionally bypasses
the orchestrator runtime preset abstraction. Use it to inspect endpoint-level
GPU compute behavior, not to validate preset-governed book-flow execution.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List


def _sample_nvidia(query_interval_s: float, stop_event: threading.Event, samples: List[Dict[str, Any]]) -> None:
    cmd = [
        "nvidia-smi",
        "--query-gpu=timestamp,utilization.gpu,utilization.memory,power.draw,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    while not stop_event.is_set():
        sampled_at = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=5)
            row = proc.stdout.strip().splitlines()[0]
            parts = [part.strip() for part in row.split(",")]
            if len(parts) >= 6:
                samples.append(
                    {
                        "sampled_at": sampled_at,
                        "timestamp": parts[0],
                        "gpu_util": float(parts[1]),
                        "mem_util": float(parts[2]),
                        "power_w": float(parts[3]),
                        "mem_used_mib": float(parts[4]),
                        "mem_total_mib": float(parts[5]),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            samples.append({"sampled_at": sampled_at, "error": str(exc)})
        stop_event.wait(query_interval_s)


def _run_generate(
    endpoint: str,
    model: str,
    prompt: str,
    num_predict: int,
    timeout: float,
    *,
    num_gpu: int,
    keep_alive: str,
) -> Dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": keep_alive,
        "options": {"num_predict": num_predict, "temperature": 0.0, "num_gpu": num_gpu},
    }
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    ended = time.time()
    data = json.loads(raw) if raw else {}
    data["wall_seconds"] = round(ended - started, 3)
    return data


def _build_prompt() -> str:
    return (
        "Produce a long, detailed technical explanation of GPU scheduling, memory residency, and "
        "kernel execution behavior in local LLM inference systems. Use many paragraphs, explicit "
        "examples, and continue until the token budget is exhausted."
    )


def _build_warm_prompt() -> str:
    return "Reply with exactly: warm-ok"


def _summarize(samples: List[Dict[str, Any]], result: Dict[str, Any]) -> Dict[str, Any]:
    valid = [sample for sample in samples if "gpu_util" in sample]
    summary: Dict[str, Any] = {
        "sample_count": len(valid),
        "request_wall_seconds": result.get("wall_seconds"),
        "response_chars": len(str(result.get("response") or "")),
        "num_gpu_layers": result.get("num_gpu_layers"),
    }
    if not valid:
        summary["has_compute_signal"] = False
        summary["reason"] = "no_valid_gpu_samples"
        return summary
    gpu_utils = [sample["gpu_util"] for sample in valid]
    mem_utils = [sample["mem_util"] for sample in valid]
    powers = [sample["power_w"] for sample in valid]
    mem_used = [sample["mem_used_mib"] for sample in valid]
    summary.update(
        {
            "gpu_util_max": max(gpu_utils),
            "gpu_util_avg": round(statistics.fmean(gpu_utils), 2),
            "gpu_util_nonzero_samples": sum(1 for value in gpu_utils if value > 0),
            "mem_util_max": max(mem_utils),
            "power_w_max": max(powers),
            "mem_used_mib_max": max(mem_used),
            "has_compute_signal": any(value > 0 for value in gpu_utils),
        }
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--num-predict", type=int, default=640)
    parser.add_argument("--warm-num-predict", type=int, default=16)
    parser.add_argument("--query-interval", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--warm-timeout", type=float, default=180.0)
    parser.add_argument("--num-gpu", type=int, default=24)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    warm_error = None
    warm_result: Dict[str, Any] = {}
    try:
        warm_result = _run_generate(
            args.endpoint,
            args.model,
            _build_warm_prompt(),
            args.warm_num_predict,
            args.warm_timeout,
            num_gpu=args.num_gpu,
            keep_alive=args.keep_alive,
        )
    except Exception as exc:  # noqa: BLE001
        warm_error = str(exc)

    samples: List[Dict[str, Any]] = []
    stop_event = threading.Event()
    sampler = threading.Thread(target=_sample_nvidia, args=(args.query_interval, stop_event, samples), daemon=True)
    sampler.start()
    error = None
    result: Dict[str, Any] = {}
    try:
        result = _run_generate(
            args.endpoint,
            args.model,
            _build_prompt(),
            args.num_predict,
            args.timeout,
            num_gpu=args.num_gpu,
            keep_alive=args.keep_alive,
        )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    finally:
        stop_event.set()
        sampler.join(timeout=5)

    payload = {
        "endpoint": args.endpoint,
        "model": args.model,
        "warm_error": warm_error,
        "warm_result": warm_result,
        "error": error,
        "result": result,
        "summary": _summarize(samples, result),
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"model={args.model}")
        print(f"endpoint={args.endpoint}")
        print(f"error={error}")
        for key, value in payload["summary"].items():
            print(f"{key}={value}")
    return 0 if not error else 1


if __name__ == "__main__":
    raise SystemExit(main())