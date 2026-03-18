# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: book_project/test-book/runs/20260317-194134-ch01-opening-scene/00_brief/book_brief.json

## LLM Response

```json
{
  "title_working": "Test Book",
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
    "Word count for Chapter 1 is exactly 1,400 words",
    "Chapter 1 introduces all primary characters within the first 500 words",
    "Setting description is complete and sensory-rich",
    "Resource tracker JSON contains a new entry for Chapter 1 completion",
    "WebUI state JSON reflects 'Chapter 1 Complete' status",
    "No markdown or prose text is present in the output"
  ]
}
```
