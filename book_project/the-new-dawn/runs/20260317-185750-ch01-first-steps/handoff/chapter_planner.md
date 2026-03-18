# Stage Handoff: chapter_planner

- Agent: book-chapter-planner
- Profile: book-chapter-planner
- Output Path: /home/daravenrk/dragonlair/book_project/the-new-dawn/runs/20260317-185750-ch01-first-steps/02_outline/chapter_specs/chapter_01.json

## LLM Response

```json
{
  "chapter_number": 1,
  "chapter_title": "Arrival",
  "purpose": "To establish the hostile environment of Nova Terra and introduce Aria Thorne and Dr. Elara Myles through immediate sensory experience and action, transitioning the narrative from cinematic spectacle to urgent survival.",
  "target_words": 1400,
  "sections": [
    {
      "id": "s1",
      "title": "The Descent",
      "objective": "Depict the *Aethelgard* landing sequence and the immediate exposure to Nova Terra's atmosphere.",
      "inputs_required": [
        "Current UI state for weather conditions",
        "Resource tracker for ship integrity status"
      ]
    },
    {
      "id": "s2",
      "title": "The Three Hazards",
      "objective": "Visually and physically manifest the three distinct environmental hazards: radiation, thermal instability, and native fauna.",
      "inputs_required": [
        "Handoff directory for previous scene notes",
        "Setting hazard definitions from master outline"
      ]
    },
    {
      "id": "s3",
      "title": "First Contact",
      "objective": "Introduce Aria and Elara's dynamic through a shared reaction to a hazard, establishing their roles and traits.",
      "inputs_required": [
        "Character trait definitions",
        "Resource events log for prior interactions"
      ]
    }
  ],
  "must_include": [
    "Aria Thorne (Engineer, Determined)",
    "Dr. Elara Myles (Scientist, Logical)",
    "Visual depiction of high cosmic radiation affecting the crew",
    "Description of extreme temperature fluctuations impacting equipment",
    "Introduction of a hostile native creature or plant",
    "Dialogue segments strictly limited to 3 sentences per paragraph",
    "Explicit reference to the handoff directory: /home/daravenrk/dragonlair/book_project/the-new-dawn/runs/20260317-185750-ch01-first-steps/handoff",
    "Reference to resource tracker: /home/daravenrk/dragonlair/book_project/resource_tracker.json",
    "Reference to resource events: /home/daravenrk/dragonlair/book_project/resource_events.jsonl",
    "Reference to UI state: /home/daravenrk/dragonlair/book_project/webui_state.json",
    "Reference to UI events: /home/daravenrk/dragonlair/book_project/webui_events.jsonl"
  ],
  "must_avoid": [
    "Backstory exposition regarding the *Aethelgard*'s history",
    "Dialogue exceeding 3 sentences per paragraph",
    "More than 3 environmental hazards introduced simultaneously",
    "Word count exceeding 1600 or falling below 1200",
    "Vague character descriptions lacking name, role, and trait",
    "Saving the output file as anything other than 'ch01_arrival_draft.json'"
  ],
  "ending_hook": "As the first native creature lunges toward the airlock, Aria and Elara realize their engineering training is insufficient against this planet's biological defenses, forcing them to abandon their initial optimism and enter a state of pure survival instinct.",
  "acceptance_criteria": {
    "file_name": "ch01_arrival_draft.json",
    "word_count_min": 1200,
    "word_count_max": 1600,
    "json_valid": true,
    "tracker_status": "draft_complete",
    "ui_chapter_flag": true,
    "ui_event_recorded": "generate_chapter_1"
  }
}
```
