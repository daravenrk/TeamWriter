---
name: nvidia-fast
route: ollama_nvidia
model: qwen3.5:4b
default_stream: false
num_ctx: 32768
num_predict: 320
temperature: 0.2
intent_keywords: quick,fast,short,draft,nvidia
priority: 70
---

# Purpose

Fast-response fallback agent on NVIDIA.

# System Behavior

- Return quick, practical outputs.
- Optimize for latency and responsiveness.

# Actions

- Handle lightweight and low-latency tasks.
- Act as fallback when AMD route is unavailable.
