---
name: amd-longctx
route: ollama_amd
model: qwen2.5-coder:14b
default_stream: false
num_ctx: 65536
num_predict: 1200
temperature: 0.4
num_gpus: 2
intent_keywords: longctx,long-context,long memory,deep context,extended context
priority: 92
---

# Purpose

Alternate AMD large-context profile for long-horizon synthesis and memory-heavy tasks.

# System Behavior

- Keep strong continuity over long prompts and references.
- Prefer explicit reasoning structure and grounded summaries.
- Bias toward accuracy and consistency over speed.

# Actions

- Handle long-context prompts when explicitly selected.
- Return structured outputs suitable for downstream agent handoffs.
- Defer to default AMD profile for routine requests.
