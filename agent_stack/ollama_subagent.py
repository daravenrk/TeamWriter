# TODO: Subagent Interaction Logging
# - Log all subagent requests, responses, and errors
# - Ensure handoffs and interactions are recorded in changes.log
# agent_stack/ollama_subagent.py

import json
import os
import time
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError

from .lock_manager import AgentLockManager, EndpointPolicy
from .exceptions import (
    OllamaEmptyResponseError,
    OllamaEndpointError,
    OllamaRequestError,
    OllamaResponseDecodeError,
)


class OllamaSubagent:
    """
    Handles requests to the Ollama LLM endpoint.
    """

    def __init__(self, endpoint="http://127.0.0.1:11435", lock_manager=None, policy=None):
        self.endpoint = endpoint.rstrip("/")
        self.lock_manager = lock_manager or AgentLockManager()
        self.policy = policy or EndpointPolicy(min_interval_seconds=1.5, max_inflight=1)
        # Keep per-request socket timeout configurable for slower long-form runs.
        self.http_timeout_seconds = self._resolve_http_timeout_seconds()

    def _resolve_http_timeout_seconds(self):
        candidates = []
        for key in (
            "AGENT_OLLAMA_HTTP_TIMEOUT_SECONDS",
            "AGENT_CALL_TIMEOUT_SECONDS_AMD",
            "AGENT_CALL_TIMEOUT_SECONDS_NVIDIA",
            "AGENT_CALL_TIMEOUT_SECONDS",
        ):
            raw = os.environ.get(key)
            if raw is None:
                continue
            try:
                candidates.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not candidates:
            return 900.0
        return max(candidates)

    def run(
        self,
        prompt,
        model="qwen3.5:27b",
        stream=False,
        system_prompt=None,
        options=None,
        keep_alive=None,
        on_stream=None,
        correlation_id=None,
        ledger_path=None,
    ):
        options_payload = dict(options or {})
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": bool(stream),
        }
        if system_prompt:
            payload["system"] = system_prompt
        if "think" in options_payload:
            payload["think"] = bool(options_payload.pop("think"))
        if options_payload:
            payload["options"] = options_payload
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive

        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.endpoint}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.lock_manager.endpoint_slot(self.endpoint, policy=self.policy):
            call_started_at = time.time()
            done_meta: dict = {}
            try:
                with request.urlopen(req, timeout=self.http_timeout_seconds) as resp:
                    if not stream:
                        raw = resp.read().decode("utf-8")
                    else:
                        assembled = []
                        while True:
                            raw_line = resp.readline()
                            if not raw_line:
                                break
                            line = raw_line.decode("utf-8").strip()
                            if not line:
                                continue
                            try:
                                chunk = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            token = chunk.get("response", "")
                            if token:
                                assembled.append(token)
                                if on_stream:
                                    on_stream(token, chunk)
                            if chunk.get("done"):
                                done_meta = chunk
                                break
                        raw = "".join(assembled)
            except (HTTPError, URLError, TimeoutError) as exc:
                self._write_ledger_entry(
                    ledger_path=ledger_path,
                    correlation_id=correlation_id,
                    model=model,
                    call_started_at=call_started_at,
                    prompt=prompt,
                    success=False,
                    error=str(exc),
                    meta={},
                )
                raise OllamaRequestError(
                    f"Ollama request failed for endpoint {self.endpoint}",
                    details={"endpoint": self.endpoint, "model": model, "error": str(exc)},
                ) from exc

        if stream:
            self._write_ledger_entry(
                ledger_path=ledger_path,
                correlation_id=correlation_id,
                model=model,
                call_started_at=call_started_at,
                prompt=prompt,
                success=True,
                meta={
                    "total_duration_ns": done_meta.get("total_duration"),
                    "eval_count": done_meta.get("eval_count"),
                    "prompt_eval_count": done_meta.get("prompt_eval_count"),
                    "eval_duration_ns": done_meta.get("eval_duration"),
                    "load_duration_ns": done_meta.get("load_duration"),
                },
            )
            return raw
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OllamaResponseDecodeError(
                f"Invalid JSON response from Ollama endpoint {self.endpoint}",
                details={"endpoint": self.endpoint, "model": model, "preview": raw[:400]},
            ) from exc
        error = str(data.get("error") or "").strip()
        if error:
            self._write_ledger_entry(
                ledger_path=ledger_path,
                correlation_id=correlation_id,
                model=model,
                call_started_at=call_started_at,
                prompt=prompt,
                success=False,
                error=error,
                meta={},
            )
            raise OllamaEndpointError(
                f"Ollama endpoint error from {self.endpoint}: {error}",
                details={"endpoint": self.endpoint, "model": model, "error": error},
            )

        response = str(data.get("response", ""))
        if not response.strip():
            payload_preview = raw[:400]
            self._write_ledger_entry(
                ledger_path=ledger_path,
                correlation_id=correlation_id,
                model=model,
                call_started_at=call_started_at,
                prompt=prompt,
                success=False,
                error="empty_response",
                meta={},
            )
            raise OllamaEmptyResponseError(
                f"Empty response from Ollama endpoint {self.endpoint} for model {model}. Payload preview: {payload_preview}",
                details={"endpoint": self.endpoint, "model": model, "preview": payload_preview},
            )

        self._write_ledger_entry(
            ledger_path=ledger_path,
            correlation_id=correlation_id,
            model=model,
            call_started_at=call_started_at,
            prompt=prompt,
            success=True,
            meta={
                "total_duration_ns": data.get("total_duration"),
                "eval_count": data.get("eval_count"),
                "prompt_eval_count": data.get("prompt_eval_count"),
                "eval_duration_ns": data.get("eval_duration"),
                "load_duration_ns": data.get("load_duration"),
            },
        )
        return response

    def _write_ledger_entry(
        self,
        ledger_path,
        correlation_id,
        model,
        call_started_at,
        prompt,
        success,
        meta,
        error=None,
    ):
        """Append one structured entry per Ollama call to the run ledger JSONL."""
        if not ledger_path:
            return
        wall_seconds = time.time() - call_started_at
        total_ns = meta.get("total_duration_ns") if isinstance(meta, dict) else None
        eval_count = meta.get("eval_count") if isinstance(meta, dict) else None
        tokens_per_second = None
        if eval_count and total_ns and total_ns > 0:
            tokens_per_second = round(eval_count / (total_ns / 1e9), 2)
        entry = {
            "correlation_id": correlation_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "endpoint": self.endpoint,
            "model": model,
            "prompt_chars": len(prompt or ""),
            "success": success,
            "error": error,
            "wall_seconds": round(wall_seconds, 3),
            "total_duration_ns": total_ns,
            "eval_count": eval_count,
            "prompt_eval_count": meta.get("prompt_eval_count") if isinstance(meta, dict) else None,
            "eval_duration_ns": meta.get("eval_duration_ns") if isinstance(meta, dict) else None,
            "load_duration_ns": meta.get("load_duration_ns") if isinstance(meta, dict) else None,
            "tokens_per_second": tokens_per_second,
        }
        try:
            p = Path(ledger_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except (OSError, TypeError, ValueError):
            pass
