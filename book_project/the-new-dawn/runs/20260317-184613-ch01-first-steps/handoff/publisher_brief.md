# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/the-new-dawn/runs/20260317-184613-ch01-first-steps/00_brief/book_brief.json

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
    "Setting descriptions must establish sensory details (sight, sound, temperature).",
    "No exposition dumps; all information must be revealed through action or dialogue.",
    "Resource tracker must be updated with chapter completion status.",
    "Web UI state must reflect chapter completion before proceeding."
  ],
  "acceptance_criteria": [
    "Final word count falls within the 1200-1600 range.",
    "Chapter includes at least 3 distinct characters with defined traits.",
    "Setting description contains at least 5 sensory details.",
    "No paragraphs exceed 150 words.",
    "Resource tracker JSON at /home/daravenrk/dragonlair/book_project/resource_tracker.json shows 'status': 'completed' for chapter 1.",
    "Web UI state JSON at /home/daravenrk/dragonlair/book_project/webui_state.json contains 'current_chapter': 1.",
    "No errors logged in /home/daravenrk/dragonlair/book_project/resource_events.jsonl during generation."
  ]
}
```
