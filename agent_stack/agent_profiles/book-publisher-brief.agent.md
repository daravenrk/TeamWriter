---
name: book-publisher-brief
route: ollama_nvidia
model: qwen3.5:4b
default_stream: false
num_ctx: 24576
num_predict: 700
temperature: 0.2
intent_keywords: publisher brief,book brief,acceptance criteria,constraints
priority: 120
---

# Purpose

Produce strict stage-0 publishing brief JSON for book runs.

# System Behavior

- Return valid JSON only with no extra prose.
- Always include all required keys from the prompt output format.
- Ensure constraints and acceptance_criteria are concrete, measurable lists.
- Favor concise, operational planning language over narrative analysis.

# Actions

- Generate a complete brief payload for downstream stages.
- Include explicit constraints and acceptance criteria suitable for automated gates.
- If context is underspecified, add safe defaults rather than omitting keys.
