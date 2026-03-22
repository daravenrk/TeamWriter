"""
ML-driven runtime preset selector for agent profiles.

Learns to assign optimal (model, context_length) combinations based on:
- Task content characteristics (word count, vocabulary, token estimate)
- Profile type (researcher, writer, editor, etc.)
- Approval status (draft, approved, published)
- Historical outcomes (quality gate pass/fail, token efficiency)

Core principle: Find the SMALLEST model/context that satisfies the task,
rewarding efficiency while maintaining quality standards defined by GPU-only
execution and vocabulary match.

Training data: agent_reward_events.jsonl + task_ledger.json
Output: Cached classifier that ranks presets by (confidence, efficiency_score)
"""

import json
import os
import time
import pickle
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from collections import Counter
import re

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import LabelEncoder
    import numpy as np
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    logging.warning("scikit-learn not available; ML selector will use heuristic fallback")
    np = None  # Placeholder for when sklearn unavailable


logger = logging.getLogger(__name__)


class TaskFeatureExtractor:
    """Extract numeric features from task content for ML model."""
    
    @staticmethod
    def estimate_token_count(text: str) -> int:
        """Rough token count estimate (actual: 1 token ≈ 4 chars for English)."""
        return max(1, len(text) // 4)
    
    @staticmethod
    def extract_vocabulary_complexity(text: str) -> float:
        """
        Score 0-1 indicating vocabulary complexity.
        - Simple: common words, short sentences (< 0.3)
        - Medium: technical terms, mixed lengths (0.3-0.7)
        - Complex: specialized vocabulary, dense prose (> 0.7)
        """
        # Lowercase and split into words
        words = re.findall(r'\b\w+\b', text.lower())
        if not words:
            return 0.0
        
        # Check for specialized vocabulary markers
        tech_markers = {
            'algorithm', 'architecture', 'implementation', 'optimization',
            'parameter', 'configuration', 'constraint', 'dependency',
            'framework', 'protocol', 'specification', 'abstraction',
        }
        
        complex_markers = {
            'whereas', 'nevertheless', 'moreover', 'furthermore',
            'constitutes', 'encompasses', 'substantive', 'epistemic',
            'methodology', 'requisite', 'profound', 'intricate',
        }
        
        # Average word length (proxy for complexity)
        avg_word_len = sum(len(w) for w in words) / len(words)
        
        # Unique word diversity
        unique_ratio = len(set(words)) / len(words)
        
        # Score components
        tech_presence = sum(1 for m in tech_markers if m in words) / max(1, len(words))
        complex_presence = sum(1 for m in complex_markers if m in words) / max(1, len(words))
        
        # Composite score
        score = (
            (avg_word_len / 20.0) * 0.3 +  # Longer words = more complex
            (unique_ratio * 0.2) +  # Diversity matters
            (tech_presence * 0.25) +  # Tech terms indicate code/architecture
            (complex_presence * 0.25)  # Complex markers
        )
        
        return min(1.0, max(0.0, score))
    
    @staticmethod
    def extract_features(
        text: str,
        profile_name: str,
        approval_status: str,
        stage: str = "default"
    ) -> Dict[str, float]:
        """
        Extract numeric features for ML model input.
        
        Args:
            text: Task content/prompt
            profile_name: e.g., "book-researcher", "book-writer"
            approval_status: e.g., "draft", "approved", "published"
            stage: Book stage (brief, research, outline, canon, draft, review, final)
        
        Returns:
            Dict with numeric features ready for ML model
        """
        # Safe numpy call - only evaluate log1p if numpy available
        text_length_log = 0.0
        if HAS_SKLEARN:
            try:
                text_length_log = float(np.log1p(len(text)))
            except Exception:
                text_length_log = 0.0
        
        features = {
            "token_estimate": TaskFeatureExtractor.estimate_token_count(text),
            "vocab_complexity": TaskFeatureExtractor.extract_vocabulary_complexity(text),
            "text_length": len(text),
            "text_length_log": text_length_log,
            "approval_draft": 1.0 if approval_status == "draft" else 0.0,
            "approval_approved": 1.0 if approval_status == "approved" else 0.0,
            "approval_published": 1.0 if approval_status == "published" else 0.0,
            "stage_brief": 1.0 if stage == "brief" else 0.0,
            "stage_research": 1.0 if stage == "research" else 0.0,
            "stage_outline": 1.0 if stage == "outline" else 0.0,
            "stage_draft": 1.0 if stage == "draft" else 0.0,
            "stage_review": 1.0 if stage == "review" else 0.0,
        }
        
        return features


class MLModelSelector:
    """
    Selects runtime presets using trained ML model.
    
    Workflow:
    1. Extract features from task content
    2. Query trained classifier for preset probabilities
    3. Rank presets by (confidence × efficiency_score)
    4. Return top-N approved presets in rank order
    5. Orchestrator validates compliance and picks top-1
    """
    
    def __init__(
        self,
        runtime_presets: Dict[str, Any],
        cache_path: str = "/tmp/ml_selector_model.pkl"
    ):
        """
        Args:
            runtime_presets: Loaded presets dict {preset_name: {route, model, options}}
            cache_path: Where to cache trained model
        """
        self.runtime_presets = runtime_presets
        self.cache_path = cache_path
        self.model = None
        self.label_encoder = None
        self.preset_names = list(runtime_presets.keys())
        self.feature_extractor = TaskFeatureExtractor()
        
        self._load_or_train_model()
    
    def _load_or_train_model(self):
        """Load cached model or train from scratch."""
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "rb") as f:
                    cached = pickle.load(f)
                    self.model = cached.get("model")
                    self.label_encoder = cached.get("label_encoder")
                    logger.info(f"Loaded cached ML model from {self.cache_path}")
                    return
            except Exception as e:
                logger.warning(f"Failed to load cached model: {e}; will retrain")
        
        if not HAS_SKLEARN:
            logger.warning("scikit-learn not available; selector will use heuristic fallback only")
            self.model = None
            return
        
        # Train on synthetic bootstrapped data (will be replaced by real outcomes)
        self._train_model_bootstrap()
    
    def _train_model_bootstrap(self):
        """
        Bootstrap training data from preset characteristics.
        (Replaced by real training data as outcomes accumulate)
        """
        if not HAS_SKLEARN:
            return
        
        logger.info("Training ML model on bootstrap data...")
        X = []
        y = []
        
        # For each preset, generate synthetic training examples
        for preset_name, preset_cfg in self.runtime_presets.items():
            model_name = preset_cfg.get("model", "")
            num_ctx = preset_cfg.get("options", {}).get("num_ctx", 4096)
            route = preset_cfg.get("route", "")
            
            # Synthetic examples based on model capabilities
            # Small context = good for short tasks
            # Large model = good for complex tasks
            # This will be refined by real outcomes
            
            if "coder" in model_name:
                # Coder models suited for medium complexity, vocabulary-heavy
                example_features = {
                    "token_estimate": num_ctx * 0.3,
                    "vocab_complexity": 0.65,
                    "text_length": num_ctx * 200,
                    "text_length_log": np.log1p(num_ctx * 200),
                    "approval_draft": 0.5,  # Mixed
                    "approval_approved": 0.3,
                    "approval_published": 0.2,
                    "stage_research": 0.5,
                    "stage_draft": 0.3,
                    "stage_outline": 0.2,
                    "stage_brief": 0.0,
                    "stage_review": 0.0,
                }
            elif "27b" in model_name or "35b" in model_name:
                # Large models suited for complex tasks
                example_features = {
                    "token_estimate": num_ctx * 0.6,
                    "vocab_complexity": 0.8,
                    "text_length": num_ctx * 300,
                    "text_length_log": np.log1p(num_ctx * 300),
                    "approval_draft": 0.2,
                    "approval_approved": 0.4,
                    "approval_published": 0.4,
                    "stage_draft": 0.4,
                    "stage_review": 0.4,
                    "stage_outline": 0.2,
                    "stage_brief": 0.0,
                    "stage_research": 0.0,
                }
            else:
                # Small/medium models suited for simple tasks
                example_features = {
                    "token_estimate": num_ctx * 0.2,
                    "vocab_complexity": 0.3,
                    "text_length": num_ctx * 100,
                    "text_length_log": np.log1p(num_ctx * 100),
                    "approval_draft": 0.6,
                    "approval_approved": 0.3,
                    "approval_published": 0.1,
                    "stage_brief": 0.5,
                    "stage_outline": 0.3,
                    "stage_draft": 0.2,
                    "stage_research": 0.0,
                    "stage_review": 0.0,
                }
            
            X.append(list(example_features.values()))
            y.append(preset_name)
        
        if not X or not y:
            logger.warning("No training data available for ML model")
            return
        
        X = np.array(X, dtype=np.float32)
        self.label_encoder = LabelEncoder()
        y_encoded = self.label_encoder.fit_transform(y)
        
        self.model = RandomForestClassifier(
            n_estimators=50,
            max_depth=8,
            random_state=42,
            n_jobs=-1
        )
        self.model.fit(X, y_encoded)
        
        # Cache the model
        try:
            with open(self.cache_path, "wb") as f:
                pickle.dump({
                    "model": self.model,
                    "label_encoder": self.label_encoder
                }, f)
            logger.info(f"Cached model to {self.cache_path}")
        except Exception as e:
            logger.warning(f"Failed to cache model: {e}")
    
    def predict_preset_ranking(
        self,
        text: str,
        profile_name: str,
        approval_status: str,
        stage: str = "default",
        allowed_presets: Optional[List[str]] = None,
        top_k: int = 5
    ) -> List[Tuple[str, float]]:
        """
        Predict best preset(s) for the given task.
        
        Args:
            text: Task content
            profile_name: Profile name (e.g., "book-researcher")
            approval_status: "draft", "approved", "published"
            stage: Book stage
            allowed_presets: List of preset names to consider (if None, use all)
            top_k: Return top-K candidates
        
        Returns:
            List of (preset_name, confidence_score) tuples, highest confidence first
        """
        # Extract features
        features_dict = self.feature_extractor.extract_features(
            text, profile_name, approval_status, stage
        )
        
        # If no model trained, use heuristic fallback
        if self.model is None or self.label_encoder is None:
            return self._heuristic_preset_ranking(
                features_dict, allowed_presets, top_k
            )
        
        # Convert to model input (only reached if model is trained, i.e., HAS_SKLEARN is True)
        features_vec = np.array(list(features_dict.values()), dtype=np.float32).reshape(1, -1)
        
        # Get probabilities from trained model
        try:
            probabilities = self.model.predict_proba(features_vec)[0]
            preset_scores = list(zip(self.label_encoder.classes_, probabilities))
        except Exception as e:
            logger.warning(f"ML prediction failed: {e}; falling back to heuristic")
            return self._heuristic_preset_ranking(
                features_dict, allowed_presets, top_k
            )
        
        # Filter to allowed presets
        if allowed_presets:
            preset_scores = [
                (p, s) for p, s in preset_scores if p in allowed_presets
            ]
        
        # Apply efficiency score (prefer smaller models/contexts)
        scored_presets = []
        for preset_name, confidence in preset_scores:
            if preset_name not in self.runtime_presets:
                continue
            
            preset = self.runtime_presets[preset_name]
            num_ctx = preset.get("options", {}).get("num_ctx", 4096)
            model_name = preset.get("model", "")
            
            # Efficiency score: smaller context = higher score
            # (Normalized 0-1, with max context = 65536 as baseline)
            efficiency_score = 1.0 - (num_ctx / 65536.0)
            
            # Combined score: confidence weighted by efficiency
            combined_score = confidence * 0.7 + efficiency_score * 0.3
            
            scored_presets.append((preset_name, combined_score, confidence))
        
        # Sort by combined score, descending
        scored_presets.sort(key=lambda x: x[1], reverse=True)
        
        # Return top-K as (preset_name, confidence)
        return [(p, c) for p, _, c in scored_presets[:top_k]]
    
    def _heuristic_preset_ranking(
        self,
        features: Dict[str, float],
        allowed_presets: Optional[List[str]],
        top_k: int
    ) -> List[Tuple[str, float]]:
        """
        Fallback heuristic when ML model is unavailable.
        Ranks presets based on feature heuristics.
        """
        vocab_complexity = features.get("vocab_complexity", 0.5)
        token_estimate = features.get("token_estimate", 2000)
        
        candidates = allowed_presets or self.preset_names
        
        scored = []
        for preset_name in candidates:
            if preset_name not in self.runtime_presets:
                continue
            
            preset = self.runtime_presets[preset_name]
            num_ctx = preset.get("options", {}).get("num_ctx", 4096)
            model_name = preset.get("model", "")
            
            # Heuristic: match model size to task complexity
            score = 0.5
            
            # Penalty for context too small
            if token_estimate > num_ctx * 0.8:
                score -= 0.3
            
            # Reward for efficiency (small context that fits)
            if token_estimate <= num_ctx * 0.3:
                score += 0.2
            
            # Match vocabulary to model capability
            if vocab_complexity > 0.6 and "coder" in model_name:
                score += 0.15
            elif vocab_complexity > 0.7 and ("27b" in model_name or "35b" in model_name):
                score += 0.15
            
            scored.append((preset_name, score))
        
        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(p, s) for p, s in scored[:top_k]]
    
    def retrain_from_outcomes(
        self,
        outcomes_path: str,
        reward_ledger_path: str,
        min_samples: int = 50
    ):
        """
        Retrain model from actual execution outcomes.
        
        Reads:
        - outcomes_path: agent_reward_events.jsonl (execution results)
        - reward_ledger_path: agent_reward_ledger.json (token balance)
        
        Computes:
        - For each (profile, preset, task_content) tuple:
          - Quality gate outcome (0 = fail, 1 = pass)
          - Efficiency (tokens_spent / max_tokens)
        
        Trains new classifier to predict quality outcomes.
        """
        if not HAS_SKLEARN:
            logger.warning("scikit-learn not available; cannot retrain model")
            return
        
        logger.info("Retraining ML model from outcomes...")
        
        X = []
        y = []  # Quality gate outcome (0 or 1)
        
        # Read outcomes
        outcomes = []
        if os.path.exists(outcomes_path):
            try:
                with open(outcomes_path, "r") as f:
                    for line in f:
                        if line.strip():
                            outcomes.append(json.loads(line))
            except Exception as e:
                logger.warning(f"Failed to read outcomes: {e}")
        
        # Read reward ledger
        ledger = {}
        if os.path.exists(reward_ledger_path):
            try:
                with open(reward_ledger_path, "r") as f:
                    ledger = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read reward ledger: {e}")
        
        # Extract training examples from outcomes
        for outcome in outcomes[-1000:]:  # Use recent 1000 outcomes
            profile_name = outcome.get("profile")
            preset_name = outcome.get("preset")
            task_content = outcome.get("task_content", "")
            approval_status = outcome.get("approval_status", "draft")
            stage = outcome.get("stage", "default")
            quality_gate = outcome.get("quality_gate_pass", False)
            
            if not profile_name or not preset_name or not task_content:
                continue
            
            try:
                features_dict = self.feature_extractor.extract_features(
                    task_content, profile_name, approval_status, stage
                )
                X.append(list(features_dict.values()))
                y.append(1 if quality_gate else 0)
            except Exception as e:
                logger.warning(f"Failed to extract features: {e}")
                continue
        
        if len(X) < min_samples:
            logger.warning(
                f"Only {len(X)} training samples; need >= {min_samples} to retrain"
            )
            return
        
        X = np.array(X, dtype=np.float32)
        y = np.array(y)
        
        # Train new model
        new_model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42,
            n_jobs=-1
        )
        new_model.fit(X, y)
        
        self.model = new_model
        
        # Cache
        try:
            with open(self.cache_path, "wb") as f:
                pickle.dump({
                    "model": self.model,
                    "label_encoder": self.label_encoder
                }, f)
            logger.info(f"Cached retrained model to {self.cache_path}")
        except Exception as e:
            logger.warning(f"Failed to cache retrained model: {e}")
        
        logger.info(f"Retrained ML model on {len(X)} real outcomes")


def create_ml_selector(runtime_presets_path: str) -> Optional[MLModelSelector]:
    """
    Factory to create ML selector instance or return None if unavailable.
    """
    try:
        from .runtime_presets import load_runtime_presets
        presets = load_runtime_presets(runtime_presets_path)
        return MLModelSelector(presets)
    except Exception as e:
        logger.warning(f"Failed to create ML selector: {e}")
        return None
