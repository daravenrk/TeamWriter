# agent_stack/ollama_subagent.py

import json
import os
from urllib import request

from .lock_manager import AgentLockManager, EndpointPolicy


class OllamaSubagent:
    """
    Handles requests to the Ollama LLM endpoint.
    """

    def __init__(self, endpoint="http://127.0.0.1:11435", lock_manager=None, policy=None):
        self.endpoint = endpoint.rstrip("/")
        self.lock_manager = lock_manager or AgentLockManager()
        self.policy = policy or EndpointPolicy(min_interval_seconds=1.5, max_inflight=1)
        # Keep per-request socket timeout configurable for slower long-form runs.
        self.http_timeout_seconds = float(os.environ.get("AGENT_OLLAMA_HTTP_TIMEOUT_SECONDS", "420"))

    def run(
        self,
        prompt,
        model="qwen3.5:27b",
        stream=False,
        system_prompt=None,
        options=None,
        keep_alive=None,
        on_stream=None,
    ):
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": bool(stream),
        }
        if system_prompt:
            payload["system"] = system_prompt
        if options:
            payload["options"] = options
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
                            break
                    raw = "".join(assembled)

        if stream:
            return raw
        data = json.loads(raw)
        return data.get("response", "")
