# Dragonlair System Notes And Autonomy Plan

## 1) Current System State

### Endpoints
- AMD endpoint: `http://127.0.0.1:11435` (`ollama_amd`)
- NVIDIA endpoint: `http://127.0.0.1:11434` (`ollama_nvidia`)

### External Network Testing
Use server IP `192.168.86.36` from other LAN hosts.

NVIDIA non-stream:
```sh
curl -sS http://192.168.86.36:11434/api/generate -d '{"model":"llama3.2:1b","prompt":"reply with ok","stream":false}'
```

NVIDIA stream:
```sh
curl -sS http://192.168.86.36:11434/api/generate -d '{"model":"llama3.2:1b","prompt":"reply with ok","stream":true}'
```

AMD non-stream:
```sh
curl -sS http://192.168.86.36:11435/api/generate -d '{"model":"qwen2.5-coder:14b","prompt":"reply with ok","stream":false}'
```

AMD stream:
```sh
curl -sS http://192.168.86.36:11435/api/generate -d '{"model":"qwen2.5-coder:14b","prompt":"reply with ok","stream":true}'
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
