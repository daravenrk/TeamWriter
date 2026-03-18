# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/the-new-dawn/runs/20260317-191737-ch01-first-steps/00_brief/book_brief.json

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
    "All character names must be consistent with the provided resource_tracker.json.",
    "Setting descriptions must align with the established world-building in resource_tracker.json.",
    "Output must be valid JSON only, no markdown code blocks or prose."
  ],
  "acceptance_criteria": [
    "Word count falls within the 1200-1600 range.",
    "Chapter title 'Arrival' is present in the output.",
    "Section title 'First Steps' is present in the output.",
    "The narrative establishes the interstellar colony setting clearly.",
    "At least two main characters are introduced with distinct motivations.",
    "The output file is saved to /home/daravenrk/dragonlair/book_project/the-new-dawn/runs/20260317-191737-ch01-first-steps/handoff/chapter_1_output.json.",
    "The resource_tracker.json is updated with the new chapter's metadata.",
    "The webui_state.json is updated to reflect completion of Chapter 1."
  ]
}
```
