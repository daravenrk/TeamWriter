# Dragonlair User Guide

## System Overview

- **Architecture:** Docker-based, portable across any Linux system with Docker, Docker Compose, and the required GPU stack (NVIDIA CUDA or AMD ROCm).
- **Endpoints:**
  - AMD: `http://127.0.0.1:11435` (container: `ollama_amd`)
  - NVIDIA: `http://127.0.0.1:11434` (container: `ollama_nvidia`)
- **Agent API/UI:** `http://127.0.0.1:11888` (container: `dragonlair_agent_stack`)
- **Fetcher service:** `http://127.0.0.1:11999` (container: `fetcher`)
- **Toolkit:** All scripts and controls are in `/home/daravenrk/dragonlair/bin`
- **Model plans and lists:** `/home/daravenrk/dragonlair/model-sets/`

## Port Matrix (Current)

- `11434` -> `ollama_nvidia` (NVIDIA Ollama endpoint)
- `11435` -> `ollama_amd` (AMD Ollama endpoint)
- `11888` -> `dragonlair_agent_stack` (API + Web UI)
- `11999` -> `fetcher` (research fetch service)

Notes:
- VS Code "Ports" can show auto-forwarded entries that are not currently listening processes.
- Confirm real listeners with:

```sh
ss -ltnp
```

## Control & Usage

### Quick Start

1. Add toolkit to your PATH:
   ```sh
   export PATH="$HOME/dragonlair/bin:$PATH"
   ```
   To persist:
   ```sh
   echo 'export PATH="$HOME/dragonlair/bin:$PATH"' >> ~/.bashrc
   source ~/.bashrc
   ```

2. Preview model pulls (no downloads):
   ```sh
   pull-models --dry-run
   pull-amd --dry-run
   pull-nvidia --dry-run
   ```

3. Pull models:
   ```sh
   pull-models --env amd --file ~/dragonlair/model-sets/amd-coder-14plus-plan.txt
   pull-models --env amd --file ~/dragonlair/model-sets/amd-writing-14plus-plan.txt
   pull-models --env nvidia --file ~/dragonlair/model-sets/nvidia-writing-balanced-plan.txt
   ```

4. Chat/Ask:
   ```sh
   chat-amd
   chat-nvidia
   ask-amd "Explain event loops"
   ask-nvidia "Write a bash function"
   ```

### Planning & Benchmarking

- Model plans are in:
  - `/home/daravenrk/dragonlair/model-sets/amd-coder-14plus-plan.txt`
  - `/home/daravenrk/dragonlair/model-sets/amd-writing-14plus-plan.txt`
  - `/home/daravenrk/dragonlair/model-sets/nvidia-writing-balanced-plan.txt`
- Use `--dry-run` to preview, then run without it to pull.
- Benchmark context ladder: 32768, 49152, 65536 (AMD); 16384, 24576, 32768 (NVIDIA).

### Backup & Restore

- **Backup (no model data):**
  ```sh
  /home/daravenrk/dragonlair/bin/dragonlair_backup_nodata.sh
  ```
  - Backs up configs, scripts, and model lists to `daravenrk@192.168.86.34:/backups/dragonlair`
  - Excludes all model data for lightweight backups.

- **Restore:**
  - Use `rsync` to copy from the backup server to your system:
    ```sh
    rsync -avz daravenrk@192.168.86.34:/backups/dragonlair/opt/ai-stack/ /opt/ai-stack/
    rsync -avz daravenrk@192.168.86.34:/backups/dragonlair/home/daravenrk/dragonlair/ /home/daravenrk/dragonlair/
    ```
  - Reinstall Docker, Docker Compose, and GPU drivers as needed.
  - Pull models as needed using your model lists.

- **Portability:**  
  This system will run on any compatible Linux host with Docker, Compose, and the right GPU stack. Just restore configs/scripts, install dependencies, and pull models.

Preferred stack-level scripts:

```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_backup
/home/daravenrk/dragonlair/bin/dragonlair_stack_restore
```

Include model blobs only when needed:

```sh
/home/daravenrk/dragonlair/bin/dragonlair_stack_backup --with-models
/home/daravenrk/dragonlair/bin/dragonlair_stack_restore --with-models
```

Bare-metal backup for hardware-failure recovery:

 /home/daravenrk/dragonlair/bin/dragonlair_metal_backup

Recovery instructions are in:

 - /home/daravenrk/dragonlair/BARE_METAL_RECOVERY.md

## AMD Stack: Available Models

Currently available models on the AMD endpoint (`ollama_amd`):

- deepseek-coder-v2:16b
- starcoder2:15b
- codellama:13b
- dragonlair-active:latest
- dragonlair-coding-amd:latest
- qwen2.5-coder:14b
- dragonlair-book-amd:latest
- qwen3.5:27b

**Planned writing models (from amd-writing-14plus-plan.txt):**
- qwen3.5:27b
- qwen2.5:14b-instruct
- qwen2.5:32b-instruct
- gemma2:27b

**Planned coder models (from amd-coder-14plus-plan.txt):**
- qwen2.5-coder:14b
- codellama:13b
- starcoder2:15b
- deepseek-coder-v2:16b

## Diagrams

### System Architecture

```mermaid
graph TD
  User[User/Client] -->|HTTP API| AMD_Ollama[Ollama AMD (11435)]
  User[User/Client] -->|HTTP API| NVIDIA_Ollama[Ollama NVIDIA (11434)]
  AMD_Ollama -->|Models| AMD_Models[AMD Model Store]
  NVIDIA_Ollama -->|Models| NVIDIA_Models[NVIDIA Model Store]
  User -->|SSH/rsync| BackupServer[Backup Server (192.168.86.34)]
  subgraph Home Toolkit
    BinScripts[dragonlair/bin/*]
    ModelSets[dragonlair/model-sets/*]
    USAGE[USAGE.md]
    MODELPLAN[MODEL_PLAN.md]
  end
  User -->|Shell| BinScripts
  BinScripts -->|Pull/Chat/Ask| AMD_Ollama
  BinScripts -->|Pull/Chat/Ask| NVIDIA_Ollama
```

## Control Flow

1. User runs toolkit scripts (pull, chat, ask) from any shell.
2. Scripts interact with the correct Ollama endpoint/container.
3. Model lists and plans are editable and drive pulls/benchmarking.
4. Backups are performed via rsync to a remote server, excluding model data.
5. Restoration is a reverse rsync, followed by environment setup and model pulls.

## Context Planning Before Requests

Use the planner to estimate required context window before running a full model request:

```sh
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/plan-context \
  --prompt "Write a structured debate on local AI model orchestration and endpoint reliability." \
  --expected-output 900
```

Optional controls:

- `--profile amd-coder|amd-writer|nvidia-fast`
- `--history-tokens <N>`
- `--safety-ratio <float>`

The planner outputs `SUGGESTED_NUM_CTX`, which you can place in profile frontmatter (`num_ctx`) for that agent.

## Dockerized Agent Stack

### Research Agent Update (March 2026)
- The book-researcher agent now uses qwen3.5:14b (128k context window).
- Research agent pulls from news, Wikipedia, and the internet for up-to-date facts.
- Research output is more data-driven and includes structured dossiers and fact cards.

Start backend + frontend:

```sh
/home/daravenrk/dragonlair/bin/agent-stack-up
```

Open frontend:

- `http://127.0.0.1:11888`
- `http://<HOST_IP>:11888` from external systems on your LAN

Example on this host:

- `http://192.168.86.36:11888`

Stop and logs:

```sh
/home/daravenrk/dragonlair/bin/agent-stack-down
/home/daravenrk/dragonlair/bin/agent-stack-logs
```

CLI status/watch for subagents and task queue:

```sh
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl server-status
PYTHONPATH=/home/daravenrk/dragonlair /home/daravenrk/dragonlair/bin/agentctl server-watch --interval 1
```

---

For more details, see the files in `/home/daravenrk/dragonlair/` and `/opt/ai-stack/`.

Additional operations and backend autonomy notes are here:

- `/home/daravenrk/dragonlair/SYSTEM_NOTES_AND_AUTONOMY.md`
