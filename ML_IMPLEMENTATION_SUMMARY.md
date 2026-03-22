---
name: ML Model Selector Implementation Summary
status: IMPLEMENTATION COMPLETE
date: 2026-03-22
---

# ML Model Selector - Implementation Complete

## What Was Built

A full ML infrastructure that learns to assign optimal **(model, context_length)** combinations per profile based on task content and execution outcomes.

**Three new modules**:

### 1. `agent_stack/ml_model_selector.py` (462 lines)
Core ML engine with:
- **TaskFeatureExtractor**: Converts task text → numeric features
  - token_estimate (len/4)
  - vocab_complexity (0-1 based on word analysis)
  - text_length, text_length_log
  - approval_status encoding (draft/approved/published)
  - stage encoding (brief/research/outline/draft/review/final)
- **MLModelSelector**: RandomForestClassifier trainer + predictor
  - `predict_preset_ranking()`: From task + profile → [(preset, confidence)]
  - `retrain_from_outcomes()`: From agent_reward_events.jsonl → train new model
  - Bootstrap + heuristic fallback

### 2. `agent_stack/ml_outcome_tracker.py` (95 lines)
Outcome recording layer:
- `record_ml_execution_outcome()`: Log task execution result
- Writes to agent_reward_events.jsonl (training data)
- Writes to ml_shadow_events.jsonl (audit trail)
- Task content preserved for feature extraction

### 3. `AGENT_ML_SELECTOR_GUIDE.md`
200+ line reference guide covering:
- Architecture diagram
- Integration points (all completed)
- Mode control (off/shadow/auto)
- Data flow diagrams
- Metrics tracked
- Examples (book-researcher use case)
- Debugging recipes

---

## Integration Points Implemented

### ✅ Point 1: Orchestrator Initialization

**File**: `agent_stack/orchestrator.py`, line ~20 (imports) + line ~343 (init)

```python
# Imports added
from .ml_model_selector import create_ml_selector
from .ml_outcome_tracker import MLOutcomeTracker

# In __init__ (after ml_shadow_events_path):
self.ml_selector = create_ml_selector(str(self.runtime_preset_path))
self.ml_selector_enabled = self.ml_selector is not None and self.ml_enabled
self.ml_outcome_tracker = MLOutcomeTracker(
    ml_shadow_events_path=str(self.ml_shadow_events_path),
    reward_events_path=str(self.agent_reward_events_path),
)
```

**Status**: ✅ Active, ready to use

### ✅ Point 2: Shadow Mode Recommendation

**File**: `agent_stack/orchestrator.py`, line ~2218 in `plan_request()`

```python
# ML preset ranking: optional learned recommendation
ml_candidates = None
if self.ml_selector_enabled and self.ml_mode == "shadow":
    try:
        ml_candidates = self.ml_selector.predict_preset_ranking(
            text=user_input,
            profile_name=profile.get("name"),
            approval_status="draft",
            stage="default",
            allowed_presets=[selected_runtime_preset],
            top_k=3,
        )
        self._emit_ml_shadow_event("plan_request_ml_candidates", {...})
    except Exception as e:
        logger.warning(f"ML selector error: {e}")

return {..., "ml_candidates": ml_candidates, ...}
```

**Status**: ✅ Returns top-K candidates in plan dict

### ✅ Point 3: Outcome Recording

**File**: `agent_stack/orchestrator.py`, line ~620 (new method)

```python
def record_ml_outcome(
    self,
    profile_name: str,
    preset_name: str,
    route: str,
    model: str,
    context_used: int,
    task_content: str,
    approval_status: str = "draft",
    stage: str = "default",
    quality_gate_pass: bool = True,
    confidence_score: float = None,
    duration_seconds: float = None,
    output_preview: str = None,
    correlation_id: str = None,
):
    """Record task execution outcome for ML training."""
    if not self.ml_outcome_tracker:
        return
    try:
        self.ml_outcome_tracker.record_ml_execution_outcome(...)
    except Exception as e:
        logger.warning(f"Failed to record ML outcome: {e}")
```

**Status**: ✅ Public API ready to call from book_flow.py

### ✅ Point 4: CLI Commands

**File**: `agent_stack/cli.py`, added new commands:

```bash
# Check ML status and training data availability
agentctl ml-status
# Output: JSON with ml_enabled, training_samples_available, ready_to_retrain, etc.

# Retrain model from accumulated outcomes
agentctl ml-retrain --min-samples 50
# Output: Progress + confirmation message
```

**Status**: ✅ Ready to use

---

## How It Works

### 1. **Shadow Mode (Default)**

```
User request
    ↓ (plan_request)
Default preset selected (deterministic)
    ↓
ML selector queries db
    ↓
Returns ml_candidates: [(preset, confidence)]
    ↓
Logged to ml_shadow_events.jsonl
    ↓
Default preset still used (no impact on execution)
```

### 2. **Outcome Recording**

```
Task execution complete
    ↓ (call orchestrator.record_ml_outcome)
record_ml_execution_outcome called
    ↓
Appends to agent_reward_events.jsonl:
{
  "profile": "book-researcher",
  "preset": "amd-qwen25-coder-14b-32768",
  "task_content_sample": "first 1000 chars...",
  "approval_status": "draft",
  "stage": "research",
  "quality_gate_pass": true,
  "confidence_score": null
}
    ↓
Features later extracted from task_content_sample
```

### 3. **Weekly Retraining**

```
50+ outcomes accumulated
    ↓ (agentctl ml-retrain)
Features extracted from task_content_sample
    ↓
RandomForestClassifier.fit(X, y)
where y = quality_gate_pass (bool)
    ↓
Model cached: /tmp/ml_selector_model.pkl
    ↓
Next plan_request uses new model
```

### 4. **Auto Mode (Future)**

```
...same as shadow, but:
if ml_candidates[0].confidence >= 0.75:
    use ml_candidates[0] instead of default
else:
    use default
```

---

## Configuration

### Environment Variables

```bash
# Mode control
AGENT_ML_MODE=shadow  # off / shadow / auto

# Thresholds (auto mode)
AGENT_ML_MIN_CONFIDENCE=0.75  # Only use ML if this confident
AGENT_ML_TOP_K=3              # Number of candidates returned

# File paths (auto-set)
AGENT_ML_SHADOW_EVENTS_PATH=/path/to/ml_shadow_events.jsonl
```

### Default Settings

```
Mode: shadow (recommends, doesn't influence)
Min confidence: 0.75
Top-K: 3
Status: ✅ Ready (infrastructure initialized)
```

---

## What's Ready Now

✅ **ML selector initialized** — Available in orchestrator  
✅ **Shadow mode active** — Recommendations logged but not used  
✅ **Outcome recorder** — `orchestrator.record_ml_outcome()` callable  
✅ **CLI retraining** — `agentctl ml-retrain` available  
✅ **Status monitoring** — `agentctl ml-status` shows training data count  

---

## What Needs Book Flow Integration

The system is **ready to use** but needs book_flow.py to start recording outcomes.

### In book_flow.py, after task execution:

```python
# After determining quality_gate_pass:
self.orchestrator.record_ml_outcome(
    profile_name=profile["name"],
    preset_name=plan["runtime_preset"],
    route=plan["route"],
    model=plan["model"],
    context_used=plan["options"].get("num_ctx", 8192),
    task_content=user_input,  # Full prompt/task
    approval_status="draft",  # Extract from book_project if available
    stage=stage,              # Extract from current stage
    quality_gate_pass=quality_bool,
    duration_seconds=time.time() - start,
    output_preview=result[:500],
    correlation_id=plan["correlation_id"],
)
```

**Expected impact**: After 10-15 tasks, first ml_shadow_events entries appear. After 50+ tasks, retraining becomes possible.

---

## Quick Start

### 1. Verify Setup
```bash
cd /home/daravenrk/dragonlair
agentctl ml-status
# Should show: ml_enabled=true, ml_selector_enabled=true, training_samples_available=0
```

### 2. Watch Shadow Events
```bash
tail -f book_project/ml_shadow_events.jsonl
# In another terminal, call orchestrator.plan_request()
# You should see recommendations logged
```

### 3. After 10+ Tasks Execute (in book_flow)
```bash
agentctl ml-status
# Should show: training_samples_available=10+
```

### 4. After 50+ Tasks
```bash
agentctl ml-retrain --min-samples 50
# ✅ ML model retrained successfully
```

### 5. Verify Retraining
```bash
agentctl ml-status
# Model will be loaded from /tmp/ml_selector_model.pkl next time
```

---

## File Inventory

| File | Lines | Purpose | Status |
|------|-------|---------|--------|
| ml_model_selector.py | 462 | Core ML engine | ✅ Active |
| ml_outcome_tracker.py | 95 | Outcome recording | ✅ Active |
| orchestrator.py | +50 | Integration | ✅ Modified |
| cli.py | +80 | CLI commands | ✅ Modified |
| AGENT_ML_SELECTOR_GUIDE.md | 380 | Reference | ✅ Updated |
| THIS FILE | — | Summary | ✅ New |

---

## Architecture Diagram

```
┌─ plan_request(user_input, profile)
│  ├─ _resolve_profile_runtime_settings()
│  │  └─ selected_runtime_preset ← default
│  │
│  ├─ [ML SELECTOR - SHADOW MODE]
│  │  └─ if ml_selector_enabled:
│  │     ├─ extract_features(user_input)
│  │     ├─ predict_preset_ranking()
│  │     └─ ml_candidates = [...]  ← logged to ml_shadow_events.jsonl
│  │
│  └─ return {preset, model, route, options, ml_candidates}
│
└─ Task Execution
   ├─ run on (route, model, options)
   │
   ├─ determine quality_gate_pass
   │
   └─ orchestrator.record_ml_outcome()
      ├─ append to agent_reward_events.jsonl
      └─ append to ml_shadow_events.jsonl
         ↓
      Weekly Retraining
      ├─ ml_selector.retrain_from_outcomes()
      ├─ Read agent_reward_events.jsonl (50+ samples)
      ├─ Extract features from task_content_sample
      ├─ Train RandomForestClassifier
      └─ Cache model → /tmp/ml_selector_model.pkl
```

---

## Token Economy Integration

Every recorded outcome feeds the reward ledger:

```
PASS (quality gate) + ML correct → +1 token to profile
 ↓
FAIL (quality gate) + ML suggested wrong preset → profile learns to avoid it
 ↓
Smaller model works → implicit efficiency reward (less resource burn)
 ↓
System learns which (model, context) combos are most efficient locally
```

---

## Safety Guardrails

✅ **GPU-100% Policy**: ML can't suggest CPU fallback presets  
✅ **Profile Allowlists**: ML only ranks from approved presets  
✅ **Deterministic Fallback**: Default preset always works if ML fails  
✅ **Shadow Mode Default**: No execution impact until explicitly enabled  
✅ **Error Handling**: Graceful degradation if ML unavailable  

---

## Next Phases

### Phase 2: Book Flow Integration (User-Driven)
- [ ] Import orchestrator in book_flow.py
- [ ] Extract approval_status + stage from book_project
- [ ] Call orchestrator.record_ml_outcome() per task
- [ ] Validate data flow with 5-10 manual tasks

### Phase 3: Monitor & Validate
- [ ] Watch ml_shadow_events.jsonl for 1-2 weeks
- [ ] Verify ml_candidates confidence scores (should be 0.5-0.95)
- [ ] Check quality_gate_pass rates
- [ ] Validate model caching after first retraining

### Phase 4: Transition to Auto Mode
- [ ] Set AGENT_ML_MODE=auto in docker-compose
- [ ] Set AGENT_ML_MIN_CONFIDENCE=0.75+
- [ ] Monitor execution impacts for 1 week
- [ ] Rollback to shadow if needed

---

## Support Commands

```bash
# Current status
agentctl ml-status | jq '.training_samples_available'

# View recent ML recommendations (shadow events)
tail -100 book_project/ml_shadow_events.jsonl | jq '.event' | sort | uniq -c

# Retrain with custom threshold
agentctl ml-retrain --min-samples 25

# Check if model cached
ls -lh /tmp/ml_selector_model.pkl

# Monitor in real-time
watch -n 2 'agentctl ml-status | jq .training_samples_available'
```

---

**🎯 Status**: Ready for book_flow.py integration  
**🔒 Safety**: All guardrails in place  
**📊 Data**: Shadow tracking active (0 samples initially)  
**⏳ Timeline**: 1-2 weeks to 50 samples → retraining ready  
