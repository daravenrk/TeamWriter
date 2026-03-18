# Dragonlair Home Toolkit

All controls are available in:
- `/home/daravenrk/dragonlair/bin`

## Quick Start

1. Load tools into your shell PATH:

```bash
export PATH="$HOME/dragonlair/bin:$PATH"
```

To persist that for new terminals:

```bash
echo 'export PATH="$HOME/dragonlair/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

2. Preview pulls (no downloads):

```bash
pull-models --dry-run
pull-amd --dry-run
pull-nvidia --dry-run
```

## Pull Models

Main script:

```bash
pull-models [--env amd|nvidia] [--file <model-list-file>] [--dry-run]
```

Examples:

```bash
pull-models --env amd --file ~/dragonlair/model-sets/amd-coder-14b.txt
pull-models --env nvidia --file ~/dragonlair/model-sets/nvidia-coder.txt
```

Shortcuts:

```bash
pull-amd
pull-nvidia
```

### One-line default switching

Edit this file:
- `/home/daravenrk/dragonlair/model-sets/pull-models.env`

Change:
- `TARGET_ENV=amd` or `TARGET_ENV=nvidia`
- `MODEL_FILE=...`

Then run:

```bash
pull-models
```

## Chat / Ask

Interactive chat:

```bash
chat-amd
chat-nvidia
```

Single-question output:

```bash
ask-amd "Explain event loops in simple terms"
ask-nvidia "Write a bash function to parse args"
```

## Files You Edit Most

- `/home/daravenrk/dragonlair/model-sets/pull-models.env`
- `/home/daravenrk/dragonlair/model-sets/amd-coder-14b.txt`
- `/home/daravenrk/dragonlair/model-sets/nvidia-coder.txt`

## Planning Files

- `/home/daravenrk/dragonlair/MODEL_PLAN.md`
- `/home/daravenrk/dragonlair/model-sets/amd-coder-14plus-plan.txt`
- `/home/daravenrk/dragonlair/model-sets/amd-writing-14plus-plan.txt`
- `/home/daravenrk/dragonlair/model-sets/nvidia-writing-balanced-plan.txt`

Plan preview commands:

```bash
pull-models --env amd --file ~/dragonlair/model-sets/amd-coder-14plus-plan.txt --dry-run
pull-models --env amd --file ~/dragonlair/model-sets/amd-writing-14plus-plan.txt --dry-run
pull-models --env nvidia --file ~/dragonlair/model-sets/nvidia-writing-balanced-plan.txt --dry-run
```

## Notes

- AMD endpoint: `http://127.0.0.1:11435` (container `ollama_amd`)
- NVIDIA endpoint: `http://127.0.0.1:11434` (container `ollama_nvidia`)
- Exposure policy: keep `11434`, `11435`, and `11999` local-only; expose only `11888` when LAN access is explicitly needed.
- `pull-models` skips already installed models by default (`SKIP_EXISTING=1`).
