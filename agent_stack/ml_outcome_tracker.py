"""
ML outcome tracking module for agent task execution.

Records execution outcomes (task content, preset used, approval_status, quality result)
to enable reward ledger updates and ML model retraining.

Integration points:
- orchestrator.record_ml_outcome() - called after task execution
- Hooks into existing quality gate success/failure recording
- Feeds agent_reward_events.jsonl with structured training data
"""

import json
import time
from pathlib import Path
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class MLOutcomeTracker:
    """Records execution outcomes for ML model training."""
    
    def __init__(self, ml_shadow_events_path: str, reward_events_path: str):
        self.ml_shadow_events_path = Path(ml_shadow_events_path)
        self.reward_events_path = Path(reward_events_path)
    
    def record_ml_execution_outcome(
        self,
        profile_name: str,
        preset_name: str,
        route: str,
        model: str,
        context_used: int,
        task_content: str,
        approval_status: str,
        stage: str,
        quality_gate_pass: bool,
        confidence_score: Optional[float] = None,
        duration_seconds: Optional[float] = None,
        output_preview: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        """
        Record a task execution outcome for ML training.
        
        This outcome feeds the reward ledger update and ML retraining.
        
        Args:
            profile_name: Profile used (e.g., "book-researcher")
            preset_name: Runtime preset used (e.g., "amd-qwen25-coder-14b-32768")
            route: Route used (ollama_amd or ollama_nvidia)
            model: Model name (e.g., "qwen2.5-coder:14b")
            context_used: Actual context window used
            task_content: The input prompt/task that was executed
            approval_status: "draft", "approved", "published"
            stage: Book stage (brief, research, outline, canon, draft, review, final)
            quality_gate_pass: Whether quality gate passed
            confidence_score: ML confidence in this preset choice (0-1)
            duration_seconds: How long execution took
            output_preview: First 500 chars of output
            correlation_id: Execution correlation ID
        """
        
        timestamp = time.time()
        
        # Record as ML shadow event (for tracking ML recommendations vs actual)
        ml_event = {
            "timestamp": timestamp,
            "event": "ml_outcome_recorded",
            "profile": profile_name,
            "preset": preset_name,
            "route": route,
            "model": model,
            "context_used": int(context_used),
            "task_chars": len(task_content),
            "approval_status": approval_status,
            "stage": stage,
            "quality_gate_pass": bool(quality_gate_pass),
            "confidence_score": float(confidence_score) if confidence_score is not None else None,
            "duration_seconds": float(duration_seconds) if duration_seconds is not None else None,
            "output_preview": (output_preview or "")[:500],
            "correlation_id": correlation_id,
        }
        
        self._append_jsonl(self.ml_shadow_events_path, ml_event)
        
        # Record in reward events for training data (task context preserved)
        reward_event = {
            "timestamp": timestamp,
            "profile": profile_name,
            "preset": preset_name,
            "route": route,
            "model": model,
            "context_limit": int(context_used),
            "task_content_sample": task_content[:1000],  # Keep sample for feature extraction
            "approval_status": approval_status,
            "stage": stage,
            "quality_gate_pass": bool(quality_gate_pass),
            "confidence_score": float(confidence_score) if confidence_score is not None else None,
            "correlation_id": correlation_id,
        }
        
        self._append_jsonl(self.reward_events_path, reward_event)
        
        logger.info(
            f"ML outcome recorded: {profile_name} + {preset_name} "
            f"→ {'PASS' if quality_gate_pass else 'FAIL'} "
            f"(confidence={confidence_score})"
        )
    
    def _append_jsonl(self, path: Path, payload: Dict[str, Any]) -> None:
        """Append JSON line to file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
        except Exception as e:
            logger.warning(f"Failed to append to {path}: {e}")
