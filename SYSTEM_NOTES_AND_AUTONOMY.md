## GPU Endpoint Smoketesting Procedure (March 2026)

This section documents the process for smoketesting both NVIDIA and AMD Ollama endpoints to ensure GPU-backed inference is working as expected.

### 1. Model and Prompt Selection
- Choose a small, known-good model for each endpoint:
  - NVIDIA: `llama3.2:1b` (or any available model in `/ai/ollama-nvidia/models`)
  - AMD: `qwen2.5-coder:3b` (or any available model in `/ai/ollama-amd/models`)
- Use a simple prompt, e.g.: `"Write a Python function that prints hello world."`

### 2. Validate Model Availability
- Run:
  - `curl http://127.0.0.1:11434/api/tags` (NVIDIA)
  - `curl http://127.0.0.1:11435/api/tags` (AMD)
- Confirm the chosen model appears in the output.

### 3. Run Inference Test
- NVIDIA:
  ```sh
  curl -sS http://127.0.0.1:11434/api/generate -d '{"model":"llama3.2:1b","prompt":"Write a Python function that prints hello world.","stream":false}'
  ```
- AMD:
  ```sh
  curl -sS http://127.0.0.1:11435/api/generate -d '{"model":"qwen2.5-coder:3b","prompt":"Write a Python function that prints hello world.","stream":false}'
  ```

### 4. Check GPU Utilization
- NVIDIA: `nvidia-smi` before and after the test
- AMD: `rocm-smi` before and after the test (if available)

### 5. Record Results
- Log the prompt, model, endpoint, output, and GPU stats in this section or a dedicated log file.

### 6. Automation (Optional)
- Create a script to automate the above steps for both endpoints and append results to a log.

**Note:** This process should be run after any major update, model change, or hardware modification to validate GPU inference health.
## Ollama AMD/NVIDIA Service Mirroring (March 2026)

- Each Ollama instance (AMD and NVIDIA) must run as a separate service/container.
- NVIDIA Ollama runs on port 11434, mounting `/ai/ollama-nvidia/models` for its manifests/blobs.
- AMD Ollama runs on port 11435, mounting `/ai/ollama-amd/models` for its manifests/blobs.
- No model or manifest sharing between AMD and NVIDIA—each has a dedicated model store.
- See `docker-compose.ollama.yml` for service definitions and GPU device mapping.
# Dragonlair System Notes And Autonomy Plan

## 0) System Evolution Plan (Adaptive Creation Platform + Assistive Layer)

### Documentation And Traceability Requirements
- All major components must define responsibilities, expected inputs/outputs, constraints, and guardrails in markdown.
- System behavior must be explicit and testable; avoid hidden behavior implied only by code.
- Architecture decisions must be versioned and traceable in this file and companion planning docs.

### Interaction Philosophy: Minimal Friction, Maximum Control
- Minimal interaction means less user burden, not less user authority.
- Default UX: guided and simple for fast progress.
- Advanced UX: users can inspect plans, review options, and override route/model/context decisions.
- Automation must remain inspectable, interruptible, and reversible at key checkpoints.

### Publisher-Centric Refactor Direction
- Replace rigid single-path execution with modular, pluggable publishers.
- Publishers (for example book and code) are specialized tools in one shared runtime.
- Each publisher defines its own flow contract while inheriting shared guardrails (policy checks, safety checks, GPU policy, rewards).
- A pure code publisher must be able to execute a streamlined non-narrative flow independently of book-flow assumptions.
- Publishers must also support governed handoff: a book publisher may request a linked child code project when the book requires a concrete software deliverable.
- Cross-publisher handoff must be explicit, auditable, and bounded by a contract; this is not free-form mode switching.

### Nested Publisher Handoff Principle
- Canonical first case: `BookPublisher -> CodePublisher`.
- Example: a C++ programming book needs a real sample program, library, or exercise project; the book runtime should spawn a linked code project rather than synthesize unverified code inline.
- The parent publisher keeps ownership of the user-facing objective; the child publisher owns only the delegated artifact.
- Parent/child linkage must be visible in task state, run journals, and produced artifacts.
- Mutual exclusion rules between book mode and code mode should prevent unrelated operator collisions, not block intentional nested delegation inside one approved plan.

### Intent-Driven Planning And Option Selection
- Execution strategy must come from user intent analysis, not fixed flow branching.
- For each project, generate multiple structured options with tradeoffs (speed, depth, automation, cost).
- User selects the preferred option before execution proceeds.
- System role: strategic advisor plus executor; user remains decision-maker.

### Adaptive Quality Curriculum Principle
- Quality gates should start from conservative completion-safe floors and tighten over time as real output quality improves.
- Threshold progression must be data-driven, auditable, and bounded (no sudden jumps).
- Effective gate thresholds should be logged with each run so quality decisions remain explainable.
- Long-term objective: replace heuristic adaptation with ML policy learning constrained by guardrails.

### Assistive Intelligence Layer (New Abstraction)
- Add a conversational mediation layer between user intent and execution runtime.
- Capabilities:
  - natural conversation and guided planning
  - project research and option synthesis before execution
  - persistent memory for user preferences, project history, and recurring patterns
  - continuity across sessions and runs
- This layer is the long-term primary interface for Dragonlair interactions.

### Long-Term Interface Trajectory
- Evolution path:
  1. tool-based generators (book/code)
  2. shared agent runtime
  3. intent-driven orchestration with option approval
  4. assistive conversational layer with persistent memory
  5. full interface abstraction where assistant mediates broader Ubuntu/Linux workflows
- Principle: conversation over configuration, with optional deep operator controls.

## 1) Current System State

### Endpoints

**Model Storage Policy (March 2026):**
- All Ollama model manifests and blobs for NVIDIA/AMD must reside in `/ai/ollama-nvidia/models` (NVIDIA) and `/ai/ollama-amd/models` (AMD).
- The `/home/daravenrk/dragonlair/model-sets` directory must NOT contain any manifests or blobs—only text lists or configuration files are allowed.
- Any stray manifests or blobs in `model-sets` should be deleted to maintain a clean and predictable structure.
- AMD endpoint: `http://127.0.0.1:11435` (`ollama_amd`)
- NVIDIA endpoint: `http://127.0.0.1:11434` (`ollama_nvidia`)
- Agent API/UI: `http://127.0.0.1:11888` (`dragonlair_agent_stack`)
- Fetcher: `http://127.0.0.1:11999` (`fetcher`)

### Exposure Policy
- `11434`, `11435`, and `11999` are local-only by default and should not be exposed on LAN/WAN.
- `11888` may be exposed to trusted LAN clients when operator access is required.
- In production, place `11888` behind a reverse proxy/TLS and source-IP controls.

NVIDIA non-stream:
```sh
curl -sS http://127.0.0.1:11434/api/generate -d '{"model":"llama3.2:1b","prompt":"reply with ok","stream":false}'
```

NVIDIA stream:
```sh
curl -sS http://127.0.0.1:11434/api/generate -d '{"model":"llama3.2:1b","prompt":"reply with ok","stream":true}'
```

AMD non-stream:
```sh
curl -sS http://127.0.0.1:11435/api/generate -d '{"model":"qwen2.5-coder:14b","prompt":"reply with ok","stream":false}'
```

AMD stream:
```sh
curl -sS http://127.0.0.1:11435/api/generate -d '{"model":"qwen2.5-coder:14b","prompt":"reply with ok","stream":true}'
```

### GPU Layer Standard (March 2026)
- Standard requirement: all models routed to NVIDIA or AMD must execute with GPU offload (`num_gpu_layers > 0`).
- CPU fallback is not permitted for GPU-routed requests.
- Enforce this with explicit per-model layer maps in compose:
  - `AGENT_NVIDIA_NUM_GPU_BY_MODEL`
  - `AGENT_AMD_NUM_GPU_BY_MODEL`
- Keep `AGENT_BLOCK_CPU_BACKEND=true` in production.
- Regression includes a GPU-layer validation check for both endpoints and fails when a model reports non-positive GPU layers.

### Operational Regression Note (2026-03-19)
- Operator reports NVIDIA GPU compute was working correctly on the current branch most or all of the time on 2026-03-18.
- Treat current uncertainty around "VRAM residency vs actual GPU compute" as a likely recent branch regression candidate.
- `main` is currently too far behind to be used as a clean operational fallback without losing substantial newer work.
- Preferred recovery/debug path: isolate recent branch-level config/runtime changes and prove compute behavior with live telemetry during sustained inference.

### Full GPU Layer Enforcement + AMD Dual-GPU Split (2026-03-19, Session 2)

#### Problems Resolved
1. **`num_gpu=999` crashed NVIDIA Ollama** — `memory layout cannot be allocated with num_gpu = 999`.
  - Root cause: orchestrator was injecting `num_gpu=999` as the "all layers" sentinel, but Ollama/CUDA rejects any value > model layer count as invalid. Correct sentinel is `-1`.
  - Fix: all per-model and default GPU counts changed to `-1` in `docker-compose.agent.yml` env vars and in `orchestrator.py`. `_parse_env_json_int_map` updated to preserve `-1` without clamping.

2. **`ollama/ollama:latest` image has no ROCm backend** — AMD container fell back to CPU silently.
  - Root cause: `ollama:latest` bundles CUDA libs only. AMD requires `ollama:rocm` image.
  - Fix: `docker-compose.ollama.yml` AMD service changed to `image: ollama/ollama:rocm`.
  - Group fix: render group (`GID 993` on this host) added as `"993"` numeric string in `group_add` since the group name is absent inside the container.

3. **`_save_ui_state_snapshot` NameError crashing `/api/status`** — function was renamed to `_refresh_ui_state_snapshot` but one call site in `api_server.py` still used the old name.
  - Fix: call site in `status()` endpoint corrected to `_refresh_ui_state_snapshot(event_type="status")`.

4. **`args.debug` AttributeError in `book_flow.py`** — bare attribute access crashed when `debug` key not in SimpleNamespace.
  - Fix: changed to `getattr(args, "debug", False)` at line ~2287.

#### AMD Dual-GPU Configuration (Confirmed Working 2026-03-19)
- Image: `ollama/ollama:rocm`
- Devices passed through: `/dev/kfd`, `/dev/dri`
- Groups: `video`, `"993"` (numeric render GID)
- Environment:
  - `HIP_VISIBLE_DEVICES=0,1`
  - `ROCR_VISIBLE_DEVICES=0,1`
  - `OLLAMA_SCHED_SPREAD=1` (spreads layers across both GPUs automatically)
- Result at runtime: `offloaded 33/33 layers to GPU` — split ~20 layers on ROCm0, ~13 on ROCm1 (both AMD Radeon RX 6800, 16 GiB VRAM each, 32 GiB total)
- VRAM per GPU during `qwen3.5:9b` inference: ROCm0 ~3.9 GiB · ROCm1 ~3.95 GiB

#### NVIDIA Single-GPU Configuration (Confirmed Working 2026-03-19)
- Device: GTX 1660 SUPER (6 GiB VRAM)
- `num_gpu=-1` (Ollama "offload all" sentinel — accepted without error)
- Result at runtime: `offloaded 32/33 layers to GPU` — 1 output-projection layer on CPU is normal for 6 GiB VRAM; all transformer compute on GPU
- CUDA0 weights: ~2 GiB · KV cache: ~2 GiB · compute graph: ~900 MiB

#### Orchestrator GPU Policy Methods (orchestrator.py, 2026-03-19)
- `_parse_env_json_int_map(env_key)` — parses JSON int maps from env; preserves `-1`
- `_resolve_amd_tensor_split()` — reads `AGENT_AMD_TENSOR_SPLIT` (default `"0.5,0.5"`)
- `_resolve_model_num_gpu_layers(model, route)` — per-model, per-route layer count
- `_apply_gpu_execution_policy(agent_name, model_name, options)` — injected into every inference call
  - Sets `num_gpu=-1` on all routes
  - On AMD: also sets `num_gpus=2`, `tensor_split=[0.5,0.5]`, `main_gpu=0`
  - On NVIDIA: strips `tensor_split`/`main_gpu` to avoid CUDA errors

#### AGENT_AMD_GPU_COUNT and Split env vars
```yaml
AGENT_BLOCK_CPU_BACKEND: "true"
AGENT_FORCE_FULL_GPU_LAYERS: "true"
AGENT_NVIDIA_NUM_GPU_DEFAULT: "-1"
AGENT_AMD_NUM_GPU_DEFAULT: "-1"
AGENT_AMD_GPU_COUNT: "2"
AGENT_AMD_MAIN_GPU: "0"
AGENT_AMD_TENSOR_SPLIT: "0.5,0.5"
```

#### Quick GPU Validation
```sh
# AMD — confirm 33/33 layers + dual split
docker logs ollama_amd 2>&1 | grep -iE "offloaded|ROCm|split"

# NVIDIA — confirm 32/33 layers on CUDA
docker logs ollama_nvidia 2>&1 | grep -iE "offloaded|CUDA|Load failed"

# Live AMD GPU utilization
rocm-smi --showuse

# Live NVIDIA GPU utilization
nvidia-smi
```

### NVIDIA GPU Policy Update (2026-03-19)
- **Operator requirement**: ALL model layers must execute on GPU. No CPU fallback permitted on NVIDIA route.
- **GTX 1660 SUPER** (6GB VRAM) is the NVIDIA device. This limits maximum model size.
- **Enforcement added to `orchestrator.py`** in `_enforce_profile_policy()`:
  - Any model dispatched to `ollama_nvidia` must appear in `AGENT_NVIDIA_TINY_MODELS`.
  - Non-allowlisted models are hard-rejected with `AgentProfileError` before the request reaches Ollama.
- **Allowed NVIDIA models** (configured via `AGENT_NVIDIA_TINY_MODELS` env var):
  - `qwen3.5:0.8b`, `qwen3.5:2b`, `qwen3.5:4b`
  - `qwen2.5-coder:1.5b`, `qwen2.5-coder:3b`
  - `llama3.2:1b`, `llama3.2:3b`
  - `codegemma:2b`
- **All 8 NVIDIA-routed agent profiles** now have explicit `allowed_routes: ollama_nvidia` frontmatter.
- **All large-model profiles** (9B, 27B, 14B) have explicit `allowed_routes: ollama_amd` frontmatter.
- **Model store is shared** — `/ai/ollama-nvidia/models` and `/ai/ollama-amd/models` may coexist on disk; the restriction is enforced at the orchestration layer, not by deleting models.

### How to Validate a Live Run (2026-03-19)

**1. Check GPU compute is active during inference:**
```sh
watch -n 2 nvidia-smi
# Look for: GPU-Util > 0%, Memory-Usage > idle baseline
```

**2. Check agent queue state:**
```sh
curl -s http://127.0.0.1:11888/api/health | python3 -c "
import json,sys; h=json.load(sys.stdin)
q=h['resource_tracker']['queue']
print('Queued:', q['status_counts']['queued'])
print('Running:', q['status_counts']['running'])
print('Failed:', q['status_counts']['failed'])
agents=h['resource_tracker']['agents']['health']['agents']
for k,v in agents.items():
    print(k, v['state'], 'model='+str(v['current_model']))
"
```

**3. Check task ledger:**
```sh
python3 -c "
import json
data=json.load(open('/home/daravenrk/dragonlair/book_project/task_ledger.json'))
for t in data.get('tasks',[])[-10:]:
    print(str(t.get('status'))[:10], str(t.get('profile'))[:28], str(t.get('created_at'))[:19])
"
```

**4. Verify Ollama container logs for model offload:**
```sh
docker logs ollama_nvidia 2>&1 | grep -E "offloaded|CUDA|CPU size|waiting"
# Good: offloaded 17/17 layers to GPU (all layers)
# Bad: offloaded 24/33 layers to GPU (partial — CPU fallback active)
```

**5. Check quarantine and failure events:**
```sh
tail -20 /home/daravenrk/dragonlair/book_project/quarantine_events.jsonl | python3 -c "
import json,sys
for line in sys.stdin:
    r=json.loads(line.strip())
    print(r.get('timestamp','')[:10], r.get('agent'), r.get('reason'))
"
```

**6. Smoketest NVIDIA GPU inference (small model, fast response):**
```sh
time curl -sS http://127.0.0.1:11434/api/generate \
  -d '{"model":"llama3.2:1b","prompt":"reply with ok","stream":false}' \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(r.get('response','?')[:80])"
# Should complete in < 15 seconds and return a text response.
```

## 2) Agent Stack Architecture

### Core Runtime
- `agent_stack/orchestrator.py`
- `agent_stack/ollama_subagent.py`
- `agent_stack/lock_manager.py`
- `agent_stack/profile_loader.py`
- `agent_stack/context_planner.py`
- `agent_stack/cli.py`

### Behavior Profiles (md-driven)
- `agent_stack/agent_profiles/amd-coder.agent.md`
- `agent_stack/agent_profiles/amd-writer.agent.md`
- `agent_stack/agent_profiles/nvidia-fast.agent.md`
- `agent_stack/agent_profiles/book-researcher.agent.md` (now uses qwen3.5:14b, 128k context, internet/news/Wikipedia research)

### Design
- Behavior is defined in markdown profiles.
- Frontmatter controls route/model/options/intent matching.
- Markdown sections are composed into system prompt context.
- Orchestrator hot-reloads profile files automatically.

## 3) Locking, Anti-Spam, And Triage

## 7) March 2026 Update: Research Agent
- book-researcher now uses qwen3.5:14b (128k context)
- Internet, news, and Wikipedia research is now standard for research agent
- Research output is structured and data-driven

### Locks
- Edit lock prevents volatile concurrent routing edits.
- Endpoint slot lock limits request concurrency and request cadence.
- Lock root default: `/tmp/dragonlair_agent_stack`.

### Endpoint controls
- AMD default: `max_inflight=1`, `min_interval_seconds=1.5`
- NVIDIA default: `max_inflight=1`, `min_interval_seconds=1.0`
- Endpoint slot wait timeout: `1800s` (allows deep queue wait before timeout)

### Single-model route guard
- `STRICT_ONE_MODEL_PER_ROUTE=true` (default in docker stack)
- For each route (`ollama_amd` / `ollama_nvidia`), queued/running work must use one model at a time.
- If a different model is requested on a busy route, API returns `409` conflict until queue drains.
- This enforces operational "one active model per endpoint route" behavior.

### Triage
- Timeout marks subagent as hung.
- Exceptions mark subagent as failed.
- Hung/failed agents are quarantined.
- Fallback route is used when available.
- Health report available via orchestrator API and CLI.

### Heartbeat telemetry
- Each subagent emits heartbeat updates into controller health state.
- Heartbeats include:
  - `state`
  - `last_heartbeat_at`
  - `current_profile`
  - `current_model`
  - `current_task_excerpt`
  - `last_system_prompt_excerpt`
  - `last_response_preview`
- UI now exposes a dedicated Subagent Telemetry table for live visibility.

## 4) Baseline Context Per Request

Each routed action sends:
1. Profile-derived system context (Purpose/System Behavior/Actions)
2. User prompt
3. Profile options (`num_ctx`, `num_predict`, `temperature`)

Impact:
- More stable behavior and policy adherence.
- Higher baseline token use due to system instruction injection.
- Keep md behavior sections concise to preserve context budget.

## 4.1) Model Selection Policy (Latest-First)

Default policy:
- Prefer latest available model families for primary profiles.
- Use lower-tier/older or smaller models only when latency, stability, or output quality fails requirements.

Operational rule:
- Start with latest-tier profile defaults (`amd-coder`, `amd-writer`, `nvidia-fast`).
- If first-token latency or failure rate exceeds operational targets, switch traffic to `nvidia-lowlatency` or equivalent fallback profile.
- Re-evaluate and return to latest-tier profiles after stabilization.

## 4.2) Book-Flow Run Strategy Standard (2026-03-20)

This is the current operational standard for first-pass run stability. It is intentionally strict so runs are reproducible.

### Required strategy rules
- Stage ownership is profile-driven and must be declared in profile frontmatter (`route`, `allowed_routes`, `model`, `model_allowlist`).
- NVIDIA-routed profiles must only use models from the approved NVIDIA tiny-model allowlist.
- GPU policy is mandatory: no CPU fallback for GPU-routed stages.
- Deterministic stage fallback is allowed, but fallback events must be journaled and auditable.

### Current stability-biased mapping
- `book-publisher-brief` -> `ollama_nvidia` / `qwen3.5:4b`
- `writing-assistant` -> `ollama_nvidia` / `qwen3.5:4b`
- `book-canon` -> `ollama_nvidia` / `qwen3.5:4b` (stability routing while AMD endpoint responsiveness is under investigation)
- `book-writer` -> `ollama_nvidia` / `qwen3.5:4b` (first-success stabilization policy)
- `book-researcher` remains AMD-routed for now, with deterministic dossier fallback when empty-output gate fails.

### Flexibility with rigidity
- Rigidity: stage contract, route/model allowlists, and GPU policy are non-negotiable.
- Flexibility: route/model strategy can be changed by profile updates, but only through audited policy changes and regression re-validation.
- Every strategy change must be reflected in:
  - profile frontmatter,
  - runtime diagnostics (`run_journal.jsonl`, `agent_diagnostics.jsonl`),
  - and future-work notes (`DEV_NEXT_STEPS.md`).

## 5) Predetermine Context Before Running

Use context planner:
```sh
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/plan-context \
  --prompt "Write a structured debate on local AI model orchestration and endpoint reliability." \
  --expected-output 900
```

Planner output includes `SUGGESTED_NUM_CTX`.

Use optional knobs:
- `--profile`
- `--history-tokens`
- `--safety-ratio`
- `--prompt-file`

## 6) New CLI Control Layer (stream + control)

Command:
```sh
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl <subcommand>
```

### Subcommands
List profiles:
```sh
agentctl profiles
```

Plan route/model/options for a prompt:
```sh
agentctl plan "write a concise debate on local AI governance"
```

One-shot request:
```sh
agentctl once "design a robust state machine"
```

One-shot with streaming:
```sh
agentctl --stream once "design a robust state machine"
```

Interactive chat:
```sh
agentctl --stream chat
```

Health report:
```sh
agentctl health
```

In-chat controls:
- `/help`
- `/profiles`
- `/health`
- `/plan <text>`
- `/quit`

## 6.1) Dockerized Agent Stack (Backend + Frontend)

### Build and start
```sh
/home/daravenrk/dragonlair/bin/agent-stack-up
```

### Stop
```sh
/home/daravenrk/dragonlair/bin/agent-stack-down
```

### Logs
```sh
/home/daravenrk/dragonlair/bin/agent-stack-logs
```

### Frontend steering UI
- Open `http://127.0.0.1:11888`
- From another system on the same network, open `http://<HOST_IP>:11888` (example: `http://192.168.86.36:11888`).
- Provide profile (optional), direction, and task prompt.
- Use Queue Task for background execution.
- Use Run Stream for streamed response output.

External access requirements:
- Container port mapping is external-facing (`0.0.0.0:11888:11888`).
- Service inside container listens on `0.0.0.0:11888`.
- Host firewall must allow TCP `11888` (if firewall is enabled).

### API endpoints
- `GET /api/health`
- `GET /api/profiles`
- `GET /api/status`
- `POST /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/stream`

Queue/ticket behavior:
- Task creation returns `queue_position`.
- Status payload includes per-task `queue_position`.
- Busy-route requests are serialized and wait their turn behind running work.

### CLI monitoring space (subagents + tasks)
```sh
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl server-status
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl server-watch --interval 1
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl server-submit "reply with ok" --profile nvidia-fast
```

These commands provide a terminal view of live subagent states, task queue counts, and per-task status.

## 7) Backup And Restore (No Model Data)

Backup command:
```sh
/home/daravenrk/dragonlair/bin/dragonlair_backup_nodata.sh
```

Destination:
- `daravenrk@192.168.86.34:/backups/dragonlair`

Restore base config/tooling:
```sh
rsync -avz daravenrk@192.168.86.34:/backups/dragonlair/opt/ai-stack/ /opt/ai-stack/
rsync -avz daravenrk@192.168.86.34:/backups/dragonlair/home/daravenrk/dragonlair/ /home/daravenrk/dragonlair/
```

Then repull models from model lists.

### Stack backup/restore commands (recommended)

Backup (config/state only, no model blobs):
```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_backup
```

Backup including model data:
```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_backup --with-models
```

Restore (config/state only):
```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_restore
```

Restore including model data:
```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_restore --with-models
```

Dry-run support:
```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_backup --dry-run
/home/daravenrk/dragonlair/bin/dragonlair_stack_restore --dry-run
```

Default remote target/source:
- `daravenrk@192.168.86.34:/backups/dragonlair`

Override remote path if needed:
```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_backup --dest user@host:/path
/home/daravenrk/dragonlair/bin/dragonlair_stack_restore --src user@host:/path
```

## 7.1) Bare-Metal Backup And Recovery

Bare-metal backup command:

 /home/daravenrk/dragonlair/bin/dragonlair_metal_backup

Modes:

 - Metadata only: /home/daravenrk/dragonlair/bin/dragonlair_metal_backup --metadata-only
 - Dry-run: /home/daravenrk/dragonlair/bin/dragonlair_metal_backup --dry-run

Default destination:

 - daravenrk@192.168.86.34:/backups/dragonlair-metal

What it captures:

 - Hardware/storage metadata (lsblk, blkid, partition dumps)
 - OS/package/driver metadata (dpkg, apt-manual, dkms, nvidia-smi, rocm-smi)
 - Critical config trees and Dragonlair stack files
 - Optional root filesystem + boot + EFI snapshots for full machine recovery

Recovery runbook:

 - /home/daravenrk/dragonlair/BARE_METAL_RECOVERY.md

## 8) Full Backend Autonomy Plan

### Phase A: Stabilize Control Plane
- Finalize all md profiles per workload class.
- Add profile lint rules (required keys/size/unsafe values).
- Add max system-prompt size guard.
- Add per-profile policy fields (allowed routes, model caps, timeout caps).

### Phase B: Autonomous Task Execution
- Add task queue with durable state (SQLite).
- Add job states: queued/running/succeeded/failed/retrying.
- Add retry and circuit-breaker policy per route/profile.
- Persist request and response metadata for postmortem.

### Phase C: Self-Healing And Escalation
- Add endpoint probes and health watchdog loop.
- Auto-quarantine unstable routes with cooldown windows.
- Automatic failover chain by profile policy.
- Add structured incident logs and triage reports.

### Phase D: Long-Running Agent Goals
- Add objective planner (goal -> subtasks -> execution graph).
- Add checkpointing at each subtask boundary.
- Add resume mode after restarts.
- Add approval gates for sensitive actions.

### Phase E: Observability And Operations
- Add metrics endpoint (latency, failures, quarantines, queue depth).
- Add CLI dashboards for queue + route status.
- Add session replay for debugging.
- Add alert hooks (syslog/webhook) for critical failures.

### Phase F: Production Readiness
- Add integration tests for routing, fallback, and stream paths.
- Add chaos tests for endpoint hang/failure behavior.
- Add restore drills from backup and scripted bring-up.
- Freeze release profiles and version profile bundles.

## 9) Immediate Build Steps

1. Add profile lint command and preflight validation.
2. Add queue-backed worker mode for autonomous background execution.
3. Add watchdog daemon and persistent health state.
4. Add CLI dashboard mode for live operations.
5. Add restore script and run a documented restore test.

---

This file is the operational notes + autonomy roadmap for current Dragonlair backend evolution.

## 10) Book Mode Agent Flow

Book mode profiles:
- `book-writer`
- `book-editor`
- `book-publisher`

Profile files:
- /home/daravenrk/dragonlair/agent_stack/agent_profiles/book-writer.agent.md
- /home/daravenrk/dragonlair/agent_stack/agent_profiles/book-editor.agent.md
- /home/daravenrk/dragonlair/agent_stack/agent_profiles/book-publisher.agent.md

Execution script:
- /home/daravenrk/dragonlair/bin/book-flow

Flow stages:
1. Writer drafts section markdown.
2. Editor revises and corrects the draft.
3. Publisher analyzes for consistency and publication readiness.

Publisher checks currently include:
- character arc
- story arc
- hero's journey alignment
- context consistency ("check for out of context" gate)
- prose quality

Example run:

 /home/daravenrk/dragonlair/bin/book-flow \
   --title "Project Nightglass" \
   --premise "A burned-out systems engineer discovers a hidden civic AI controlling her city." \
   --chapter-number 1 \
   --chapter-title "Fault Line" \
   --section-title "Opening Incident" \
   --section-goal "Introduce protagonist, normal world, and destabilizing event."

Output artifacts are written to:
- /home/daravenrk/dragonlair/book_runs/<timestamp-and-section-slug>/

Artifacts:
- 01_writer_draft.md
- 02_editor_revision.md
- 03_publisher_raw.txt
- 04_publisher_report.json
- 05_final_section.md
- run_summary.json

## 11) Book Project Scaffolding Plan

When the publisher agent determines there is enough information to proceed (after user selects a book proposal):

- The system will automatically scaffold the book project folder structure as follows:

```
/book_runs/BookTitle/
  overview.md                # Book overview, premise, and structure
  timeline.md                # Chronological event log (optional)
  dictionaries/
    characters.md            # Character names, traits, arcs
    locations.md             # City/place names, descriptions
    tech.md                  # Technology, magic, or worldbuilding details
  sections/
    01_intro.md              # First section/chapter
    02_conflict.md           # Second section/chapter
    ...                      # Additional sections/chapters
```

**Scaffolding Steps:**
1. Create the root folder for the book under `/book_runs/BookTitle/` (slugified).
2. Create `overview.md` with the selected proposal and outline.
3. Create `dictionaries/` subfolder with empty or template files for `characters.md`, `locations.md`, and `tech.md`.
4. Create `sections/` subfolder with placeholder files for each planned section/chapter (named and numbered).
5. Optionally create `timeline.md` if the book structure benefits from a timeline.
6. All agents (publisher, writer, editor) will reference and update these files as the book progresses.

**Benefits:**
- Ensures a consistent, organized workspace for every book project.
- Enables agents to maintain memory and context efficiently.
- Supports user and agent collaboration on structure, worldbuilding, and content.

**Trigger:**
- This scaffolding is triggered by the publisher agent after the user selects a book proposal and confirms the outline.

## 12) Story Bible and Interactive Feedback

### Story Bible
- Add a `story_bible.md` file to each book project folder.
- Purpose: Track long-range character arcs, subplots, themes, unresolved plot points, and major world details.
- All agents (publisher, writer, editor, topicalizer) reference and update the story bible as the book progresses.
- Structure example:
  - Characters: arcs, motivations, relationships
  - Plot threads: status, open/closed
  - Themes: how/where expressed
  - World rules: key facts, constraints

### Interactive Agent Feedback
- Agents (editor, topicalizer, publisher) can return actionable suggestions or questions for the user.
- System will:
  - Surface these responses to the user (via UI or file-based notes).
  - Allow the user to select new topics, submit new ideas, or clarify intent at any stage.
  - Pause workflow for user input when agent requests clarification or new direction.
- Example: If the topicalizer cannot generate strong proposals, it asks the user for more detail or a new ideation blurb.
- Example: If the editor finds a major continuity issue, it flags it for user review and possible rewrite.

**Benefits:**
- Maintains deep continuity and narrative quality across the book.
- Keeps the user in the loop for creative decisions and problem-solving.
- Makes the workflow adaptive and collaborative, not just linear.
