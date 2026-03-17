# AGENT_PLAN.md

## Objective
Build a fully autonomous backend agent control system for Dragonlair using md-defined behavior profiles, lock/triage safeguards, and an operator CLI with streaming support.

## Current Completed Baseline
- [x] Python class-based orchestrator + subagent stack
- [x] md profile behavior files with hot reload
- [x] Profile-driven route/model/options selection
- [x] Lock manager for edit safety + endpoint anti-spam
- [x] Hung/failure triage with quarantine + fallback
- [x] Context estimation preflight command
- [x] CLI control layer (`agentctl`) with streaming

## Phase 1: Control Plane Hardening
- [ ] Add profile lint command and strict schema checks
- [ ] Add max system-prompt size guardrails
- [ ] Add per-profile timeout and retry settings
- [ ] Add profile policy fields for route/model allowlists

## Phase 2: Autonomous Execution Engine
- [ ] Add persistent task queue (SQLite)
- [ ] Add worker loop for queued requests
- [ ] Add checkpointed task state transitions
- [ ] Add retry strategy and dead-letter handling

## Phase 3: Self-Healing
- [ ] Add endpoint watchdog probes and health scoring
- [ ] Add circuit breaker logic by route/profile
- [ ] Add dynamic fallback chains from policy
- [ ] Add auto cooldown expiry and requalification checks

## Phase 4: Operations + Observability
- [ ] Add metrics endpoint (latency/failure/quarantine/queue)
- [ ] Add CLI dashboard mode for live status
- [ ] Add structured run logs and trace IDs
- [ ] Add alert hooks for repeated failures

## Phase 5: Recovery + Release Discipline
- [ ] Add scripted restore validation workflow
- [ ] Add regression test suite for routing/stream/triage
- [ ] Add chaos tests for hangs/timeouts/failover
- [ ] Version and freeze profile bundles per release

## Operator CLI Deliverables

## March 2026: Research Agent Update
- book-researcher: qwen3.5:14b, 128k context, internet/news/Wikipedia research, structured output
- [x] `agentctl profiles`
- [x] `agentctl plan "..."`
- [x] `agentctl once "..."`
- [x] `agentctl --stream once "..."`
- [x] `agentctl --stream chat`
- [x] `agentctl health`
- [ ] `agentctl queue` and `agentctl dashboard`

## Near-Term Execution Order
1. Implement profile lint + guardrails.
2. Implement persistent queue worker mode.
3. Implement watchdog + circuit breaker.
4. Add queue/dashboard CLI subcommands.
5. Run full backup/restore drill and document sign-off.
