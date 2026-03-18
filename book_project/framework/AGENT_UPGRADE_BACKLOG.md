# Agent Upgrade Backlog (Book Flow)

Date: 2026-03-18

## Purpose
Keep agent behavior aligned to the book framework, chapter momentum, and continuity metadata so runs keep progressing without structural drift.

## Immediate Improvements

1. Add schema guardrails for every stage output.
- Define required keys per stage.
- Reject and retry outputs missing mandatory fields.

2. Add framework skeleton completeness checks.
- Require design framework fields (acts, thesis, premise, arc milestones) before drafting.
- Block progression when framework is incomplete.

3. Add arc tracker consistency checks.
- Compare chapter events against active story and character arcs.
- Flag unresolved loops that stop progressing for multiple chapters.

4. Add handoff expectation packets.
- Every stage output should include: next agent, expected input shape, acceptance criteria.
- Persist to `framework/agent_context_status.jsonl`.

5. Add continuity confidence score.
- Compute confidence score from canon consistency, arc updates, timeline integrity.
- Require minimum threshold prior to publisher stage.

6. Add run-level planning heartbeat.
- Persist chapter-level progress summary after each stage.
- Keep `framework/progress_index.json` current with blockers and next actions.

## Suggested Data Contracts

### framework_skeleton.json
- book_id
- thesis
- premise
- act_structure
- chapter_milestones
- narrative_constraints
- update_history

### arc_tracker.json
- story_arcs[]: name, status, last_progress_chapter, next_required_beat
- character_arcs[]: character, current_state, pressure, expected_transition
- open_loops[]: loop, introduced_in, urgency, target_resolution
- chapter_progress[]: chapter, section, achieved_beats, missing_beats

### progress_index.json
- current_stage
- completed_stages[]
- blocked_stages[]
- active_agent
- next_agent
- expected_output_schema
- updated_at

### agent_context_status.jsonl
- timestamp
- agent
- stage
- status
- expectation
- artifact_path
- notes

## Later Enhancements

1. Add auto-repair prompt templates for common gate failures.
2. Add chapter pacing model (slow/medium/fast) tied to arc urgency.
3. Add contradiction detector against canon and timeline snapshots.
4. Add carry-forward memory budget to cap context bloat while preserving essentials.
5. Add pre-publisher final continuity assembly with unresolved-loop warnings.

## Orchestrator + Ollama Flow Enhancements

1. Add profile-scored routing instead of first keyword match.
- Score with `intent_keywords`, `priority`, quality history, and reward token state.

2. Use full profile metadata for execution planning.
- Map profile sections (persona/response-style/quality-loop/token-recovery) to structured prompt directives.
- Plan `num_ctx`, `num_predict`, `temperature`, and `think` dynamically by stage.

3. Add failure-memory-guided retries.
- Pull recent quality failure reasons and inject explicit corrective constraints on retry calls.

4. Add route/model circuit breaker + adaptive backoff.
- Quarantine degraded route-model pairs using rolling error/latency windows.

5. Add stage-aware stream mode policy.
- Default non-streamed strict JSON stages, optional streamed narrative stages.

6. Add hedge calls for latency-critical stages.
- Fire secondary route/model after timeout threshold and cancel losing call.

7. Add Ollama call observability payload.
- Capture TTFT, total latency, tokens/sec, retries, fallback hops, and context size estimate per stage.

8. Replace hardcoded fallback map with profile-defined fallback ladders.
- Keep fallback definitions in profile metadata and validate against expected output schema.

## Ollama Analytics + Economics Enhancements

1. Add a per-call Ollama run ledger with correlation IDs.
- Persist `call_id`, stage, profile, route, endpoint, model, latency, eval counters, and quality outcome so book-flow settlement can join back to the originating run.

2. Add `/metrics` export and bounded Prometheus label design.
- Publish route/model/profile counters, latency histograms, inflight gauges, fallback counts, and quality results without unbounded cardinality.

3. Install Prometheus, Grafana, Loki, and Promtail for the stack.
- Keep scrape config, dashboard provisioning, and JSONL log shipping in versioned deployment artifacts.

4. Add AMD and NVIDIA GPU telemetry exporters.
- Correlate GPU utilization, VRAM pressure, and temperature with route/model latency and quality-pass analytics.

5. Add dashboard pack for endpoint health, model efficiency, profile economics, and stage diagnostics.
- Include alert thresholds for p95 latency spikes, degraded throughput, and repeated gate failures.

6. Add retention, redaction, and diagnostics sampling policy.
- Roll up old analytics logs, redact unnecessary full-text prompt content, and prevent verbose diagnostics from becoming the bottleneck.

7. Replace flat token deltas with spend-and-settle economics.
- Charge by endpoint/model/runtime at call start and settle payouts only when the stage passes quality gates.
