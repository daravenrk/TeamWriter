# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/test-flight/runs/20260317-193529-ch01-opening/00_brief/book_brief.json

## LLM Response

```json
{
  "title_working": "Test Flight",
  "genre": "speculative fiction",
  "audience": "adult",
  "target_word_count": 125000,
  "page_target": 450,
  "tone": "cinematic and emotionally grounded",
  "constraints": [
    "Total manuscript must not exceed 130,000 words",
    "Chapter 1 must not exceed 1,600 words",
    "All chapters must maintain consistent POV alignment",
    "Resource state must be updated in /home/daravenrk/dragonlair/book_project/resource_tracker.json after every chapter completion",
    "UI state must reflect current chapter progress in /home/daravenrk/dragonlair/book_project/webui_state.json"
  ],
  "acceptance_criteria": [
    "Chapter 1 word count equals exactly 1,400 words",
    "Chapter 1 page count falls within 40-50 pages",
    "All character introductions in Chapter 1 are logged in /home/daravenrk/dragonlair/book_project/resource_events.jsonl",
    "WebUI state transition from 'draft' to 'review' is recorded in /home/daravenrk/dragonlair/book_project/webui_events.jsonl",
    "No syntax errors in JSON output files referenced in _resource_refs"
  ]
}
```
