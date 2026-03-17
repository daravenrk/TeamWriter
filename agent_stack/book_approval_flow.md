# Book Approval and Validation Flow

## 1. Initial Prompt Intake
- Trigger a "flock" of agents:
  - Topic Checker: Validate topic suitability, originality, and alignment with project goals.
  - Chapter Structure Planner: Propose initial chapter breakdown and logical flow.
  - Story Overview Agent: Summarize premise, genre, tone, and key arcs for storage and review.
- Store all outputs for review by human or automated reviewer.

## 2. Idea Approval Steps
- Human or automated reviewer approves or requests revision on:
  - Topic suitability
  - Chapter structure
  - Story overview
- Only after approval does the book proceed to the next stage.

## 3. Book Writing Steps (Post-Approval)
- Research Agent: Gathers facts, worldbuilding, and references.
- Architect Agent: Produces master outline and book skeleton.
- Chapter Planner: Expands outline into detailed chapter specs.
- Canon Agent: Maintains long-term memory and continuity.
- Writer Agent: Drafts sections per approved specs.
- Editor/Reviewers: Validate, revise, and approve each section.

## 4. Validation and Quality Gates
- Each major output (chapter, section, review) passes through:
  - Automated rubric checks (structure, coherence, style, etc.)
  - Reviewer agent(s) for actionable feedback
  - Human review (optional, for critical stages)
- Only outputs passing all gates move forward.

## 5. Explicit Approval Before Chapter Merge
- Before a section is merged into a chapter:
  - Reviewer agent must explicitly approve the section.
  - Section must pass a clean merge check with the previous chapter/section.
  - Continuity agent must validate that the merge does not introduce contradictions, timeline errors, or broken arcs.
  - Only after all checks and approvals is the section merged and accepted as part of the manuscript.

## 6. Final Approval and Export
- Publisher Agent: Final review and approval for publication.
- Proofreader Agent: Final polish and error correction.
- Export and archive all artifacts, logs, and approvals.

---

This flow ensures every book idea is vetted, structured, and validated at each stage, with clear approval and review checkpoints for quality and alignment.