# TODO: Agent Lifecycle Logging Improvements
# - Add log entries for agent start, return, and error
# - Ensure all agent actions and state changes are logged to changes.log
# - Log agent handoffs and inter-agent communication
# agent_stack/orchestrator.py

import time
import os
import threading
import json
import copy
import uuid
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import nullcontext, contextmanager
from glob import glob
from pathlib import Path

from .lock_manager import AgentLockManager, EndpointPolicy
from .ollama_subagent import OllamaSubagent
from .profile_loader import load_agent_profiles
from .validate_agent_profiles import DEFAULT_MAX_SYSTEM_PROMPT_CHARS, lint_profiles
from .exceptions import (
    AgentHungError,
    AgentProfileError,
    AgentQuarantinedError,
    AgentRouteConfigError,
    AgentStackError,
    AgentUnexpectedError,
)

class OrchestratorAgent:
    def analytics_start_run(self):
        self.analytics_run_start = time.time()
        self.analytics_log.clear()
        self.reset_analytics_counters()

    def analytics_end_run(self):
        self.analytics_run_end = time.time()

    def analytics_log_event(self, event_type, details=None):
        entry = {
            "timestamp": time.time(),
            "event": event_type,
            "details": details or {},
        }
        self.analytics_log.append(entry)

    def analytics_save(self, path):
        analytics = {
            "run_start": self.analytics_run_start,
            "run_end": self.analytics_run_end,
            "duration": (self.analytics_run_end or 0) - (self.analytics_run_start or 0),
            "events": self.analytics_log,
            "unique_agents": list(self.analytics_total_agents_used),
            "total_agents": self.get_total_agents_used(),
            "active_agents": list(self.active_agents),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(analytics, f, indent=2)
    """
    Main entry point for agent stack. Routes tasks to subagents based on input and context.
    Only internal and LLM (Ollama) calls are handled; external tools are not invoked until all internal logic is complete.
    """
    def __init__(self):
        # Analytics/runtime state used across request lifecycle.
        self.analytics_log = []
        self.analytics_run_start = None
        self.analytics_run_end = None
        self.analytics_total_agents_used = set()
        self.active_agents = set()
        self.lock_manager = AgentLockManager()
        self.server_mode = str(os.environ.get("AGENT_SERVER_MODE", "standard")).strip().lower()
        self.strict_mode_validation = str(os.environ.get("AGENT_STRICT_MODE_VALIDATION", "true")).lower() in {"1", "true", "yes", "on"}
        self.profile_dir = str(Path(__file__).parent / "agent_profiles")
        self.max_system_prompt_chars = max(
            1,
            int(os.environ.get("AGENT_MAX_SYSTEM_PROMPT_CHARS", str(DEFAULT_MAX_SYSTEM_PROMPT_CHARS))),
        )
        self._validate_profiles_or_raise()
        self.profiles = load_agent_profiles(self.profile_dir)
        self._profile_stamp = self._compute_profile_stamp()
        amd_endpoint = os.environ.get("OLLAMA_AMD_ENDPOINT", "http://127.0.0.1:11435")
        nvidia_endpoint = os.environ.get("OLLAMA_NVIDIA_ENDPOINT", "http://127.0.0.1:11434")
        self.route_max_inflight = {
            "ollama_amd": max(1, int(os.environ.get("AGENT_ROUTE_MAX_INFLIGHT_AMD", "1"))),
            "ollama_nvidia": max(1, int(os.environ.get("AGENT_ROUTE_MAX_INFLIGHT_NVIDIA", "1"))),
        }
        self.global_max_active = max(1, int(os.environ.get("AGENT_GLOBAL_MAX_ACTIVE", "3")))
        self.global_active_semaphore = threading.BoundedSemaphore(value=self.global_max_active)
        self.endpoint_max_inflight = {
            "ollama_amd": max(1, int(os.environ.get("AGENT_ENDPOINT_MAX_INFLIGHT_AMD", "1"))),
            "ollama_nvidia": max(1, int(os.environ.get("AGENT_ENDPOINT_MAX_INFLIGHT_NVIDIA", "1"))),
        }
        openclaw_mode_enabled = self.server_mode in {"openclaw-client", "openclaw"}
        if openclaw_mode_enabled and self.strict_mode_validation and not self.profiles:
            raise AgentProfileError("No agent profiles loaded for openclaw-client mode")

        self.subagents = {
            "ollama_amd": OllamaSubagent(
                endpoint=amd_endpoint,
                lock_manager=self.lock_manager,
                policy=EndpointPolicy(
                    min_interval_seconds=1.5,
                    max_inflight=self.endpoint_max_inflight["ollama_amd"],
                    wait_timeout_seconds=1800,
                ),
            ),
            "ollama_nvidia": OllamaSubagent(
                endpoint=nvidia_endpoint,
                lock_manager=self.lock_manager,
                policy=EndpointPolicy(
                    min_interval_seconds=1.0,
                    max_inflight=self.endpoint_max_inflight["ollama_nvidia"],
                    wait_timeout_seconds=1800,
                ),
            ),
        }

        self.route_aliases = self._load_route_aliases()
        self.agent_health = {
            name: {
                "state": "idle",
                "last_heartbeat_at": None,
                "last_started_at": None,
                "last_completed_at": None,
                "last_duration_seconds": None,
                "last_error": None,
                "current_profile": None,
                "current_model": None,
                "current_task_excerpt": None,
                "last_system_prompt_excerpt": None,
                "last_response_preview": None,
                "hung_count": 0,
                "failed_count": 0,
                "success_count": 0,
                "quarantined_until": 0.0,
                "last_recovered_at": None,
                "last_recovery_reason": None,
            }
            for name in self.subagents
        }
        self.call_timeout_seconds = float(os.environ.get("AGENT_CALL_TIMEOUT_SECONDS", "180"))
        self.route_call_timeouts = {
            "ollama_amd": float(
                os.environ.get("AGENT_CALL_TIMEOUT_SECONDS_AMD", str(self.call_timeout_seconds))
            ),
            "ollama_nvidia": float(
                os.environ.get("AGENT_CALL_TIMEOUT_SECONDS_NVIDIA", str(self.call_timeout_seconds))
            ),
        }
        default_heartbeat_timeout = max(120.0, max(self.route_call_timeouts.values()) * 1.25)
        self.default_route_models = {
            "ollama_amd": os.environ.get("AGENT_DEFAULT_MODEL_OLLAMA_AMD", "qwen3.5:27b"),
            "ollama_nvidia": os.environ.get("AGENT_DEFAULT_MODEL_OLLAMA_NVIDIA", "qwen3.5:4b"),
        }
        # Dynamic Qwen-only selector for lightweight witty/chat responses.
        self.joke_short_model = os.environ.get("AGENT_JOKE_SHORT_MODEL", "qwen3.5:0.8b")
        self.joke_medium_model = os.environ.get("AGENT_JOKE_MEDIUM_MODEL", "qwen3.5:2b")
        self.joke_long_model = os.environ.get("AGENT_JOKE_LONG_MODEL", "qwen3.5:4b")
        self.joke_short_prompt_chars = max(1, int(os.environ.get("AGENT_JOKE_SHORT_PROMPT_CHARS", "220")))
        self.joke_medium_prompt_chars = max(
            self.joke_short_prompt_chars + 1,
            int(os.environ.get("AGENT_JOKE_MEDIUM_PROMPT_CHARS", "800")),
        )
        self.profile_quality_fallbacks = self._load_profile_quality_fallbacks()
        self.amd_ephemeral_override_models = {
            item.strip()
            for item in str(
                os.environ.get("AGENT_AMD_EPHEMERAL_OVERRIDE_MODELS", "qwen2.5-coder:14b,qwen3.5:14b")
            ).split(",")
            if item.strip()
        }
        self.amd_ephemeral_enabled = str(
            os.environ.get("AGENT_AMD_EPHEMERAL_OVERRIDE_ENABLED", "true")
        ).lower() in {"1", "true", "yes", "on"}
        self.enable_cross_route_fallback = str(
            os.environ.get("AGENT_ENABLE_CROSS_ROUTE_FALLBACK", "false")
        ).lower() in {"1", "true", "yes", "on"}
        self.quarantine_seconds = 60
        self.heartbeat_timeout_seconds = float(
            os.environ.get("AGENT_HEARTBEAT_TIMEOUT_SECONDS", str(default_heartbeat_timeout))
        )
        self.hibernate_enabled = str(os.environ.get("AGENT_ENABLE_HIBERNATION", "true")).lower() in {
            "1", "true", "yes", "on"
        }
        self.hibernate_idle_seconds = max(30.0, float(os.environ.get("AGENT_HIBERNATE_IDLE_SECONDS", "240")))
        self.hibernate_unload_model = str(os.environ.get("AGENT_HIBERNATE_UNLOAD_MODEL", "true")).lower() in {
            "1", "true", "yes", "on"
        }
        self.hibernate_store_path = Path(
            os.environ.get(
                "AGENT_HIBERNATION_STORE_PATH",
                "/home/daravenrk/dragonlair/book_project/agent_hibernation_state.json",
            )
        )
        self._hibernate_store = self._load_hibernate_store()
        self.book_project_root = Path(
            os.environ.get("AGENT_BOOK_PROJECT_ROOT", "/home/daravenrk/dragonlair/book_project")
        )
        framework_root = self.book_project_root / "framework"
        self.book_framework_refs = {
            "framework_skeleton": framework_root / "framework_skeleton.json",
            "arc_tracker": framework_root / "arc_tracker.json",
            "progress_index": framework_root / "progress_index.json",
            "agent_context_status": framework_root / "agent_context_status.jsonl",
        }
        self.route_semaphores = {
            route: threading.BoundedSemaphore(value=limit)
            for route, limit in self.route_max_inflight.items()
        }
        raw_support = str(os.environ.get("AGENT_SUPPORT_PROFILES", "book-publisher,book-continuity,book-canon,orchestrator"))
        self.support_profiles = {item.strip() for item in raw_support.split(",") if item.strip()}
        self.rewards_enabled = str(os.environ.get("AGENT_REWARDS_ENABLED", "true")).lower() in {
            "1", "true", "yes", "on"
        }
        self.agent_reward_start_tokens = max(0, int(os.environ.get("AGENT_REWARD_START_TOKENS", "6")))
        self.agent_reward_max_tokens = max(
            self.agent_reward_start_tokens,
            int(os.environ.get("AGENT_REWARD_MAX_TOKENS", "12")),
        )
        self.agent_reward_success_delta = int(os.environ.get("AGENT_REWARD_SUCCESS_DELTA", "1"))
        self.agent_reward_failure_delta = int(os.environ.get("AGENT_REWARD_FAILURE_DELTA", "-1"))
        self.agent_rewards_path = Path(
            os.environ.get(
                "AGENT_REWARDS_PATH",
                "/home/daravenrk/dragonlair/book_project/agent_reward_ledger.json",
            )
        )
        self.agent_reward_events_path = Path(
            os.environ.get(
                "AGENT_REWARD_EVENTS_PATH",
                "/home/daravenrk/dragonlair/book_project/agent_reward_events.jsonl",
            )
        )
        self.quality_failures_log_path = Path(
            os.environ.get(
                "AGENT_QUALITY_FAILURES_LOG_PATH",
                "/home/daravenrk/dragonlair/book_project/quality_gate_failures.jsonl",
            )
        )
        self.ollama_run_ledger_path = Path(
            os.environ.get(
                "AGENT_OLLAMA_RUN_LEDGER_PATH",
                "/home/daravenrk/dragonlair/book_project/ollama_run_ledger.jsonl",
            )
        )
        self.quarantine_events_path = Path(
            os.environ.get(
                "AGENT_QUARANTINE_EVENTS_PATH",
                "/home/daravenrk/dragonlair/book_project/quarantine_events.jsonl",
            )
        )
        self._reward_lock = threading.Lock()
        self._profile_rewards = self._load_profile_rewards()
        self._ensure_profile_rewards()
        self.profile_scoring_enabled = str(
            os.environ.get("AGENT_PROFILE_SCORING_ENABLED", "true")
        ).lower() in {"1", "true", "yes", "on"}
        self.profile_score_keyword_weight = float(os.environ.get("AGENT_PROFILE_SCORE_KEYWORD_WEIGHT", "3.0"))
        self.profile_score_priority_weight = float(os.environ.get("AGENT_PROFILE_SCORE_PRIORITY_WEIGHT", "0.025"))
        self.profile_score_token_weight = float(os.environ.get("AGENT_PROFILE_SCORE_TOKEN_WEIGHT", "1.0"))
        self.profile_score_failure_penalty = float(os.environ.get("AGENT_PROFILE_SCORE_FAILURE_PENALTY", "0.8"))
        self.profile_score_domain_weight = float(os.environ.get("AGENT_PROFILE_SCORE_DOMAIN_WEIGHT", "1.5"))
        self.profile_score_default_fast_bonus = float(os.environ.get("AGENT_PROFILE_SCORE_FAST_BONUS", "0.2"))
        self.profile_score_failure_window_seconds = max(
            300,
            int(os.environ.get("AGENT_PROFILE_SCORE_FAILURE_WINDOW_SECONDS", "86400")),
        )
        self.profile_score_failure_max_lines = max(
            50,
            int(os.environ.get("AGENT_PROFILE_SCORE_FAILURE_MAX_LINES", "600")),
        )
        self._quality_failure_cache = {
            "mtime": None,
            "size": None,
            "counts": {},
            "loaded_at": 0.0,
        }
        # External subagents (ChatGPU, Copilot) are registered but not called until internal logic is done
        # Example: self.subagents['chatgpu'] = ChatGPUSubagent()
        # Example: self.subagents['copilot'] = CopilotSubagent()

    def _heartbeat(self, agent_name, **fields):
        health = self.agent_health[agent_name]
        health["last_heartbeat_at"] = time.time()
        health["last_active_at"] = health["last_heartbeat_at"]
        for key, value in fields.items():
            health[key] = value
        profile_name = str(health.get("current_profile") or "").strip()
        if profile_name:
            health["profile_tokens"] = self._get_profile_tokens(profile_name)

    def _load_hibernate_store(self):
        try:
            if not self.hibernate_store_path.exists():
                return {}
            raw = self.hibernate_store_path.read_text(encoding="utf-8")
            payload = json.loads(raw or "{}")
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _save_hibernate_store(self):
        try:
            self.hibernate_store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.hibernate_store_path.with_suffix(self.hibernate_store_path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._hibernate_store, indent=2), encoding="utf-8")
            tmp.replace(self.hibernate_store_path)
        except Exception:
            pass

    def _append_jsonl(self, path, payload):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except Exception:
            pass

    def _write_json_atomic(self, path, payload):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass

    def _load_profile_rewards(self):
        try:
            if not self.agent_rewards_path.exists():
                return {}
            payload = json.loads(self.agent_rewards_path.read_text(encoding="utf-8") or "{}")
            profiles = payload.get("profiles") if isinstance(payload, dict) else None
            return profiles if isinstance(profiles, dict) else {}
        except Exception:
            return {}

    def _ensure_profile_rewards(self):
        updated = False
        profile_names = {
            str(profile.get("name") or "").strip()
            for profile in self.profiles
            if str(profile.get("name") or "").strip()
        }
        for name in sorted(profile_names):
            if name not in self._profile_rewards or not isinstance(self._profile_rewards.get(name), dict):
                self._profile_rewards[name] = {
                    "tokens": self.agent_reward_start_tokens,
                    "created_at": time.time(),
                    "updated_at": time.time(),
                }
                updated = True
                continue

            row = self._profile_rewards[name]
            tokens = int(row.get("tokens", self.agent_reward_start_tokens) or self.agent_reward_start_tokens)
            clamped = max(0, min(self.agent_reward_max_tokens, tokens))
            if clamped != tokens:
                row["tokens"] = clamped
                row["updated_at"] = time.time()
                updated = True

        if updated:
            self._persist_profile_rewards()

    def _persist_profile_rewards(self):
        payload = {
            "updated_at": time.time(),
            "start_tokens": self.agent_reward_start_tokens,
            "max_tokens": self.agent_reward_max_tokens,
            "profiles": self._profile_rewards,
        }
        self._write_json_atomic(self.agent_rewards_path, payload)

    def _get_profile_tokens(self, profile_name):
        if not profile_name:
            return None
        row = self._profile_rewards.get(profile_name)
        if not isinstance(row, dict):
            return self.agent_reward_start_tokens
        return int(row.get("tokens", self.agent_reward_start_tokens) or self.agent_reward_start_tokens)

    def _adjust_profile_tokens(self, profile_name, delta, reason, details=None):
        if not self.rewards_enabled or not profile_name:
            return None
        with self._reward_lock:
            self._ensure_profile_rewards()
            row = self._profile_rewards.get(profile_name) or {
                "tokens": self.agent_reward_start_tokens,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            prev_tokens = int(row.get("tokens", self.agent_reward_start_tokens) or self.agent_reward_start_tokens)
            next_tokens = max(0, min(self.agent_reward_max_tokens, prev_tokens + int(delta)))
            row["tokens"] = next_tokens
            row["updated_at"] = time.time()
            self._profile_rewards[profile_name] = row
            self._persist_profile_rewards()

            self._append_jsonl(
                self.agent_reward_events_path,
                {
                    "timestamp": time.time(),
                    "profile": profile_name,
                    "reason": reason,
                    "delta": int(delta),
                    "tokens_before": prev_tokens,
                    "tokens_after": next_tokens,
                    "details": details or {},
                },
            )
            return next_tokens

    def record_quality_gate_failure(self, *, stage, agent, profile, model=None, gate_message=None, run_journal_path=None, attempt=None):
        payload = {
            "timestamp": time.time(),
            "stage": stage,
            "agent": agent,
            "profile": profile,
            "model": model,
            "attempt": attempt,
            "gate_message": str(gate_message or "quality gate failure"),
            "run_journal_path": run_journal_path,
        }
        self._append_jsonl(self.quality_failures_log_path, payload)
        if profile:
            self._adjust_profile_tokens(
                profile,
                self.agent_reward_failure_delta,
                reason="quality_gate_failure",
                details=payload,
            )

    def record_quality_gate_success(self, *, stage, agent, profile, model=None, run_journal_path=None, attempt=None):
        payload = {
            "timestamp": time.time(),
            "stage": stage,
            "agent": agent,
            "profile": profile,
            "model": model,
            "attempt": attempt,
            "run_journal_path": run_journal_path,
        }
        if profile and self.agent_reward_success_delta != 0:
            self._adjust_profile_tokens(
                profile,
                self.agent_reward_success_delta,
                reason="quality_gate_success",
                details=payload,
            )

    def _unload_route_model(self, agent_name, model_name):
        if not model_name:
            return
        agent = self.subagents.get(agent_name)
        endpoint = getattr(agent, "endpoint", None) if agent else None
        if not endpoint:
            return
        try:
            payload = json.dumps(
                {"model": model_name, "prompt": "", "stream": False, "keep_alive": "0m"}
            ).encode("utf-8")
            req = urllib.request.Request(
                endpoint.rstrip("/") + "/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20):
                pass
        except Exception:
            pass

    def _hibernate_agent(self, agent_name, reason="idle-timeout"):
        if not self.hibernate_enabled:
            return False
        health = self.agent_health.get(agent_name)
        if not health:
            return False
        if str(health.get("state") or "") == "running":
            return False
        if str(health.get("state") or "") == "hibernated":
            return False

        checkpoint = {
            "agent": agent_name,
            "saved_at": time.time(),
            "reason": reason,
            "context": {
                "last_started_at": health.get("last_started_at"),
                "last_completed_at": health.get("last_completed_at"),
                "last_duration_seconds": health.get("last_duration_seconds"),
                "last_error": health.get("last_error"),
                "current_profile": health.get("current_profile"),
                "current_model": health.get("current_model"),
                "current_task_excerpt": health.get("current_task_excerpt"),
                "last_system_prompt_excerpt": health.get("last_system_prompt_excerpt"),
                "last_response_preview": health.get("last_response_preview"),
                "hung_count": health.get("hung_count"),
                "failed_count": health.get("failed_count"),
                "success_count": health.get("success_count"),
            },
        }

        self._hibernate_store[agent_name] = checkpoint
        self._save_hibernate_store()

        model_name = str(health.get("current_model") or "")
        if self.hibernate_unload_model and model_name:
            self._unload_route_model(agent_name, model_name)

        health["state"] = "hibernated"
        health["hibernated"] = True
        health["hibernated_at"] = checkpoint["saved_at"]
        health["hibernation_ref"] = str(self.hibernate_store_path)
        health["current_profile"] = None
        health["current_model"] = None
        health["current_task_excerpt"] = None
        health["last_system_prompt_excerpt"] = None
        self._heartbeat(agent_name)
        return True

    def _wake_agent(self, agent_name):
        health = self.agent_health.get(agent_name)
        if not health:
            return False
        if str(health.get("state") or "") != "hibernated":
            return False

        checkpoint = self._hibernate_store.get(agent_name) or {}
        saved_ctx = checkpoint.get("context") or {}
        health["state"] = "idle"
        health["hibernated"] = False
        health["hibernated_at"] = None
        health["hibernation_ref"] = str(self.hibernate_store_path)
        if saved_ctx.get("current_profile"):
            health["current_profile"] = saved_ctx.get("current_profile")
        if saved_ctx.get("current_model"):
            health["current_model"] = saved_ctx.get("current_model")
        if saved_ctx.get("current_task_excerpt"):
            health["current_task_excerpt"] = saved_ctx.get("current_task_excerpt")
        if saved_ctx.get("last_system_prompt_excerpt"):
            health["last_system_prompt_excerpt"] = saved_ctx.get("last_system_prompt_excerpt")
        if saved_ctx.get("last_response_preview"):
            health["last_response_preview"] = saved_ctx.get("last_response_preview")
        self._heartbeat(agent_name)
        return True

    def scan_idle_agents_for_hibernation(self):
        if not self.hibernate_enabled:
            return {"count": 0, "agents": {}, "enabled": False}

        now = time.time()
        hibernated = {}
        with self.lock_manager.edit_lock(name="orchestrator_hibernation_scan"):
            for agent_name, health in self.agent_health.items():
                state = str(health.get("state") or "idle")
                if state in {"running", "hibernated", "hung", "failed"}:
                    continue
                if self.is_route_call_active(agent_name):
                    continue

                last_ref = max(
                    float(health.get("last_active_at") or 0.0),
                    float(health.get("last_completed_at") or 0.0),
                    float(health.get("last_started_at") or 0.0),
                    float(health.get("last_heartbeat_at") or 0.0),
                )
                if last_ref <= 0:
                    continue
                idle_for = now - last_ref
                if idle_for < self.hibernate_idle_seconds:
                    continue

                if self._hibernate_agent(agent_name, reason="idle-timeout"):
                    hibernated[agent_name] = {
                        "idle_seconds": idle_for,
                        "hibernated_at": self.agent_health[agent_name].get("hibernated_at"),
                    }

            if hibernated:
                self.lock_manager.reset_endpoint_state()

        return {
            "count": len(hibernated),
            "agents": hibernated,
            "enabled": True,
            "idle_seconds_threshold": self.hibernate_idle_seconds,
        }

    def _compute_profile_stamp(self):
        parts = []
        for path in sorted(glob(str(Path(self.profile_dir) / "*.agent.md"))):
            try:
                stat = Path(path).stat()
                parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
            except OSError:
                continue
        return "|".join(parts)

    def _load_route_aliases(self):
        aliases = {}
        raw = str(os.environ.get("AGENT_ROUTE_ALIASES", "")).strip()
        if not raw:
            return aliases
        for pair in raw.split(","):
            chunk = pair.strip()
            if not chunk or "=" not in chunk:
                continue
            source, target = chunk.split("=", 1)
            source = source.strip()
            target = target.strip()
            if self.strict_mode_validation and source and source not in {"ollama_amd", "ollama_nvidia"}:
                raise AgentRouteConfigError(f"AGENT_ROUTE_ALIASES has unsupported source route: {source}")
            if self.strict_mode_validation and target and target not in {"ollama_amd", "ollama_nvidia"}:
                raise AgentRouteConfigError(f"AGENT_ROUTE_ALIASES has unsupported target route: {target}")
            if source and target:
                aliases[source] = target
        return aliases

    def _resolve_route(self, route):
        resolved = self.route_aliases.get(route, route)
        if resolved not in self.subagents:
            return route
        return resolved

    def _reload_profiles_if_changed(self):
        current = self._compute_profile_stamp()
        if current == self._profile_stamp:
            return
        self._validate_profiles_or_raise()
        self.profiles = load_agent_profiles(self.profile_dir)
        self._profile_stamp = current
        with self._reward_lock:
            self._ensure_profile_rewards()

    def _validate_profiles_or_raise(self):
        report = lint_profiles(
            profile_dir=self.profile_dir,
            max_system_prompt_chars=self.max_system_prompt_chars,
        )
        if report.get("valid"):
            return

        invalid_profiles = [profile for profile in report.get("profiles", []) if profile.get("errors")]
        summary_bits = []
        for profile in invalid_profiles[:3]:
            label = profile.get("name") or Path(profile.get("path") or "unknown").name
            summary_bits.append(f"{label}: {profile['errors'][0]}")
        summary = "; ".join(summary_bits) or "profile lint failed"
        raise AgentProfileError(
            f"Profile validation failed with {report.get('error_count', 0)} errors: {summary}"
        )

    def _pick_profile(self, user_input):
        if self.profile_scoring_enabled:
            scored = self._pick_profile_by_score(user_input)
            if scored:
                return scored

        lowered = user_input.lower()
        for profile in self.profiles:
            for keyword in profile.get("intent_keywords", []):
                if keyword in lowered:
                    return profile
        # If no intent matches, use nvidia-fast as the default for auto mode
        for profile in self.profiles:
            if profile.get("name") == "nvidia-fast":
                return profile
        return self.profiles[0] if self.profiles else None

    def _pick_profile_by_score(self, user_input):
        if not self.profiles:
            return None

        lowered = str(user_input or "").lower()
        failure_counts = self._load_recent_quality_failure_counts()
        scored = []
        for profile in self.profiles:
            score, details = self._score_profile_for_input(profile, lowered, failure_counts)
            scored.append((score, int(profile.get("priority", 0) or 0), str(profile.get("name") or ""), profile, details))

        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        winner = scored[0][3]

        top_rows = [
            {
                "name": row[2],
                "score": round(float(row[0]), 4),
                "priority": row[1],
                "details": row[4],
            }
            for row in scored[:3]
        ]
        self.analytics_log_event("profile_score_selection", {"input_preview": lowered[:180], "top": top_rows})
        return winner

    def _score_profile_for_input(self, profile, lowered_input, failure_counts):
        name = str(profile.get("name") or "").strip()
        priority = int(profile.get("priority", 0) or 0)
        keywords = [str(k).strip().lower() for k in (profile.get("intent_keywords") or []) if str(k).strip()]
        keyword_matches = sum(1 for kw in keywords if kw in lowered_input)
        score = float(keyword_matches) * self.profile_score_keyword_weight

        score += float(priority) * self.profile_score_priority_weight

        tokens = self._get_profile_tokens(name)
        if tokens is not None and self.agent_reward_max_tokens > 0:
            normalized_tokens = max(0.0, min(1.0, float(tokens) / float(self.agent_reward_max_tokens)))
            score += normalized_tokens * self.profile_score_token_weight
        else:
            normalized_tokens = None

        failure_count = int(failure_counts.get(name, 0) or 0)
        score -= float(failure_count) * self.profile_score_failure_penalty

        has_book_term = any(
            term in lowered_input
            for term in ["book", "chapter", "scene", "novel", "story", "manuscript", "canon", "arc", "outline"]
        )
        has_code_term = any(
            term in lowered_input
            for term in ["code", "python", "bash", "debug", "refactor", "test", "api", "function", "bug"]
        )
        is_book_profile = name.startswith("book-")

        if has_book_term:
            score += self.profile_score_domain_weight if is_book_profile else (-0.5 * self.profile_score_domain_weight)
        if has_code_term and not is_book_profile:
            score += 0.5 * self.profile_score_domain_weight

        if name == "nvidia-fast":
            score += self.profile_score_default_fast_bonus

        details = {
            "keyword_matches": keyword_matches,
            "priority": priority,
            "normalized_tokens": normalized_tokens,
            "recent_failures": failure_count,
            "book_domain": bool(has_book_term and is_book_profile),
            "code_domain": bool(has_code_term and not is_book_profile),
        }
        return score, details

    def _load_recent_quality_failure_counts(self):
        path = self.quality_failures_log_path
        try:
            stat = path.stat()
        except Exception:
            return {}

        if (
            self._quality_failure_cache.get("mtime") == stat.st_mtime_ns
            and self._quality_failure_cache.get("size") == stat.st_size
        ):
            return dict(self._quality_failure_cache.get("counts") or {})

        now = time.time()
        cutoff = now - float(self.profile_score_failure_window_seconds)
        lines = deque(maxlen=self.profile_score_failure_max_lines)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for raw in handle:
                    lines.append(raw)
        except Exception:
            return {}

        counts = {}
        for raw in lines:
            row = raw.strip()
            if not row:
                continue
            try:
                payload = json.loads(row)
            except Exception:
                continue
            timestamp = float(payload.get("timestamp") or 0.0)
            if timestamp and timestamp < cutoff:
                continue
            profile_name = str(payload.get("profile") or "").strip()
            if not profile_name:
                continue
            counts[profile_name] = int(counts.get(profile_name, 0)) + 1

        self._quality_failure_cache = {
            "mtime": stat.st_mtime_ns,
            "size": stat.st_size,
            "counts": counts,
            "loaded_at": now,
        }
        return dict(counts)

    def _pick_profile_by_name(self, profile_name):
        for profile in self.profiles:
            if profile.get("name") == profile_name:
                return profile
        return None

    def _resolve_dynamic_model(self, profile, user_input, current_model):
        if not profile:
            return current_model

        profile_name = str(profile.get("name") or "")
        if profile_name != "joke-it-guy":
            return current_model

        prompt_len = len((user_input or "").strip())
        if prompt_len <= self.joke_short_prompt_chars:
            return self.joke_short_model
        if prompt_len <= self.joke_medium_prompt_chars:
            return self.joke_medium_model
        return self.joke_long_model

    def _get_profile_timeout_seconds(self, profile, route_name):
        if profile and profile.get("timeout_seconds") is not None:
            return float(profile["timeout_seconds"])
        return float(self.route_call_timeouts.get(route_name, self.call_timeout_seconds))

    def _get_profile_retry_limit(self, profile):
        if not profile:
            return 0
        try:
            return max(0, int(profile.get("retry_limit", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _enforce_profile_policy(self, profile, route_name, model_name):
        if not profile:
            return

        profile_name = str(profile.get("name") or "default")
        allowed_routes = [str(route).strip() for route in (profile.get("allowed_routes") or []) if str(route).strip()]
        if allowed_routes and route_name not in allowed_routes:
            raise AgentProfileError(
                f"Profile {profile_name} does not allow route {route_name}",
                details={
                    "profile": profile_name,
                    "route": route_name,
                    "allowed_routes": allowed_routes,
                },
            )

        model_allowlist = [str(item).strip() for item in (profile.get("model_allowlist") or []) if str(item).strip()]
        if model_allowlist and str(model_name or "").strip() not in model_allowlist:
            raise AgentProfileError(
                f"Profile {profile_name} does not allow model {model_name}",
                details={
                    "profile": profile_name,
                    "model": model_name,
                    "model_allowlist": model_allowlist,
                },
            )

    def _is_retryable_agent_error(self, err):
        if isinstance(err, (AgentProfileError, AgentRouteConfigError, AgentQuarantinedError)):
            return False
        return isinstance(err, AgentStackError)

    def _load_profile_quality_fallbacks(self):
        raw = os.environ.get(
            "AGENT_PROFILE_QUALITY_FALLBACKS",
            json.dumps(
                {
                    "book-proofreader": {
                        "fallback_route": "ollama_nvidia",
                        "fallback_model": "qwen3.5:4b",
                        "min_chars": 250,
                        "min_ratio": 0.3,
                        "suspicious_phrases": [
                            "please provide",
                            "share the text",
                            "need the text",
                            "i need the chapter",
                            "as an ai",
                        ],
                    },
                    "book-copy-editor": {
                        "fallback_route": "ollama_amd",
                        "fallback_model": "qwen3.5:9b",
                        "min_chars": 300,
                        "min_ratio": 0.33,
                        "suspicious_phrases": [
                            "please provide",
                            "share the text",
                            "need the text",
                            "as an ai",
                        ],
                    },
                    "book-line-editor": {
                        "fallback_route": "ollama_amd",
                        "fallback_model": "qwen3.5:9b",
                        "min_chars": 350,
                        "min_ratio": 0.33,
                        "suspicious_phrases": [
                            "please provide",
                            "share the text",
                            "need the text",
                            "as an ai",
                        ],
                    },
                    "book-publisher": {
                        "fallback_route": "ollama_amd",
                        "fallback_model": "qwen3.5:27b",
                        "min_chars": 180,
                        "required_terms": ["APPROVE", "REVISE"],
                        "suspicious_phrases": [
                            "please provide",
                            "share the text",
                            "need the text",
                            "as an ai",
                        ],
                    },
                }
            ),
        )
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _is_quality_retry_candidate(self, profile_name, model, result, user_input):
        if not profile_name or not isinstance(result, str):
            return None

        policy = self.profile_quality_fallbacks.get(profile_name)
        if not isinstance(policy, dict):
            return None

        fallback_model = str(policy.get("fallback_model") or "").strip()
        fallback_route = self._resolve_route(str(policy.get("fallback_route") or "").strip())
        if not fallback_model or fallback_model == str(model or "").strip():
            return None
        if fallback_route and fallback_route not in self.subagents:
            return None

        response_text = result.strip()
        lowered = response_text.lower()
        prompt_len = len((user_input or "").strip())
        min_chars = max(1, int(policy.get("min_chars", 0) or 0))
        min_ratio = float(policy.get("min_ratio", 0.0) or 0.0)
        min_expected = min_chars
        if min_ratio > 0 and prompt_len > 0:
            min_expected = max(min_expected, int(prompt_len * min_ratio))

        if min_expected > 0 and len(response_text) < min_expected:
            return {
                "reason": f"response_too_short<{min_expected}",
                "fallback_route": fallback_route or None,
                "fallback_model": fallback_model,
            }

        for phrase in policy.get("suspicious_phrases", []) or []:
            phrase_text = str(phrase or "").strip().lower()
            if phrase_text and phrase_text in lowered:
                return {
                    "reason": f"suspicious_phrase:{phrase_text}",
                    "fallback_route": fallback_route or None,
                    "fallback_model": fallback_model,
                }

        required_terms = [str(term or "").strip() for term in (policy.get("required_terms") or []) if str(term or "").strip()]
        if required_terms and not any(term in response_text for term in required_terms):
            return {
                "reason": "missing_required_terms",
                "fallback_route": fallback_route or None,
                "fallback_model": fallback_model,
            }

        return None

    def _log_quality_retry(self, profile_name, initial_route, initial_model, fallback_route, fallback_model, reason):
        self.analytics_log_event(
            "agent_quality_retry",
            {
                "profile": profile_name,
                "initial_route": initial_route,
                "initial_model": initial_model,
                "fallback_route": fallback_route,
                "fallback_model": fallback_model,
                "reason": reason,
            },
        )
        if hasattr(self, "diagnostics_path") and self.diagnostics_path:
            try:
                with open(self.diagnostics_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "event": "orchestrator_agent_quality_retry",
                        "profile": profile_name,
                        "initial_route": initial_route,
                        "initial_model": initial_model,
                        "fallback_route": fallback_route,
                        "fallback_model": fallback_model,
                        "reason": reason,
                        "timestamp": time.time(),
                    }) + "\n")
            except Exception:
                pass

    def _build_system_prompt(self, profile):
        if not profile:
            return None
        sections = profile.get("sections", {})
        blocks = [f"Agent Profile: {profile.get('name', 'default')}"]

        ordered_sections = [
            ("purpose", "Purpose"),
            ("system_behavior", "System Behavior"),
            ("actions", "Actions"),
        ]
        rendered_keys = set()

        for key, title in ordered_sections:
            content = str(sections.get(key, "") or "").strip()
            if not content:
                continue
            rendered_keys.add(key)
            blocks.extend(["", f"{title}:", content])

        for key, raw_value in sections.items():
            if key in rendered_keys or key == "content":
                continue
            content = str(raw_value or "").strip()
            if not content:
                continue
            blocks.extend(["", f"{self._profile_section_title(key)}:", content])

        blocks.extend([
            "",
            "Global Operating Contract:",
            self._build_global_agent_contract(profile),
        ])
        rendered = "\n".join(blocks).strip()
        if rendered and len(rendered) > self.max_system_prompt_chars:
            raise AgentProfileError(
                f"Profile {profile.get('name', 'default')} system prompt exceeds "
                f"AGENT_MAX_SYSTEM_PROMPT_CHARS ({len(rendered)} > {self.max_system_prompt_chars})"
            )
        return rendered or None

    def _profile_section_title(self, key):
        parts = [p for p in str(key).split("_") if p]
        if not parts:
            return "Section"
        return " ".join(part.capitalize() for part in parts)

    def _build_global_agent_contract(self, profile):
        profile_name = str((profile or {}).get("name") or "").strip()
        framework_skeleton = str(self.book_framework_refs["framework_skeleton"])
        arc_tracker = str(self.book_framework_refs["arc_tracker"])
        progress_index = str(self.book_framework_refs["progress_index"])
        agent_context_status = str(self.book_framework_refs["agent_context_status"])

        lines = [
            "- Complete the assigned task with concrete outputs and avoid returning placeholders.",
            "- Keep responses aligned with established canon, timeline, and unresolved narrative loops.",
            "- If the request is book-related, treat continuity and forward story progress as hard requirements.",
            "- For book-related tasks, use and preserve the companion framework files:",
            f"  - framework_skeleton_json: {framework_skeleton}",
            f"  - arc_tracker_json: {arc_tracker}",
            f"  - progress_index_json: {progress_index}",
            f"  - agent_context_status_jsonl: {agent_context_status}",
            "- Ensure chapter and section outputs reinforce an early, explicit book skeleton (acts, arcs, milestones).",
            "- Track expected next steps for downstream agents so handoffs are clear and actionable.",
            "- When uncertain, state assumptions briefly and proceed with the most continuity-safe interpretation.",
        ]

        if profile_name.startswith("book-"):
            lines.extend(
                [
                    "- This profile is a book specialist: prioritize structural integrity, arc momentum, and chapter-to-chapter linkage.",
                    "- Ensure your output can be consumed immediately by the next stage without missing schema fields.",
                ]
            )

        return "\n".join(lines)

    def _is_available(self, agent_name):
        health = self.agent_health[agent_name]
        return time.time() >= float(health.get("quarantined_until", 0.0))

    def _log_quarantine_event(self, event_type, agent_name, health=None, extra=None):
        health = health or self.agent_health.get(agent_name, {})
        payload = {
            "timestamp": time.time(),
            "event": event_type,
            "agent": agent_name,
            "route": agent_name,
            "state": str((health or {}).get("state") or "idle"),
            "profile": (health or {}).get("current_profile"),
            "model": (health or {}).get("current_model"),
            "task_excerpt": (health or {}).get("current_task_excerpt"),
            "last_error": (health or {}).get("last_error"),
            "quarantined_until": float((health or {}).get("quarantined_until") or 0.0),
        }
        if extra:
            payload.update(extra)
        self._append_jsonl(self.quarantine_events_path, payload)

    def _quarantine_status(self, health, now=None):
        now = time.time() if now is None else float(now)
        quarantined_until = float((health or {}).get("quarantined_until") or 0.0)
        is_active = quarantined_until > now
        return {
            "active": is_active,
            "until": quarantined_until,
            "remaining_seconds": max(0.0, quarantined_until - now) if is_active else 0.0,
        }

    def _auto_recover_expired_quarantines(self, now=None):
        now = time.time() if now is None else float(now)
        recovered = {}
        for agent_name, health in self.agent_health.items():
            state = str(health.get("state") or "idle")
            quarantined_until = float(health.get("quarantined_until") or 0.0)
            if quarantined_until <= 0 or quarantined_until > now:
                continue
            if state == "running":
                continue
            if state not in {"hung", "failed", "quarantined"}:
                continue

            recovered[agent_name] = {
                "previous_state": state,
                "previous_quarantined_until": quarantined_until,
                "previous_error": health.get("last_error"),
            }
            self._heartbeat(
                agent_name,
                state="idle",
                quarantined_until=0.0,
                last_recovered_at=now,
                last_recovery_reason="quarantine-expired-auto-recover",
            )
            self._log_quarantine_event(
                "auto_recover",
                agent_name,
                health=health,
                extra={
                    "recovery_reason": "quarantine-expired-auto-recover",
                    "previous_state": state,
                    "previous_quarantined_until": quarantined_until,
                },
            )
        return recovered

    def _triage_mark_hung(self, agent_name, started_at):
        health = self.agent_health[agent_name]
        health["state"] = "hung"
        health["hung_count"] = int(health.get("hung_count", 0)) + 1
        health["last_error"] = "timeout/hung"
        health["last_duration_seconds"] = time.time() - started_at
        health["quarantined_until"] = time.time() + self.quarantine_seconds
        self._heartbeat(agent_name)
        self._log_quarantine_event(
            "quarantine_marked_hung",
            agent_name,
            health=health,
            extra={"started_at": started_at, "duration_seconds": health.get("last_duration_seconds")},
        )

    def _triage_mark_failed(self, agent_name, err, started_at):
        health = self.agent_health[agent_name]
        health["state"] = "failed"
        health["failed_count"] = int(health.get("failed_count", 0)) + 1
        health["last_error"] = str(err)
        health["last_duration_seconds"] = time.time() - started_at
        health["quarantined_until"] = time.time() + self.quarantine_seconds
        self._heartbeat(agent_name)
        self._log_quarantine_event(
            "quarantine_marked_failed",
            agent_name,
            health=health,
            extra={"started_at": started_at, "duration_seconds": health.get("last_duration_seconds")},
        )

    def _triage_mark_success(self, agent_name, started_at):
        health = self.agent_health[agent_name]
        health["state"] = "healthy"
        health["last_completed_at"] = time.time()
        health["last_duration_seconds"] = time.time() - started_at
        health["last_error"] = None
        health["last_recovery_reason"] = None
        health["success_count"] = int(health.get("success_count", 0)) + 1
        self._heartbeat(agent_name)

    @contextmanager
    def _route_gate(self, agent_name):
        semaphore = self.route_semaphores.get(agent_name)
        if semaphore is None:
            yield
            return
        semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()

    @contextmanager
    def _global_active_gate(self):
        self.global_active_semaphore.acquire()
        try:
            yield
        finally:
            self.global_active_semaphore.release()

    def _invoke_with_triage(
        self,
        agent_name,
        user_input,
        model=None,
        stream=False,
        system_prompt=None,
        options=None,
        keep_alive=None,
        on_stream=None,
        profile_name=None,
        correlation_id=None,
        timeout_override=None,
    ):
        self._wake_agent(agent_name)
        if not self._is_available(agent_name):
            raise AgentQuarantinedError(
                f"Agent {agent_name} is quarantined",
                details={"agent": agent_name, "quarantined_until": self.agent_health.get(agent_name, {}).get("quarantined_until")},
            )

        call_correlation_id = correlation_id or str(uuid.uuid4())
        health = self.agent_health[agent_name]
        health["state"] = "running"
        started_at = time.time()
        health["last_started_at"] = started_at
        self.active_agents.add(agent_name)
        self.analytics_total_agents_used.add(agent_name)
        self._heartbeat(
            agent_name,
            state="running",
            current_profile=profile_name,
            current_model=model,
            current_task_excerpt=(user_input or "")[:240],
            last_system_prompt_excerpt=(system_prompt or "")[:400],
        )

        stream_preview = []
        request_options = dict(options or {})
        request_keep_alive = keep_alive

        # AMD override policy: allow temporary alternate large-context model calls,
        # then unload that model so default route model is preferred on next request.
        if (
            self.amd_ephemeral_enabled
            and agent_name == "ollama_amd"
            and model
            and model in self.amd_ephemeral_override_models
            and model != self.default_route_models.get("ollama_amd")
        ):
            request_keep_alive = "0m"

        def wrapped_stream_callback(token, chunk):
            if token:
                stream_preview.append(token)
                preview = "".join(stream_preview)[-400:]
                self._heartbeat(agent_name, last_response_preview=preview)
            if on_stream:
                on_stream(token, chunk)

        is_support_profile = bool(profile_name and profile_name in self.support_profiles)
        gate = nullcontext() if is_support_profile else self._route_gate(agent_name)
        global_gate = nullcontext() if is_support_profile else self._global_active_gate()
        call_timeout_seconds = float(
            timeout_override
            if timeout_override is not None
            else self.route_call_timeouts.get(agent_name, self.call_timeout_seconds)
        )
        self.analytics_log_event("agent_start", {"agent": agent_name, "profile": profile_name, "prompt": user_input, "model": model})
        # Diagnostics: log agent invocation and parameters
        if hasattr(self, "diagnostics_path") and self.diagnostics_path:
            try:
                with open(self.diagnostics_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "event": "orchestrator_agent_invoke",
                        "agent": agent_name,
                        "profile": profile_name,
                        "prompt": user_input,
                        "model": model,
                        "timestamp": time.time(),
                    }) + "\n")
            except Exception:
                pass
        try:
            with global_gate:
                with gate:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(
                            self.subagents[agent_name].run,
                            user_input,
                            model=model,
                            stream=stream,
                            system_prompt=system_prompt,
                            options=request_options,
                            keep_alive=request_keep_alive,
                            on_stream=wrapped_stream_callback,
                            correlation_id=call_correlation_id,
                            ledger_path=str(self.ollama_run_ledger_path),
                        )
                        try:
                            result = future.result(timeout=call_timeout_seconds)
                            self.analytics_log_event("agent_success", {"agent": agent_name, "result": str(result)[:400]})
                            if hasattr(self, "diagnostics_path") and self.diagnostics_path:
                                with open(self.diagnostics_path, "a", encoding="utf-8") as f:
                                    f.write(json.dumps({
                                        "event": "orchestrator_agent_success",
                                        "agent": agent_name,
                                        "profile": profile_name,
                                        "result": str(result)[:400],
                                        "timestamp": time.time(),
                                    }) + "\n")
                        except FutureTimeoutError:
                            self._triage_mark_hung(agent_name, started_at)
                            self.analytics_log_event("agent_hung", {"agent": agent_name})
                            if hasattr(self, "diagnostics_path") and self.diagnostics_path:
                                with open(self.diagnostics_path, "a", encoding="utf-8") as f:
                                    f.write(json.dumps({
                                        "event": "orchestrator_agent_hung",
                                        "agent": agent_name,
                                        "profile": profile_name,
                                        "timestamp": time.time(),
                                    }) + "\n")
                            raise AgentHungError(
                                f"Agent {agent_name} hung and was quarantined",
                                details={"agent": agent_name, "timeout_seconds": call_timeout_seconds},
                            )
                        except AgentStackError as err:
                            self._triage_mark_failed(agent_name, err, started_at)
                            self.analytics_log_event(
                                "agent_error",
                                {
                                    "agent": agent_name,
                                    "error": str(err),
                                    "error_code": err.code,
                                },
                            )
                            if hasattr(self, "diagnostics_path") and self.diagnostics_path:
                                with open(self.diagnostics_path, "a", encoding="utf-8") as f:
                                    f.write(json.dumps({
                                        "event": "orchestrator_agent_error",
                                        "agent": agent_name,
                                        "profile": profile_name,
                                        "error": str(err),
                                        "error_code": err.code,
                                        "timestamp": time.time(),
                                    }) + "\n")
                            raise
                        except Exception as err:
                            wrapped = AgentUnexpectedError(
                                f"Unexpected agent execution error for {agent_name}: {err}",
                                details={"agent": agent_name},
                            )
                            self._triage_mark_failed(agent_name, wrapped, started_at)
                            self.analytics_log_event(
                                "agent_error",
                                {
                                    "agent": agent_name,
                                    "error": str(wrapped),
                                    "error_code": wrapped.code,
                                },
                            )
                            if hasattr(self, "diagnostics_path") and self.diagnostics_path:
                                with open(self.diagnostics_path, "a", encoding="utf-8") as f:
                                    f.write(json.dumps({
                                        "event": "orchestrator_agent_error",
                                        "agent": agent_name,
                                        "profile": profile_name,
                                        "error": str(wrapped),
                                        "error_code": wrapped.code,
                                        "timestamp": time.time(),
                                    }) + "\n")
                            raise wrapped

            self._triage_mark_success(agent_name, started_at)
            if isinstance(result, str):
                self._heartbeat(agent_name, last_response_preview=result[:400])
            return result
        finally:
            # Remove from active agents set
            self.active_agents.discard(agent_name)
            # Optionally, reset endpoint state after every run for robustness
            self.lock_manager.reset_endpoint_state()
            self.analytics_log_event("agent_end", {"agent": agent_name})
    def get_active_agent_count(self):
        """Return the number of currently active agents."""
        return len(self.active_agents)

    def get_total_agents_used(self):
        """Return the number of unique agents used in this run (analytics)."""
        return len(self.analytics_total_agents_used)

    def reset_analytics_counters(self):
        """Reset analytics counters for a new run."""
        self.analytics_total_agents_used.clear()
        self.active_agents.clear()

    def _fallback_agent(self, preferred):
        candidates = [name for name in self.subagents if name != preferred and self._is_available(name)]
        return candidates[0] if candidates else None

    def get_agent_health_report(self):
        self.scan_idle_agents_for_hibernation()
        now = time.time()
        self._auto_recover_expired_quarantines(now=now)
        global_max_active = int(getattr(self, "global_max_active", 1) or 1)
        rewards = copy.deepcopy(self._profile_rewards)
        depleted_profiles = sorted(
            name
            for name, row in rewards.items()
            if isinstance(row, dict) and int(row.get("tokens", self.agent_reward_start_tokens) or 0) <= 0
        )
        health_agents = copy.deepcopy(self.agent_health)
        for info in health_agents.values():
            profile_name = str((info or {}).get("current_profile") or "").strip()
            token_balance = self._get_profile_tokens(profile_name) if profile_name else None
            info["profile_tokens"] = token_balance
            info["token_depleted"] = bool(token_balance is not None and int(token_balance) <= 0)
            quarantine = self._quarantine_status(info, now=now)
            info["quarantine_active"] = quarantine["active"]
            info["quarantine_remaining_seconds"] = quarantine["remaining_seconds"]
            info["display_state"] = "quarantined" if quarantine["active"] else str((info or {}).get("state") or "idle")
            if quarantine["active"]:
                info["status_detail"] = f"auto-recovers in {int(quarantine['remaining_seconds'])}s"
            elif info.get("last_recovery_reason") == "quarantine-expired-auto-recover":
                info["status_detail"] = "auto-recovered after quarantine expiry"
            elif info.get("last_error"):
                info["status_detail"] = str(info.get("last_error"))
        return {
            "server_mode": self.server_mode,
            "timeout_seconds": self.call_timeout_seconds,
            "max_system_prompt_chars": self.max_system_prompt_chars,
            "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
            "route_timeout_seconds": self.route_call_timeouts,
            "route_max_inflight": self.route_max_inflight,
            "endpoint_max_inflight": self.endpoint_max_inflight,
            "global_max_active": global_max_active,
            "logging_runtime": self.lock_manager.get_logging_runtime(),
            "enable_cross_route_fallback": self.enable_cross_route_fallback,
            "quarantine_seconds": self.quarantine_seconds,
            "hibernation": {
                "enabled": self.hibernate_enabled,
                "idle_seconds": self.hibernate_idle_seconds,
                "store_path": str(self.hibernate_store_path),
                "unload_model": self.hibernate_unload_model,
            },
            "rewards": {
                "enabled": self.rewards_enabled,
                "start_tokens": self.agent_reward_start_tokens,
                "max_tokens": self.agent_reward_max_tokens,
                "success_delta": self.agent_reward_success_delta,
                "failure_delta": self.agent_reward_failure_delta,
                "depleted_profiles": depleted_profiles,
                "profiles": rewards,
                "quality_failures_log": str(self.quality_failures_log_path),
                "reward_ledger": str(self.agent_rewards_path),
            },
            "analytics": {
                "ollama_run_ledger": str(self.ollama_run_ledger_path),
                "quarantine_events": str(self.quarantine_events_path),
            },
            "agents": health_agents,
        }

    def scan_unresponsive_agents(self):
        """Mark long-running agents without heartbeat updates as hung and recover endpoint state."""
        now = time.time()
        stale = {}
        with self.lock_manager.edit_lock(name="orchestrator_watchdog"):
            for agent_name, health in self.agent_health.items():
                if str(health.get("state") or "") != "running":
                    continue

                started_at = float(health.get("last_started_at") or 0.0)
                last_heartbeat_at = float(health.get("last_heartbeat_at") or 0.0)
                ref = max(started_at, last_heartbeat_at)
                if ref <= 0:
                    continue
                elapsed = now - ref
                if elapsed <= self.heartbeat_timeout_seconds:
                    continue

                health["state"] = "hung"
                health["hung_count"] = int(health.get("hung_count", 0)) + 1
                health["last_error"] = "heartbeat-timeout/unresponsive"
                health["last_duration_seconds"] = now - started_at if started_at > 0 else elapsed
                health["quarantined_until"] = now + self.quarantine_seconds
                self._heartbeat(agent_name)
                self._log_quarantine_event(
                    "quarantine_watchdog_timeout",
                    agent_name,
                    health=health,
                    extra={
                        "elapsed_seconds": elapsed,
                        "last_started_at": started_at,
                        "last_heartbeat_at": last_heartbeat_at,
                    },
                )
                stale[agent_name] = {
                    "elapsed_seconds": elapsed,
                    "last_started_at": started_at,
                    "last_heartbeat_at": last_heartbeat_at,
                }

            if stale:
                # Clear stale endpoint slot counters after watchdog marks.
                self.lock_manager.reset_endpoint_state()

        return {
            "count": len(stale),
            "agents": stale,
            "at": now,
            "timeout_seconds": self.heartbeat_timeout_seconds,
        }

    def recover_hung_agents(self, force=False):
        """Recover agents from hung/failed/quarantined state and clear endpoint gate state."""
        now = time.time()
        recovered = {}
        with self.lock_manager.edit_lock(name="orchestrator_recovery"):
            for agent_name, health in self.agent_health.items():
                state = str(health.get("state") or "idle")
                quarantined_until = float(health.get("quarantined_until") or 0.0)
                is_flagged = state in {"hung", "failed"} or quarantined_until > now

                if not is_flagged and not force:
                    continue
                if state == "running" and not force:
                    continue

                recovered[agent_name] = {
                    "previous_state": state,
                    "previous_error": health.get("last_error"),
                    "previous_quarantined_until": quarantined_until,
                }

                health["state"] = "idle"
                health["quarantined_until"] = 0.0
                health["last_error"] = None
                health["last_recovered_at"] = now
                health["last_recovery_reason"] = "manual-recover-hung"
                health["current_profile"] = None
                health["current_model"] = None
                health["current_task_excerpt"] = None
                health["last_system_prompt_excerpt"] = None
                self._heartbeat(agent_name, state="idle")
                self._log_quarantine_event(
                    "manual_recover",
                    agent_name,
                    health=health,
                    extra={
                        "forced": bool(force),
                        "previous_state": state,
                        "previous_quarantined_until": quarantined_until,
                    },
                )

            # Clear endpoint slot bookkeeping to avoid stale inflight counters.
            self.lock_manager.reset_endpoint_state()

        return {
            "recovered": recovered,
            "count": len(recovered),
            "forced": bool(force),
            "at": now,
        }

    def is_route_call_active(self, route_name):
        """Best-effort check whether a route currently has an active in-flight Ollama call."""
        route = self._resolve_route(route_name)
        agent = self.subagents.get(route)
        if not agent:
            return False

        endpoint = getattr(agent, "endpoint", None)
        inflight = 0
        if endpoint:
            runtime = self.lock_manager.get_endpoint_runtime(endpoint)
            inflight = int((runtime or {}).get("inflight", 0) or 0)

        health = self.agent_health.get(route) or {}
        state_running = str(health.get("state") or "") == "running"
        return bool(inflight > 0 or state_running)

    def plan_request(self, user_input, profile_name=None, stream_override=None, model_override=None):
        with self.lock_manager.edit_lock(name="orchestrator_route"):
            self._reload_profiles_if_changed()
            if profile_name:
                profile = self._pick_profile_by_name(profile_name)
                if not profile:
                    raise AgentProfileError(f"Profile not found: {profile_name}")
            else:
                profile = self._pick_profile(user_input)

            route = profile.get("route", "ollama_amd") if profile else "ollama_amd"
            route = self._resolve_route(route)
            model = profile.get("model") if profile else None
            model = self._resolve_dynamic_model(profile, user_input, model)
            if model_override and str(model_override).strip():
                model = str(model_override).strip()
            self._enforce_profile_policy(profile, route, model)
            stream = bool(profile.get("default_stream", False)) if profile else False
            if stream_override is not None:
                stream = bool(stream_override)
            options = dict(profile.get("options", {})) if profile else None
            system_prompt = self._build_system_prompt(profile)
            timeout_seconds = self._get_profile_timeout_seconds(profile, route)
            retry_limit = self._get_profile_retry_limit(profile)

            return {
                "profile": profile,
                "route": route,
                "model": model,
                "stream": stream,
                "options": options,
                "system_prompt": system_prompt,
                "timeout_seconds": timeout_seconds,
                "retry_limit": retry_limit,
            }

    def handle_request_with_overrides(
        self,
        user_input,
        profile_name=None,
        model_override=None,
        stream_override=None,
        on_stream=None,
        direction=None,
    ):
        # Auto-extract direction if not provided
        if not direction and not profile_name:
            # Use nvidia-fast to summarize the prompt into a direction
            nvidia_fast_profile = None
            for profile in self.profiles:
                if profile.get("name") == "nvidia-fast":
                    nvidia_fast_profile = profile
                    break
            if nvidia_fast_profile:
                summarization_prompt = (
                    "Summarize the following user request into a high-level direction or intent statement. "
                    "Focus on constraints, goals, or style if present.\n\nUSER_REQUEST:\n" + user_input
                )
                try:
                    summary_route = self._resolve_route(nvidia_fast_profile["route"])
                    auto_direction = self._invoke_with_triage(
                        summary_route,
                        summarization_prompt,
                        model=nvidia_fast_profile.get("model"),
                        stream=False,
                        system_prompt=self._build_system_prompt(nvidia_fast_profile),
                        options=nvidia_fast_profile.get("options", {}),
                        on_stream=None,
                        profile_name="nvidia-fast",
                    )
                    direction = auto_direction.strip()
                except Exception:
                    direction = None
        # Combine direction and prompt as before
        if direction:
            merged_prompt = f"Direction:\n{direction.strip()}\n\nTask:\n{user_input.strip()}"
        else:
            merged_prompt = user_input.strip()

        plan = self.plan_request(
            merged_prompt,
            profile_name=profile_name,
            stream_override=stream_override,
            model_override=model_override,
        )
        preferred = plan["route"]
        model = plan["model"]
        stream = plan["stream"]
        options = plan["options"]
        system_prompt = plan["system_prompt"]
        timeout_seconds = float(plan.get("timeout_seconds") or self.call_timeout_seconds)
        retry_limit = max(0, int(plan.get("retry_limit") or 0))
        resolved_profile_name = (plan.get("profile") or {}).get("name")

        try:
            attempt = 0
            while True:
                attempt += 1
                self.analytics_log_event(
                    "agent_execution_attempt",
                    {
                        "agent": preferred,
                        "profile": resolved_profile_name,
                        "attempt": attempt,
                        "retry_limit": retry_limit,
                        "timeout_seconds": timeout_seconds,
                    },
                )
                try:
                    result = self._invoke_with_triage(
                        preferred,
                        merged_prompt,
                        model=model,
                        stream=stream,
                        system_prompt=system_prompt,
                        options=options,
                        on_stream=on_stream,
                        profile_name=resolved_profile_name,
                        timeout_override=timeout_seconds,
                    )
                    break
                except AgentStackError as err:
                    if attempt > retry_limit or not self._is_retryable_agent_error(err):
                        raise
                    self.analytics_log_event(
                        "agent_execution_retry",
                        {
                            "agent": preferred,
                            "profile": resolved_profile_name,
                            "attempt": attempt,
                            "retry_limit": retry_limit,
                            "error": str(err),
                            "error_code": err.code,
                        },
                    )
            if stream or on_stream or model_override:
                return result

            retry_policy = self._is_quality_retry_candidate(
                resolved_profile_name,
                model,
                result,
                merged_prompt,
            )
            if not retry_policy:
                return result

            fallback_route = retry_policy.get("fallback_route") or preferred
            fallback_model = retry_policy["fallback_model"]
            self._log_quality_retry(
                resolved_profile_name,
                preferred,
                model,
                fallback_route,
                fallback_model,
                retry_policy.get("reason"),
            )
            retry_options = copy.deepcopy(options) if options else None
            return self._invoke_with_triage(
                fallback_route,
                merged_prompt,
                model=fallback_model,
                stream=stream,
                system_prompt=system_prompt,
                options=retry_options,
                on_stream=None,
                profile_name=resolved_profile_name,
                timeout_override=timeout_seconds,
            )
        except Exception:
            if not self.enable_cross_route_fallback:
                raise
            fallback = self._fallback_agent(preferred)
            if not fallback:
                raise
            return self._invoke_with_triage(
                fallback,
                merged_prompt,
                model=model,
                stream=stream,
                system_prompt=system_prompt,
                options=options,
                on_stream=on_stream,
                profile_name=resolved_profile_name,
                timeout_override=timeout_seconds,
            )

    def handle_request(self, user_input):
        return self.handle_request_with_overrides(user_input)
