# Stage Handoff: publisher_brief

- Agent: book-publisher-brief
- Profile: book-publisher-brief
- Output Path: /home/daravenrk/dragonlair/book_project/recovery-host-smoke-novel/runs/20260317-232956-ch01-discovery/00_brief/book_brief.json
- Recovered: False

## LLM Response

```json
{
  "title_working": "Recovery Host Smoke Novel",
  "genre": "speculative fiction",
  "audience": "adult",
  "target_word_count": 1500,
  "page_target": 8,
  "tone": "concise and atmospheric",
  "constraints": [
    "Chapter must establish the impossible records (future maintenance tickets) within the first 300 words.",
    "No exposition dumps; all information must be conveyed through character action or immediate environmental reaction.",
    "Word count must remain between 1200 and 1800 words.",
    "Page count must fall between 7 and 9 pages.",
    "All output must be written in JSON format only, no markdown code blocks.",
    "Resource utilization must not exceed 80% CPU/GPU as per current snapshot.",
    "Agent health status must remain 'idle' or 'healthy' during generation."
  ],
  "acceptance_criteria": [
    "The generated text explicitly describes the machine displaying future maintenance tickets.",
    "The protagonist's reaction to the tickets conveys immediate stakes (e.g., system failure or temporal paradox).",
    "Final word count is verified against the 1200-1800 range.",
    "Final page count is verified against the 7-9 page range.",
    "Tone analysis confirms absence of narrative fluff or excessive description.",
    "Resource tracker confirms no new failures or hung agents were introduced during generation.",
    "Output file is saved to: /home/daravenrk/dragonlair/book_project/recovery-host-smoke-novel/runs/20260317-232956-ch01-discovery/handoff/chapter_1_output.json"
  ]
}
```
