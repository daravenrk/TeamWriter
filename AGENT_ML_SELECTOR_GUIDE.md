---
name: ML Model Selector Integration
path: AGENT_ML_SELECTOR_GUIDE.md
status: active
last_updated: 2026-03-22
---

# ML Model Selector Integration Guide

## Overview

The ML model selector learns to assign optimal **(model, context\_length, route)** combinations for agent profiles based on:
- **Task characteristics** (word count, vocabulary complexity, token estimate)
- **Approval status** (draft → approved → published)
- **Profile type** (researcher, writer, editor, publisher, etc.)
- **Historical outcomes** (quality gate success/failure, token efficiency)

## Architecture

```
user_input + profile
    ↓
[plan_request()]
    ↓
[_resolve_profile_runtime_settings] ← DEFAULT PRESET
    ↓
[ML Selector] ← LEARNED RANKING (optional, shadow mode by default)
    ↓
[compliance validator]
    ↓
(route, model, context, options)
    ↓
[task execution]
    ↓
[outcome recorded]
    ↓
[reward ledger updated + ML training data accumulated]
```

## Integration Status

✅ **COMPLETE (2026-03-22)** — All core integration points implemented:

1. ✅ **ML Selector Initialization** (`orchestrator.__init__`)
   - Imports: `ml_model_selector.py` + `ml_outcome_tracker.py`
   - Creates: `self.ml_selector` (RandomForestClassifier trainer/predictor)
   - Creates: `self.ml_outcome_tracker` (outcome recording interface)
   - Enabled: Checks `ml_enabled` flag (from `AGENT_ML_MODE`)

2. ✅ **Outcome Tracker Integration**
   - `MLOutcomeTracker` initialized with paths to ml_shadow_events.jsonl + agent_reward_events.jsonl
   - Ready to record task execution results

3. ✅ **plan_request() Integration**
   - Added ML selector call (shadow mode only)
   - Queries `ml_selector.predict_preset_ranking()` with task features
   - Returns `ml_candidates` in plan dict (top-K presets by confidence)
   - Logs recommendations to `ml_shadow_events.jsonl`
   - Includes error handling (falls back to default if ML fails)

4. ✅ **New Method: orchestrator.record_ml_outcome()**
   - Public API for recording task execution outcomes
   - Signature: `record_ml_outcome(profile_name, preset_name, route, model, context_used, task_content, approval_status, stage, quality_gate_pass, confidence_score, duration_seconds, output_preview, correlation_id)`
   - Integrates with reward ledger + ML training pipeline
   - Wraps MLOutcomeTracker with error handling

### Status: Core infrastructure ready for use

**Next Steps (User-Driven)**:
- [ ] Call `orchestrator.record_ml_outcome()` in book_flow.py after each task completion (pass/fail)
- [ ] Extract approval_status + stage from book_project state when recording
- [ ] Accumulate 50+ outcomes over 1-2 weeks
- [ ] Run weekly retraining via cron job (see CLI helper below)
- [ ] Monitor ml_shadow_events.jsonl for recommendation patterns
- [ ] Transition from shadow → auto mode once confidence ≥ 0.75

---

## Integration Points (IMPLEMENTED)

### 1. **ML Selector Initialization in `OrchestratorAgent.__init__`** ✅

IMPLEMENTED at line ~343 in orchestrator.py:

```python
# ML Model Selector: learns optimal (model, context) per profile
self.ml_selector = create_ml_selector(str(self.runtime_preset_path))
self.ml_selector_enabled = self.ml_selector is not None and self.ml_enabled

# ML Outcome tracking: records execution results for training data
self.ml_outcome_tracker = MLOutcomeTracker(
    ml_shadow_events_path=str(self.ml_shadow_events_path),
    reward_events_path=str(self.agent_reward_events_path),
)
```

### 2. **Outcome Tracking Infrastructure** ✅

IMPLEMENTED: `MLOutcomeTracker` class in ml_outcome_tracker.py records outcomes to:
- `agent_reward_events.jsonl` — training data with task_content_sample
- `ml_shadow_events.jsonl` — recommendation audit trail

## Mode Control

Control ML selector behavior via environment variables:

```bash
# Off: no ML, use default presets only
AGENT_ML_MODE=off

# Shadow (default): ML recommends but doesn't affect execution
AGENT_ML_MODE=shadow

# Auto: ML affects execution if confidence >= threshold
AGENT_ML_MODE=auto
AGENT_ML_MIN_CONFIDENCE=0.75
AGENT_ML_TOP_K=3
```

## Data Flow

### Training Data Pipeline

```
Task Execution
    ↓ (outcome_tracker.record_ml_execution_outcome)
agent_reward_events.jsonl
    ↓ (contains task_content_sample + metadata)
    ↓
Weekly Retraining Run
    ↓ (ml_selector.retrain_from_outcomes)
Features Extracted:
    - token_estimate
    - vocab_complexity
    - text_length
    - stage encoding
    - approval_status encoding
    ↓
RandomForestClassifier Trained
    ↓
Cached Model (_generated/ml_selector_model.pkl)
```

### Inference Path

```
user_input + profile
    ↓ (ml_selector.predict_preset_ranking)
Feature Extraction:
    - Extract from task content
    - Encode profile metadata
    ↓
Model Predict Probabilities
    ↓
Rank by (confidence × efficiency)
    ↓
Return Top-K presets
```

## Metrics Tracked

For each outcome recorded:

| Metric | Type | Purpose |
|--------|------|---------|
| `token_estimate` | int | Input complexity |
| `vocab_complexity` | 0-1 | Lexical density |
| `text_length` | int | Task magnitude |
| `approval_status` | enum | Draft/approved/published |
| `stage` | enum | Book stage progression |
| `quality_gate_pass` | bool | Success signal |
| `context_used` | int | Actual context window |
| `duration_seconds` | float | Execution speed |
| `confidence_score` | 0-1 | ML certainty |

## Example: Book Researcher Profile

**Goal**: Quickly learn that research tasks need:
- Medium context (16-32k tokens)
- Coder models (specialized vocabulary)
- Sufficient but not excessive context

**Training sequence**:
1. Task: 8000 chars research prompt → uses `amd-qwen25-coder-14b-32768`
   - PASS: +1 token, +0.1 confidence
   - FAIL: -1 token, -0.1 confidence

2. After 50 outcomes, model learns:
   - Research + high-vocab → prefer coder 14b or 27b
   - Simple research → 9b is sufficient
   - Long research → 49k context needed

3. Next 8000-char research prompt:
   - ML predicts: `[("amd-qwen25-coder-14b-32768", 0.92), ("amd-qwen35-27b-49152", 0.75)]`
   - Orchestrator picks first (92% confidence)
   - If PASS → +1 token; If FAIL → fallback to 27b

## Gamification Integration

The ML selector works within the token economy:

- **Token Cost**: Larger models cost implicit tokens (less efficient)
- **Token Reward**: Quality passes give +1 token
- **Token Penalty**: Quality failures give -1 token
- **Learned Policies**: ML learns which model/context combos yield high success with low token burn

Example:
```
book-researcher token balance: 8/12
- Needs complex task, confidence that 14b works: +1 token (learned model is good)
- Needs complex task, uncertain, picks 27b instead: 0 tokens (safe but expensive)
- Completes successfully with 14b: +1 token (back to 9)
- After 100 successes: ML confidence in 14b rises; system picks it ~80% of the time
```

## Validation & Testing

### Quick Test: Bootstrap Model

```python
from agent_stack.ml_model_selector import MLModelSelector
from agent_stack.runtime_presets import load_runtime_presets

presets = load_runtime_presets("/path/to/presets.json")
selector = MLModelSelector(presets)

# Model bootstrapped on synthetic data
ranking = selector.predict_preset_ranking(
    text="Implement a Python function that...",
    profile_name="amd-coder",
    approval_status="draft",
    allowed_presets=["amd-qwen25-coder-14b-32768"],
    top_k=3
)
print(ranking)
```

### Test: Retraining on Real Data

```python
selector.retrain_from_outcomes(
    outcomes_path="/path/to/agent_reward_events.jsonl",
    reward_ledger_path="/path/to/agent_reward_ledger.json",
    min_samples=10  # Lower threshold for testing
)
```

## Next Steps

1. **Integrate outcome tracking** into book_flow.py task completion
2. **Enable shadow mode** (`AGENT_ML_MODE=shadow`) to see recommendations
3. **Accumulate 50+ outcomes** over 1-2 weeks
4. **Run first retraining** with real data
5. **Evaluate confidence scores** vs actual quality gate pass rates
6. **Gradually shift to auto mode** (`AGENT_ML_MODE=auto`) with high confidence threshold (0.80+)
7. **Monitor token efficiency** (smaller models used, quality maintained)

## Debugging

### Check ML Model Status

```bash
PYTHONPATH=/home/daravenrk/dragonlair python3 -c "
from agent_stack.ml_model_selector import create_ml_selector
from agent_stack.runtime_presets import load_runtime_presets

selector = create_ml_selector('/home/daravenrk/dragonlair/agent_stack/runtime_presets.json')
print(f'Model trained: {selector.model is not None}')
print(f'Has LabelEncoder: {selector.label_encoder is not None}')
"
```

### Inspect Recent Outcomes

```bash
tail -20 /home/daravenrk/dragonlair/book_project/agent_reward_events.jsonl | python3 -m json.tool
```

### Check Shadow Events

```bash
tail -5 /home/daravenrk/dragonlair/book_project/ml_shadow_events.jsonl | python3 -m json.tool
```

### Manual Retraining

```bash
cd /home/daravenrk/dragonlair
python3 - <<"PY"
from agent_stack.orchestrator import OrchestratorAgent
o = OrchestratorAgent()
if o.ml_selector:
    o.ml_selector.retrain_from_outcomes(
        outcomes_path=str(o.agent_reward_events_path),
        reward_ledger_path=str(o.agent_rewards_path),
        min_samples=10
    )
    print("Retrained successfully")
else:
    print("ML selector not available")
PY
```

---

## Implementation Timeline

**Phase 1: Core Infrastructure** ✅ (2026-03-22)
- Created `ml_model_selector.py` — ML engine (RandomForestClassifier trainer/predictor)
- Created `ml_outcome_tracker.py` — outcome recording interface
- Integrated into `orchestrator.__init__()` — initializes both components
- Added `plan_request()` integration — ML selector queries in shadow mode
- Added `orchestrator.record_ml_outcome()` — public API for outcome recording
- Updated imports and logging setup

**Phase 2: Book Flow Integration** (TODO - User-Driven)
- [ ] Import orchestrator in book_flow.py
- [ ] Extract approval_status + stage from book_project state
- [ ] Call `orchestrator.record_ml_outcome()` after each task completes
- [ ] Record both PASS and FAIL outcomes for quality gate results
- [ ] Test with 5-10 manual tasks to verify data flow

**Phase 3: Retraining Pipeline** (TODO - User-Driven)
- [ ] Add CLI helper function or cron job for weekly retraining
- [ ] Accumulate 50+ outcomes (1-2 weeks of operation)
- [ ] Run first manual retraining to test ML training pipeline
- [ ] Verify ml_selector_model.pkl is created and cached

**Phase 4: Validation & Transition** (TODO - Post-Training)
- [ ] Monitor ml_shadow_events.jsonl for recommendation patterns
- [ ] Verify ml_candidates confidence scores sensible (0.5-0.95 range)
- [ ] Check quality_gate_pass rates for recommended vs default presets
- [ ] Transition to auto mode (AGENT_ML_MODE=auto) once confident
- [ ] Set AGENT_ML_MIN_CONFIDENCE=0.75+ for auto selection

---

**Status**: **READY FOR BOOK_FLOW INTEGRATION**
- Infrastructure: ✅ Complete
- Core API: ✅ Public methods available
- Data Flow: ✅ Shadow recording active
- Dependencies**: scikit-learn (optional, falls back to heuristics if unavailable)  
**Training Data**: Accumulating in agent_reward_events.jsonl (shadow mode)
**Safe to Deploy**: Yes (shadow mode by default, no execution impact)
