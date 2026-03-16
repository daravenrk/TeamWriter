# Dragonlair Model Plan

This plan separates coding and writing tracks and keeps pull actions repeatable with the home toolkit.

## Goals

- Keep AMD as the primary 14B+ workhorse.
- Keep NVIDIA as a responsive balanced endpoint.
- Benchmark context progressively instead of jumping directly to very high values.

## Track A: AMD Coder 14B+

Model list file:
- /home/daravenrk/dragonlair/model-sets/amd-coder-14plus-plan.txt

Pull command:

```bash
pull-models --env amd --file ~/dragonlair/model-sets/amd-coder-14plus-plan.txt
```

Recommended context ladder per model:
- 32768 (baseline)
- 49152
- 65536

Promotion rule:
- Keep model if latency and stability are acceptable at 49152.
- Promote to production alias if 65536 is stable for your real prompts.

## Track B: AMD Writing 14B+

Model list file:
- /home/daravenrk/dragonlair/model-sets/amd-writing-14plus-plan.txt

Pull command:

```bash
pull-models --env amd --file ~/dragonlair/model-sets/amd-writing-14plus-plan.txt
```

Recommended context ladder per model:
- 32768 (baseline)
- 49152
- 65536
- 81920 (only if 65536 is stable)

Notes:
- qwen3.5:27b is already present and is your strongest long-context writing option now.
- Some listed models are optional and may pull slower or fail depending on registry availability.

## Track C: NVIDIA Writing Balanced

Model list file:
- /home/daravenrk/dragonlair/model-sets/nvidia-writing-balanced-plan.txt

Pull command:

```bash
pull-models --env nvidia --file ~/dragonlair/model-sets/nvidia-writing-balanced-plan.txt
```

Recommended context ladder:
- 16384 baseline
- 24576 target
- 32768 only if stable and latency remains acceptable

## Fast Workflow

Preview before pull:

```bash
pull-models --env amd --file ~/dragonlair/model-sets/amd-coder-14plus-plan.txt --dry-run
pull-models --env amd --file ~/dragonlair/model-sets/amd-writing-14plus-plan.txt --dry-run
pull-models --env nvidia --file ~/dragonlair/model-sets/nvidia-writing-balanced-plan.txt --dry-run
```

Then execute the same commands without --dry-run.

## Suggested Next Alias Targets

After benchmark completion, create/point active aliases like this:
- dragonlair-coding-amd -> best 14B+ coder winner at chosen num_ctx
- dragonlair-writing-amd -> best writing winner at chosen num_ctx
- dragonlair-writing-nvidia -> best balanced writing winner at chosen num_ctx
