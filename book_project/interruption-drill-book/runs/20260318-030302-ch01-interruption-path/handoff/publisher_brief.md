# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/interruption-drill-book/runs/20260318-030302-ch01-interruption-path/00_brief/book_brief.json
- Recovered: False

## LLM Response

```json
{
  "title_working": "Interruption Drill Book",
  "genre": "speculative fiction",
  "audience": "adult",
  "target_word_count": 125000,
  "page_target": 450,
  "tone": "cinematic and emotionally grounded",
  "constraints": [
    "Maintain strict continuity with established canon and timeline.",
    "Ensure Chapter 1 ('Recovery Drill Chapter') explicitly exercises interruption detection and resume behavior mechanics.",
    "Adhere to the 'cinematic and emotionally grounded' tone throughout.",
    "Respect the 125,000 word total count and 450-page target for the full manuscript.",
    "Integrate unresolved narrative loops from previous chapters without breaking character consistency.",
    "Utilize the provided framework files for arc tracking and skeleton validation."
  ],
  "acceptance_criteria": [
    "Output must be valid JSON containing all required keys.",
    "Chapter 1 content must demonstrate functional interruption detection logic within the narrative.",
    "Word count for Chapter 1 must fall within the 300-400 word range to stay on track for 125k total.",
    "No prose or markdown formatting outside the JSON structure.",
    "All constraints and acceptance criteria must be concrete and measurable.",
    "Continuity checks against /home/daravenrk/dragonlair/book_project/interruption-drill-book/framework/arc_tracker.json must pass before proceeding."
  ]
}
```
