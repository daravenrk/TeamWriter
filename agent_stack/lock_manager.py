import json
import os
import time
from typing import Optional
from contextlib import contextmanager
from dataclasses import dataclass
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock


@dataclass
class EndpointPolicy:
    min_interval_seconds: float = 1.5
    max_inflight: int = 1
    wait_timeout_seconds: Optional[float] = 900.0


class AgentLockManager:
    """
    Process-safe lock/state manager.

    It provides:
    - edit lock: prevents volatile concurrent edits.
    - endpoint gate: rate-limit + in-flight guard per endpoint to prevent spam.
    """

    def __init__(self, lock_root=None):
        self.lock_root = lock_root or os.environ.get("DRAGONLAIR_LOCK_ROOT", "/tmp/dragonlair_agent_stack")
        os.makedirs(self.lock_root, exist_ok=True)
        self.state_path = os.path.join(self.lock_root, "endpoint_state.json")
        self.state_lock_path = os.path.join(self.lock_root, "state.lock")
        raw_allowed = str(
            os.environ.get(
                "AGENT_CHANGELOG_ALLOWED_AGENTS",
                "book-flow-parent,book-publisher,book-continuity,orchestrator",
            )
        )
        self.allowed_log_agents = {item.strip() for item in raw_allowed.split(",") if item.strip()}

    def _endpoint_key(self, endpoint):
        return endpoint.replace("://", "_").replace(":", "_").replace("/", "_")

    def is_lock_active(self, name="agent_edit"):
        lock_path = os.path.join(self.lock_root, f"{name}.lock")
        return os.path.exists(lock_path)

    def _file_lock_path(self, file_path: str) -> str:
        return f"{file_path}.lock"

    @contextmanager
    def file_lock(self, file_path: str, timeout_seconds=20.0, poll_seconds=0.1):
        """Exclusive sidecar lock for a specific file path."""
        lock_path = self._file_lock_path(str(file_path))
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as handle:
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    flock(handle.fileno(), LOCK_EX | LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring file lock for {file_path}")
                    time.sleep(poll_seconds)
            try:
                yield
            finally:
                flock(handle.fileno(), LOCK_UN)

    def get_lock_status(self, name="agent_edit"):
        return {
            "name": name,
            "active": self.is_lock_active(name=name),
            "lock_root": self.lock_root,
        }

    def log_agent_change(self, log_path, agent, action, details):
        """Log an agent action to a shared changes log under a lock."""
        import datetime

        if self.allowed_log_agents and agent not in self.allowed_log_agents:
            return

        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "agent": agent,
            "action": action,
            "details": details,
            "lock": self.get_lock_status(name="changes_log"),
        }

        os.makedirs(os.path.dirname(str(log_path)), exist_ok=True)
        with self.edit_lock(name="changes_log"):
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")

    @contextmanager
    def edit_lock(self, name="agent_edit", timeout_seconds=20.0, poll_seconds=0.1):
        """Exclusive lock for edit operations."""
        lock_path = os.path.join(self.lock_root, f"{name}.lock")
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as handle:
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    flock(handle.fileno(), LOCK_EX | LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out acquiring edit lock: {name}")
                    time.sleep(poll_seconds)
            try:
                yield
            finally:
                flock(handle.fileno(), LOCK_UN)

    def _load_state(self):
        if not os.path.exists(self.state_path):
            return {"endpoints": {}}
        with open(self.state_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _save_state(self, state):
        tmp_path = f"{self.state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(tmp_path, self.state_path)

    def reset_endpoint_state(self, endpoint: Optional[str] = None):
        """
        Reset endpoint gate bookkeeping.

        - endpoint=None: reset all endpoint state
        - endpoint=<url>: reset only that endpoint key
        """
        endpoint_key = self._endpoint_key(endpoint) if endpoint else None
        with open(self.state_lock_path, "a+", encoding="utf-8") as lock_handle:
            flock(lock_handle.fileno(), LOCK_EX)
            try:
                state = self._load_state()
                state.setdefault("endpoints", {})
                if endpoint_key is None:
                    state["endpoints"] = {}
                else:
                    state["endpoints"].pop(endpoint_key, None)
                self._save_state(state)
            finally:
                flock(lock_handle.fileno(), LOCK_UN)

    def get_endpoint_runtime(self, endpoint: Optional[str] = None):
        """
        Read current endpoint runtime bookkeeping.

        - endpoint=None: returns all endpoint state entries
        - endpoint=<url>: returns a single endpoint state entry
        """
        endpoint_key = self._endpoint_key(endpoint) if endpoint else None
        with open(self.state_lock_path, "a+", encoding="utf-8") as lock_handle:
            flock(lock_handle.fileno(), LOCK_EX)
            try:
                state = self._load_state()
                endpoints = state.get("endpoints", {})
                if endpoint_key is None:
                    return dict(endpoints)
                return dict(endpoints.get(endpoint_key, {}))
            finally:
                flock(lock_handle.fileno(), LOCK_UN)

    @contextmanager
    def endpoint_slot(self, endpoint, policy=None, timeout_seconds=30.0, poll_seconds=0.1):
        """
        Reserve a guarded slot for endpoint usage.

        Enforces:
        - max concurrent in-flight requests
        - min interval between request starts
        """
        policy = policy or EndpointPolicy()
        endpoint_key = self._endpoint_key(endpoint)
        effective_timeout = policy.wait_timeout_seconds if policy.wait_timeout_seconds is not None else timeout_seconds

        with open(self.state_lock_path, "a+", encoding="utf-8") as lock_handle:
            deadline = None
            if effective_timeout is not None:
                deadline = time.monotonic() + float(effective_timeout)

            while True:
                flock(lock_handle.fileno(), LOCK_EX)
                try:
                    state = self._load_state()
                    ep_state = state["endpoints"].get(endpoint_key, {"inflight": 0, "last_start": 0.0})
                    now = time.time()
                    elapsed = now - float(ep_state.get("last_start", 0.0))
                    has_capacity = int(ep_state.get("inflight", 0)) < int(policy.max_inflight)
                    interval_ok = elapsed >= float(policy.min_interval_seconds)

                    if has_capacity and interval_ok:
                        ep_state["inflight"] = int(ep_state.get("inflight", 0)) + 1
                        ep_state["last_start"] = now
                        state["endpoints"][endpoint_key] = ep_state
                        self._save_state(state)
                        acquired = True
                    else:
                        acquired = False
                finally:
                    flock(lock_handle.fileno(), LOCK_UN)

                if acquired:
                    break

                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out acquiring endpoint slot for {endpoint}")
                time.sleep(poll_seconds)

            try:
                yield
            finally:
                flock(lock_handle.fileno(), LOCK_EX)
                try:
                    state = self._load_state()
                    ep_state = state["endpoints"].get(endpoint_key, {"inflight": 0, "last_start": 0.0})
                    ep_state["inflight"] = max(0, int(ep_state.get("inflight", 0)) - 1)
                    state["endpoints"][endpoint_key] = ep_state
                    self._save_state(state)
                finally:
                    flock(lock_handle.fileno(), LOCK_UN)
