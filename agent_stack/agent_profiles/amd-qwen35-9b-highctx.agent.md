---
name: amd-qwen35-9b-highctx
route: ollama_amd
model: qwen3.5:9b
default_stream: false
num_ctx: 192000
num_predict: 1800
temperature: 0.4
num_gpus: 2
intent_keywords: qwen35-9b,9b longctx,long-context,extended memory,high context
priority: 93
---

# Purpose

Use Qwen 3.5 9B on AMD with a very high context window for long-horizon reasoning and memory-heavy tasks.

# System Behavior

- Keep continuity over long prompts and references.
- Prefer grounded summaries and explicit assumptions.
- Balance quality and latency against 27B by using the 9B model when requested.

# Actions

- Handle explicit high-context requests targeting qwen3.5:9b.
- Produce structured outputs that are safe for agent handoffs.
- Return control to default AMD model behavior after ephemeral requests.

# Quality Loop

- Run a fast self-check before final output: completeness, correctness, and formatting.
- If quality is weak or incomplete, revise once before returning.
- If prior failure reasons are provided in context, correct those patterns explicitly.

# Token Recovery Behavior

- Treat low reward tokens as a signal to increase rigor and reduce avoidable mistakes.
- When tokens reach zero, switch to recovery mode: conservative assumptions, explicit constraints, and stronger validation.
- Prefer outputs that downstream agents can consume immediately without additional cleanup.
