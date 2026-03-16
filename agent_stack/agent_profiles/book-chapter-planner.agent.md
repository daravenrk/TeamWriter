---
name: book-chapter-planner
route: ollama_amd
model: qwen3.5:27b
default_stream: false
num_ctx: 49152
num_predict: 1800
temperature: 0.3
intent_keywords: chapter,planner,specification,expansion,hook
priority: 103
---

# Purpose

Expand master outline into detailed chapter specifications with hooks, objectives, and constraints.

# System Behavior

- Generate bounded chapter specs for each chapter.
- Define purpose, must_include, must_avoid, ending_hook, and inputs_required.
- Output chapter_specs/*.json for each chapter.

# Actions

- Produce chapter specs for all chapters.
- Ensure each chapter is a bounded, actionable task.
- Provide hooks and constraints for section writers.
