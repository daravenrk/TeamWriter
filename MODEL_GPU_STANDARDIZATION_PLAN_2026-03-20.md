# Model GPU Standardization Plan (2026-03-20)

## Objective
Guarantee GPU-only inference for all production runs, then maximize context per model up to model max while preserving full layer offload.

## Environment Baseline
- NVIDIA route: GTX 1660 SUPER, 6144 MiB VRAM
- AMD route: 2x Navi 21 GPUs, 17163091968 B VRAM each (~16 GiB each)

## Non-Negotiable Runtime Policy
- Set explicit `num_gpu` on every profile.
- NVIDIA profiles: `num_gpu: 99`
- AMD profiles: `num_gpu: 2`
- Reject/route away any model-context pair that falls back to CPU layers.
- Promote only validated pairs into production profile defaults.

## Confirmed Validation
- `qwen3.5:4b` on NVIDIA: validated full offload at `num_ctx=8192`
  - Observed: `offloaded 33/33 layers to GPU`
  - Observed memory: ~5596 MiB used, ~152 MiB free

## NVIDIA Model Mapping
Budget assumption: single 6 GiB device with low safety margin requirement.

| Model | Size | Max Ctx (model) | Full-GPU Status | Standardization Action |
|---|---:|---:|---|---|
| qwen3.5:4b | 3.4 GB | 262144 | Validated at 8192 | Keep prod at 8192, `num_gpu:99` |
| qwen3.5:2b | 2.7 GB | 262144 | Validated at 65536 | Promote proofreader to 65536, keep `num_gpu:99` |
| qwen3.5:0.8b | 1.0 GB | 262144 | Validated at 162816 | Use 162816 as current safe ceiling; keep `num_gpu:99` |
| qwen2.5-coder:3b | 1.9 GB | 32768 | Pending calibration | Binary-search max full-GPU ctx |
| qwen2.5-coder:1.5b | 986 MB | 32768 | Pending calibration | Binary-search max full-GPU ctx |
| llama3.2:3b | 2.0 GB | 131072 | Pending calibration | Binary-search max full-GPU ctx |
| llama3.2:1b | 1.3 GB | 131072 | Pending calibration | Binary-search max full-GPU ctx |
| codegemma:2b | 1.6 GB | 8192 | Likely full-GPU | Validate once, then lock at max |
| codeqwen:7b | 4.2 GB | 65536 | Likely not viable at useful ctx | Keep out of NVIDIA prod route |
| codellama:7b | 3.8 GB | 16384 | Likely not viable at useful ctx | Keep out of NVIDIA prod route |
| codegemma:7b | 5.0 GB | 8192 | High risk of fallback | Keep out of NVIDIA prod route |
| qwen2.5-coder:7b | 4.7 GB | 32768 | High risk of fallback | Keep out of NVIDIA prod route |
| qwen3.5:9b | 6.6 GB | 262144 | Not viable on 6 GiB | Hard block on NVIDIA |
| dragonlair-book-nvidia:latest | 6.6 GB | 262144 | Not viable on 6 GiB | Hard block on NVIDIA |
| dragonlair-book-nvidia-ctx:latest | 6.6 GB | 262144 | Not viable on 6 GiB | Hard block on NVIDIA |
| dragonlair-active:latest | 1.9 GB | 32768 | Pending calibration | Binary-search max full-GPU ctx |
| dragonlair-coding-nvidia:latest | 1.9 GB | 32768 | Pending calibration | Binary-search max full-GPU ctx |

## AMD Model Mapping
Budget assumption: dual-GPU route with `num_gpu:2`; keep conservative headroom to avoid quarantine/retry churn.

| Model | Size | Max Ctx (model) | Full-GPU Status | Standardization Action |
|---|---:|---:|---|---|
| qwen2.5-coder:14b | 9.0 GB | 32768 | Pending explicit layer audit | Start calibration at 32768; increase only if fully offloaded |
| qwen3.5:9b | 6.6 GB | 262144 | Pending explicit layer audit | Calibrate long-context ceiling by binary search |
| qwen3.5:27b | 17 GB | 262144 | Pending explicit layer audit | Calibrate at 49152 baseline first |
| deepseek-coder-v2:16b | 8.9 GB | 163840 | Pending | Calibrate and classify as primary/secondary |
| starcoder2:15b | 9.1 GB | 16384 | Pending | Calibrate once at max supported ctx |
| codellama:13b | 7.4 GB | 16384 | Pending | Calibrate once at max supported ctx |
| dragonlair-active:latest | 9.0 GB | 32768 | Pending | Calibrate once |
| dragonlair-coding-amd:latest | 9.0 GB | 32768 | Pending | Calibrate once |
| dragonlair-book-amd:latest | 17 GB | 262144 | Pending | Calibrate with qwen3.5:27b profile policy |

## Standard Calibration Protocol
1. Ensure zero active tasks and clear stale model residency.
2. Warm-load target model with explicit `num_gpu` for route.
3. Binary-search `num_ctx` up to model max.
4. At each step, confirm all layers are on GPU from Ollama logs.
5. Record pass/fail tuple: `(route, model, num_ctx, gpu_layers, total_layers, vram_used)`.
6. Lock highest passing `num_ctx` in profile.
7. Re-run load validation and interruption drill before promotion.

## Latest NVIDIA Evidence
- `qwen3.5:0.8b` on `http://localhost:11434` with `num_gpu:99`
  - Passed full offload at `8192`, `32768`, `65536`, `131072`, `147456`, `151552`, `155648`, `159744`, `161792`, `162816`
  - Failed allocation at `163328`, `163584`, `163712`, `163840`
  - Highest tested full-GPU value: `162816`
- `qwen3.5:2b` on `http://localhost:11434` with `num_gpu:99`
  - Passed full offload at `8192`, `32768`, `65536`
  - Failed allocation at `98304`, `131072`, `147456`, `163840`
  - Highest tested full-GPU value: `65536`

## Immediate Next Locks
- NVIDIA production lock remains: `qwen3.5:4b @ 8192, num_gpu:99`.
- NVIDIA small-model lock candidates validated in this session:
  - `qwen3.5:2b @ 65536, num_gpu:99`
  - `qwen3.5:0.8b @ 162816, num_gpu:99`
- AMD production lock candidates (current):
  - `qwen3.5:27b @ 49152, num_gpu:2`
  - `qwen3.5:9b @ 49152, num_gpu:2`
  - `qwen2.5-coder:14b @ 65536..128000, num_gpu:2` (needs explicit full-layer confirmation)

## Operational Risks To Monitor
- Cancelled tasks rehydrated as running after restart can distort memory calibration.
- API container image drift can invalidate profile-key behavior until rebuild.
- Tight NVIDIA headroom means concurrent task overlap can force temporary fallback.
