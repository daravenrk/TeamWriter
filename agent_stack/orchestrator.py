# TODO: Agent Lifecycle Logging Improvements
# - Add log entries for agent start, return, and error
# - Ensure all agent actions and state changes are logged to changes.log
# - Log agent handoffs and inter-agent communication
# agent_stack/orchestrator.py

import time
import os
import threading
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import nullcontext, contextmanager
from glob import glob
from pathlib import Path

from .lock_manager import AgentLockManager, EndpointPolicy
from .ollama_subagent import OllamaSubagent
from .profile_loader import load_agent_profiles

class OrchestratorAgent:
    def __init__(self):
        # Analytics: track process runtime, actions, prompts, and results
        self.analytics_log = []
        self.analytics_run_start = None
        self.analytics_run_end = None
        # Analytics: track total unique agents used per run
        self.analytics_total_agents_used = set()
        # Track currently active agents
        self.active_agents = set()
        self.lock_manager = AgentLockManager()
        self.server_mode = str(os.environ.get("AGENT_SERVER_MODE", "standard")).strip().lower()
        self.strict_mode_validation = str(os.environ.get("AGENT_STRICT_MODE_VALIDATION", "true")).lower() in {"1", "true", "yes", "on"}
        self.profile_dir = str(Path(__file__).parent / "agent_profiles")
        self.profiles = load_agent_profiles(self.profile_dir)
        self._profile_stamp = self._compute_profile_stamp()
        amd_endpoint = os.environ.get("OLLAMA_AMD_ENDPOINT", "http://127.0.0.1:11435")
        nvidia_endpoint = os.environ.get("OLLAMA_NVIDIA_ENDPOINT", "http://127.0.0.1:11434")
        self.route_max_inflight = {
            "ollama_amd": max(1, int(os.environ.get("AGENT_ROUTE_MAX_INFLIGHT_AMD", "1"))),
            "ollama_nvidia": max(1, int(os.environ.get("AGENT_ROUTE_MAX_INFLIGHT_NVIDIA", "1"))),
        }
        self.endpoint_max_inflight = {
            "ollama_amd": max(1, int(os.environ.get("AGENT_ENDPOINT_MAX_INFLIGHT_AMD", "1"))),
            "ollama_nvidia": max(1, int(os.environ.get("AGENT_ENDPOINT_MAX_INFLIGHT_NVIDIA", "1"))),
        }
        openclaw_mode_enabled = self.server_mode in {"openclaw-client", "openclaw"}
        if openclaw_mode_enabled and self.strict_mode_validation and not self.profiles:
            raise RuntimeError("No agent profiles loaded for openclaw-client mode")

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
        self.profiles = load_agent_profiles(self.profile_dir)
        self._profile_stamp = self._compute_profile_stamp()
        amd_endpoint = os.environ.get("OLLAMA_AMD_ENDPOINT", "http://127.0.0.1:11435")
        nvidia_endpoint = os.environ.get("OLLAMA_NVIDIA_ENDPOINT", "http://127.0.0.1:11434")
        self.route_max_inflight = {
            "ollama_amd": max(1, int(os.environ.get("AGENT_ROUTE_MAX_INFLIGHT_AMD", "1"))),
            "ollama_nvidia": max(1, int(os.environ.get("AGENT_ROUTE_MAX_INFLIGHT_NVIDIA", "1"))),
        }
        self.endpoint_max_inflight = {
            "ollama_amd": max(1, int(os.environ.get("AGENT_ENDPOINT_MAX_INFLIGHT_AMD", "1"))),
            "ollama_nvidia": max(1, int(os.environ.get("AGENT_ENDPOINT_MAX_INFLIGHT_NVIDIA", "1"))),
        }
        openclaw_mode_enabled = self.server_mode in {"openclaw-client", "openclaw"}
        if openclaw_mode_enabled and self.strict_mode_validation and not self.profiles:
            raise RuntimeError("No agent profiles loaded for openclaw-client mode")

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
        self.route_semaphores = {
            route: threading.BoundedSemaphore(value=limit)
            for route, limit in self.route_max_inflight.items()
        }
        raw_support = str(os.environ.get("AGENT_SUPPORT_PROFILES", "book-publisher,book-continuity,book-canon,orchestrator"))
        self.support_profiles = {item.strip() for item in raw_support.split(",") if item.strip()}
        # External subagents (ChatGPU, Copilot) are registered but not called until internal logic is done
        # Example: self.subagents['chatgpu'] = ChatGPUSubagent()
        # Example: self.subagents['copilot'] = CopilotSubagent()

    def _heartbeat(self, agent_name, **fields):
        health = self.agent_health[agent_name]
        health["last_heartbeat_at"] = time.time()
        for key, value in fields.items():
            health[key] = value

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
                raise RuntimeError(f"AGENT_ROUTE_ALIASES has unsupported source route: {source}")
            if self.strict_mode_validation and target and target not in {"ollama_amd", "ollama_nvidia"}:
                raise RuntimeError(f"AGENT_ROUTE_ALIASES has unsupported target route: {target}")
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
        self.profiles = load_agent_profiles(self.profile_dir)
        self._profile_stamp = current

    def _pick_profile(self, user_input):
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

    def _pick_profile_by_name(self, profile_name):
        for profile in self.profiles:
            if profile.get("name") == profile_name:
                return profile
        return None

    def _build_system_prompt(self, profile):
        if not profile:
            return None
        sections = profile.get("sections", {})
        purpose = sections.get("purpose", "")
        behavior = sections.get("system_behavior", "")
        actions = sections.get("actions", "")

        blocks = [
            f"Agent Profile: {profile.get('name', 'default')}",
            "",
            "Purpose:",
            purpose,
            "",
            "System Behavior:",
            behavior,
            "",
            "Actions:",
            actions,
        ]
        rendered = "\n".join(blocks).strip()
        return rendered or None

    def _is_available(self, agent_name):
        health = self.agent_health[agent_name]
        return time.time() >= float(health.get("quarantined_until", 0.0))

    def _triage_mark_hung(self, agent_name, started_at):
        health = self.agent_health[agent_name]
        health["state"] = "hung"
        health["hung_count"] = int(health.get("hung_count", 0)) + 1
        health["last_error"] = "timeout/hung"
        health["last_duration_seconds"] = time.time() - started_at
        health["quarantined_until"] = time.time() + self.quarantine_seconds
        self._heartbeat(agent_name)

    def _triage_mark_failed(self, agent_name, err, started_at):
        health = self.agent_health[agent_name]
        health["state"] = "failed"
        health["failed_count"] = int(health.get("failed_count", 0)) + 1
        health["last_error"] = str(err)
        health["last_duration_seconds"] = time.time() - started_at
        health["quarantined_until"] = time.time() + self.quarantine_seconds
        self._heartbeat(agent_name)

    def _triage_mark_success(self, agent_name, started_at):
        health = self.agent_health[agent_name]
        health["state"] = "healthy"
        health["last_completed_at"] = time.time()
        health["last_duration_seconds"] = time.time() - started_at
        health["last_error"] = None
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
    ):
        if not self._is_available(agent_name):
            raise RuntimeError(f"Agent {agent_name} is quarantined")

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
        call_timeout_seconds = float(self.route_call_timeouts.get(agent_name, self.call_timeout_seconds))
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
                        raise RuntimeError(f"Agent {agent_name} hung and was quarantined")
                    except Exception as err:
                        self._triage_mark_failed(agent_name, err, started_at)
                        self.analytics_log_event("agent_error", {"agent": agent_name, "error": str(err)})
                        if hasattr(self, "diagnostics_path") and self.diagnostics_path:
                            with open(self.diagnostics_path, "a", encoding="utf-8") as f:
                                f.write(json.dumps({
                                    "event": "orchestrator_agent_error",
                                    "agent": agent_name,
                                    "profile": profile_name,
                                    "error": str(err),
                                    "timestamp": time.time(),
                                }) + "\n")
                        raise

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
        return {
            "server_mode": self.server_mode,
            "timeout_seconds": self.call_timeout_seconds,
            "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
            "route_timeout_seconds": self.route_call_timeouts,
            "route_max_inflight": self.route_max_inflight,
            "endpoint_max_inflight": self.endpoint_max_inflight,
            "enable_cross_route_fallback": self.enable_cross_route_fallback,
            "quarantine_seconds": self.quarantine_seconds,
            "agents": self.agent_health,
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
                health["current_profile"] = None
                health["current_model"] = None
                health["current_task_excerpt"] = None
                health["last_system_prompt_excerpt"] = None
                self._heartbeat(agent_name, state="idle")

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
                    raise RuntimeError(f"Profile not found: {profile_name}")
            else:
                profile = self._pick_profile(user_input)

            route = profile.get("route", "ollama_amd") if profile else "ollama_amd"
            route = self._resolve_route(route)
            model = profile.get("model") if profile else None
            if model_override and str(model_override).strip():
                model = str(model_override).strip()
            stream = bool(profile.get("default_stream", False)) if profile else False
            if stream_override is not None:
                stream = bool(stream_override)
            options = dict(profile.get("options", {})) if profile else None
            system_prompt = self._build_system_prompt(profile)

            return {
                "profile": profile,
                "route": route,
                "model": model,
                "stream": stream,
                "options": options,
                "system_prompt": system_prompt,
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
        if not direction:
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

        try:
            return self._invoke_with_triage(
                preferred,
                merged_prompt,
                model=model,
                stream=stream,
                system_prompt=system_prompt,
                options=options,
                on_stream=on_stream,
                profile_name=(plan.get("profile") or {}).get("name"),
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
                profile_name=(plan.get("profile") or {}).get("name"),
            )

    def handle_request(self, user_input):
        return self.handle_request_with_overrides(user_input)
