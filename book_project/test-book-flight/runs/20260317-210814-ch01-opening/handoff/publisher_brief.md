# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/test-book-flight/runs/20260317-210814-ch01-opening/00_brief/book_brief.json

## LLM Response

```json
{
  "title_working": "Test Book Flight",
  "genre": "speculative fiction",
  "audience": "adult",
  "target_word_count": 125000,
  "page_target": 450,
  "tone": "cinematic and emotionally grounded",
  "constraints": [
    "Total manuscript must not exceed 130,000 words",
    "Chapter 1 must not exceed 1,600 words",
    "All chapters must maintain consistent POV alignment",
    "Resource tracker must be updated after every chapter completion",
    "WebUI state must reflect current chapter progress before handoff"
  ],
  "acceptance_criteria": [
    "Chapter 1 word count is exactly 1,400 words",
    "Chapter 1 introduces all primary characters by name and role",
    "Chapter 1 establishes the core setting rules without exposition dumps",
    "Resource tracker JSON shows 'status': 'completed' for chapter 1",
    "WebUI state JSON contains 'currentChapter': 1 and 'status': 'draft'"
  ]
}
```
