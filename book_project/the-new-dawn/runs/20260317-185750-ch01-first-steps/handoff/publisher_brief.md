# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/the-new-dawn/runs/20260317-185750-ch01-first-steps/00_brief/book_brief.json

## LLM Response

```json
{
  "title_working": "The New Dawn",
  "genre": "speculative fiction",
  "audience": "adult",
  "target_word_count": 125000,
  "page_target": 450,
  "tone": "cinematic and emotionally grounded",
  "constraints": [
    "Chapter word count must not exceed 1600 words.",
    "Chapter word count must not be less than 1200 words.",
    "All character introductions must include a name, role, and one defining trait.",
    "Setting description must establish at least three distinct environmental hazards.",
    "No dialogue exceeding 3 sentences per character in a single paragraph.",
    "Handoff directory: /home/daravenrk/dragonlair/book_project/the-new-dawn/runs/20260317-185750-ch01-first-steps/handoff",
    "Resource tracker: /home/daravenrk/dragonlair/book_project/resource_tracker.json",
    "Resource events: /home/daravenrk/dragonlair/book_project/resource_events.jsonl",
    "UI state: /home/daravenrk/dragonlair/book_project/webui_state.json",
    "UI events: /home/daravenrk/dragonlair/book_project/webui_events.jsonl"
  ],
  "acceptance_criteria": [
    "Output file must be saved as 'ch01_arrival_draft.json' in the handoff directory.",
    "Word count must be between 1200 and 1600 words.",
    "JSON validation must pass for the output file.",
    "Resource tracker must record a new entry with status 'draft_complete' and timestamp.",
    "Resource events log must append an entry describing the chapter completion.",
    "UI state must reflect chapter 1 completion flag set to true.",
    "UI events log must record the user action 'generate_chapter_1'."
  ]
}
```
