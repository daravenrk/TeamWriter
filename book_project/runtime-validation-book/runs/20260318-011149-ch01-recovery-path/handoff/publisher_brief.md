# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-011149-ch01-recovery-path/00_brief/book_brief.json
- Recovered: False

## LLM Response

```json
{
  "title_working": "runtime-validation-book-ch01-recovery-path",
  "genre": "techno-thriller",
  "audience": "adult",
  "target_word_count": 2000,
  "page_target": 12,
  "tone": "clear and grounded",
  "constraints": [
    "Output must be valid JSON only.",
    "All required keys must be present.",
    "Constraints and acceptance_criteria must be concrete lists.",
    "Chapter word count must not exceed 450.",
    "Content must align with premise: systems engineer verifying resilient orchestration pipeline under stress.",
    "Tone must remain clear and grounded, avoiding excessive jargon without context.",
    "Resource state must be respected: mode is 'normal', pressure_mode is inactive, queue has 1 running task.",
    "Agent states are 'idle' for ollama_amd and ollama_nvidia; no active inference required for this chapter.",
    "Handoff directory: /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-011149-ch01-recovery-path/handoff",
    "Journal path: /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-011149-ch01-recovery-path/run_journal.jsonl",
    "Resource references must be cited as: /home/daravenrk/dragonlair/book_project/resource_tracker.json, /home/daravenrk/dragonlair/book_project/resource_events.jsonl, /home/daravenrk/dragonlair/book_project/webui_state.json, /home/daravenrk/dragonlair/book_project/webui_events.jsonl"
  ],
  "acceptance_criteria": [
    "JSON output contains all 8 required keys.",
    "Chapter word count is between 400 and 450 words.",
    "Section goal is met: progression through all gates is demonstrated.",
    "Complete section is produced with no truncation.",
    "Tone analysis confirms clarity and grounding in systems engineering context.",
    "No narrative analysis or prose outside the JSON structure.",
    "Constraints list contains at least 10 specific, measurable items.",
    "Acceptance criteria list contains at least 8 specific, measurable items.",
    "Resource paths are explicitly referenced in constraints.",
    "Current agent states (idle) and queue status (1 running) are acknowledged in constraints."
  ]
}
```
