# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-013407-ch01-recovery-path/00_brief/book_brief.json
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
    "Constraints and acceptance_criteria must be concrete, measurable lists.",
    "No prose outside the JSON structure.",
    "Chapter word count must not exceed 450 words.",
    "Content must demonstrate progression through all gates.",
    "Content must produce a complete section on the Recovery Path."
  ],
  "acceptance_criteria": [
    "JSON validity check passes.",
    "All required fields (title_working, genre, audience, target_word_count, page_target, tone, constraints, acceptance_criteria) are present.",
    "Constraints list contains at least 3 specific operational rules.",
    "Acceptance criteria list contains at least 3 measurable validation points.",
    "Chapter word count is <= 450.",
    "Section explicitly covers 'Recovery Path' progression.",
    "Tone remains clear and grounded throughout.",
    "Resource paths referenced in constraints match provided input paths."
  ]
}
```
