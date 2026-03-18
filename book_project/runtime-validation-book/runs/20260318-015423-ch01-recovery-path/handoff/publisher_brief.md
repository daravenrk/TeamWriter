# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-015423-ch01-recovery-path/00_brief/book_brief.json
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
    "Chapter must demonstrate progression through all gates in the orchestration pipeline.",
    "Chapter must produce a complete section titled 'Recovery Path'.",
    "Writer word count must not exceed 450 words.",
    "Content must align with the premise of a systems engineer verifying a resilient pipeline under stress.",
    "Tone must remain clear and grounded, avoiding excessive jargon without context.",
    "Resource state must reflect normal mode with 1 running agent (qwen3.5:9b) and 0 failed agents.",
    "Handoff directory must be: /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-015423-ch01-recovery-path/handoff",
    "Run journal must be logged to: /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-015423-ch01-recovery-path/run_journal.jsonl"
  ],
  "acceptance_criteria": [
    "Output contains exactly 450 words or fewer.",
    "Output explicitly references the recovery path progression.",
    "Output confirms completion of the 'Recovery Path' section goal.",
    "Output does not contain any prose outside the JSON structure.",
    "Output is valid JSON parseable by downstream agents.",
    "Output references resource paths: /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-015423-ch01-recovery-path/handoff and /home/daravenrk/dragonlair/book_project/runtime-validation-book/runs/20260318-015423-ch01-recovery-path/run_journal.jsonl"
  ]
}
```
