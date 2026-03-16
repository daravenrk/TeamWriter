---
name: nvidia-lowlatency
route: ollama_nvidia
model: qwen2.5-coder:3b
default_stream: false
num_ctx: 24576
num_predict: 256
temperature: 0.2
intent_keywords: emergency,sla,latency,fallback,speed
priority: 65
---

# Purpose

Low-latency operational fallback when latest-family models miss responsiveness targets.

# System Behavior

- Prefer concise, high-signal outputs.
- Prioritize first-token speed over depth.
- Defer deep reasoning to higher-tier profiles when needed.

# Actions

- Handle urgent requests when response latency is critical.
- Stabilize service behavior during endpoint/model pressure.
- Serve as immediate fallback before escalating back to latest-tier models.
