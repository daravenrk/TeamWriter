# TODO: Agent Work and Response Logging
# - Add detailed logging for agent work, responses, and lifecycle events
# - Log all major stage transitions, returns, and errors
import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock

from .lock_manager import AgentLockManager
from .exceptions import AgentStackError, AgentUnexpectedError, BookExportError, ChapterSpecValidationError, StageQualityGateError
from .orchestrator import OrchestratorAgent
from .output_schemas import validate_stage_payload
from .writing_assistant import generate_names, generate_technology, generate_personalities, generate_dates_history


RUBRIC_KEYS = [
    "concept_validation",
    "structure_validation",
    "chapter_coherence",
    "sentence_clarity",
    "grammar_correction",
    "continuity_tracking",
    "fact_verification",
    "tone_consistency",
    "genre_compliance",
    "reader_engagement_score",
]


def slugify(text: str) -> str:
    lowered = text.strip().lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = re.sub(r"-+", "-", lowered).strip("-")
    return lowered or "book-run"


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


@contextmanager
def file_lock(path: Path, timeout_seconds: float = 20.0, poll_seconds: float = 0.1):
    lock_path = Path(f"{path}.lock")
    ensure_dir(lock_path.parent)
    with open(lock_path, "w", encoding="utf-8") as handle:
        deadline = datetime.utcnow().timestamp() + timeout_seconds
        while True:
            try:
                flock(handle.fileno(), LOCK_EX | LOCK_NB)
                break
            except BlockingIOError:
                if datetime.utcnow().timestamp() >= deadline:
                    raise TimeoutError(f"Timed out acquiring file lock for {path}")
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            flock(handle.fileno(), LOCK_UN)


def write_text(path: Path, content: str):
    ensure_dir(path.parent)
    with file_lock(path):
        path.write_text(content, encoding="utf-8")


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    with file_lock(path):
        return path.read_text(encoding="utf-8")


def read_json(path: Path, default=None):
    if default is None:
        default = {}
    raw = read_text(path, "")
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload):
    write_text(path, json.dumps(payload, indent=2))

def append_analytics(path: Path, event: dict):
    ensure_dir(path.parent)
    with file_lock(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")


def append_jsonl(path: Path, payload):
    ensure_dir(path.parent)
    with file_lock(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


def append_run_event(path: Path, event: str, details: dict):
    if path is None:
        return
    append_jsonl(
        path,
        {
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "details": details,
        },
    )


def parse_json_block(raw: str, fallback=None):
    fallback = fallback if fallback is not None else {}
    text = (raw or "").strip()
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return fallback
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return fallback


def build_contract(role: str, objective: str, constraints, inputs, output_format: str, failure_conditions) -> str:
    constraints_block = "\n".join(f"- {item}" for item in constraints)
    failures_block = "\n".join(f"- {item}" for item in failure_conditions)
    return (
        f"ROLE:\n{role}\n\n"
        f"OBJECTIVE:\n{objective}\n\n"
        f"CONSTRAINTS:\n{constraints_block}\n\n"
        f"INPUTS:\n{json.dumps(inputs, indent=2)}\n\n"
        f"OUTPUT FORMAT:\n{output_format}\n\n"
        f"FAILURE CONDITIONS:\n{failures_block}\n"
    ).strip()


def build_resource_reference_block(context_store: dict) -> str:
    refs = context_store.get("_resource_refs") if isinstance(context_store, dict) else None
    framework_refs = context_store.get("_framework_refs") if isinstance(context_store, dict) else None
    snapshot = context_store.get("_resource_snapshot") if isinstance(context_store, dict) else None
    if not refs and not framework_refs:
        return ""

    lines = [
        "RESOURCE REFERENCES:",
        "- These files are shared state for publisher + agents.",
        f"- tracker_json: {refs.get('resource_tracker', '')}",
        f"- events_jsonl: {refs.get('resource_events', '')}",
        f"- ui_state_json: {refs.get('ui_state', '')}",
        f"- ui_events_jsonl: {refs.get('ui_events', '')}",
        "- If you cite resource state, reference these paths explicitly in your output.",
    ]

    if isinstance(snapshot, dict) and snapshot:
        compact = {
            "mode": snapshot.get("mode"),
            "pressure_mode": snapshot.get("pressure_mode"),
            "queue": (snapshot.get("queue") or {}).get("status_counts"),
            "agents": ((snapshot.get("agents") or {}).get("summary") or {}),
        }
        lines.append("- Current snapshot:")
        lines.append(json.dumps(compact, indent=2))

    if isinstance(framework_refs, dict) and framework_refs:
        lines.extend(
            [
                "BOOK FRAMEWORK REFERENCES:",
                f"- framework_skeleton_json: {framework_refs.get('framework_skeleton', '')}",
                f"- arc_tracker_json: {framework_refs.get('arc_tracker', '')}",
                f"- progress_index_json: {framework_refs.get('progress_index', '')}",
                f"- agent_context_status_jsonl: {framework_refs.get('agent_context_status', '')}",
                "- Use these references to keep continuity, pacing, and unresolved arc state aligned.",
            ]
        )

    return "\n".join(lines)


def _normalize_list(value):
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def build_framework_skeleton(brief: dict, outline_payload: dict, chapter_spec: dict, chapter_number: int) -> dict:
    structure = outline_payload.get("book_structure") if isinstance(outline_payload, dict) else {}
    sections = chapter_spec.get("sections") if isinstance(chapter_spec, dict) else []
    section_entries = []
    for idx, item in enumerate(sections or [], start=1):
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or f"Section {idx}")
            goal = str(item.get("objective") or item.get("goal") or "")
        else:
            title = str(item or f"Section {idx}")
            goal = ""
        section_entries.append({"index": idx, "title": title, "goal": goal})

    return {
        "book_identity": {
            "title_working": brief.get("title_working"),
            "genre": brief.get("genre"),
            "audience": brief.get("audience"),
            "tone": brief.get("tone"),
            "target_word_count": brief.get("target_word_count"),
            "page_target": brief.get("page_target"),
        },
        "design_framework": {
            "constraints": _normalize_list(brief.get("constraints")),
            "acceptance_criteria": _normalize_list(brief.get("acceptance_criteria")),
            "book_structure": structure,
            "master_outline_markdown": str(outline_payload.get("master_outline_markdown") or ""),
        },
        "chapter_skeleton": {
            "chapter_number": chapter_number,
            "chapter_title": chapter_spec.get("chapter_title"),
            "purpose": chapter_spec.get("purpose"),
            "ending_hook": chapter_spec.get("ending_hook"),
            "target_words": chapter_spec.get("target_words"),
            "sections": section_entries,
            "must_include": _normalize_list(chapter_spec.get("must_include")),
            "must_avoid": _normalize_list(chapter_spec.get("must_avoid")),
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


def update_arc_tracker(existing: dict, *, chapter_number: int, chapter_title: str, section_title: str, next_writer_notes: dict, continuity_state: dict, canon_payload: dict, rubric_report: dict):
    tracker = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    tracker.setdefault("story_arcs", [])
    tracker.setdefault("character_arcs", [])
    tracker.setdefault("open_loops", [])
    tracker.setdefault("chapter_progress", [])

    loop_items = canon_payload.get("open_loops") if isinstance(canon_payload, dict) else []
    tracker["open_loops"] = _normalize_list(loop_items)

    notes = next_writer_notes if isinstance(next_writer_notes, dict) else {}
    state_updates = continuity_state.get("section_updates") if isinstance(continuity_state, dict) else []
    state_updates = state_updates if isinstance(state_updates, list) else []

    timeline_events = _normalize_list(notes.get("timeline_events"))
    unresolved_questions = _normalize_list(notes.get("unresolved_questions"))
    character_updates = _normalize_list(notes.get("character_state_updates"))

    if timeline_events:
        tracker["story_arcs"].append(
            {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "events": timeline_events,
            }
        )
    if character_updates:
        tracker["character_arcs"].append(
            {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "updates": character_updates,
            }
        )

    tracker["chapter_progress"].append(
        {
            "chapter_number": chapter_number,
            "chapter_title": chapter_title,
            "section_title": section_title,
            "continuity_updates_count": len(state_updates),
            "timeline_events": timeline_events,
            "unresolved_questions": unresolved_questions,
            "rubric_scores": (rubric_report or {}).get("scores") if isinstance(rubric_report, dict) else {},
            "updated_at": datetime.utcnow().isoformat(),
        }
    )
    tracker["last_updated"] = datetime.utcnow().isoformat()
    return tracker


def write_agent_context_status(path: Path, payload: dict):
    append_jsonl(
        path,
        {
            "timestamp": datetime.utcnow().isoformat(),
            "event": "agent_context_status",
            "details": payload,
        },
    )


def gate_chapter_spec(spec):
    sections = spec.get("sections") or []
    if not isinstance(sections, list) or len(sections) < 2:
        return False, "chapter spec requires at least 2 sections"
    return True, "ok"


def gate_developmental(report):
    if not isinstance(report, dict):
        return False, "invalid developmental report"
    scores = report.get("scores") or {}
    if not isinstance(scores, dict):
        return False, "scores missing"
    values = [v for v in scores.values() if isinstance(v, (int, float))]
    if not values:
        return False, "scores empty"
    if min(values) < 4:
        return False, "one or more scores below 4"
    if (sum(values) / len(values)) < 4:
        return False, "average score below 4"
    return True, "pass"


def gate_publisher(report):
    if not isinstance(report, dict):
        return False, "invalid publisher report"
    decision = str(report.get("decision", "")).upper()
    if decision == "APPROVE":
        return True, "approve"
    return False, "publisher requested revision"


def gate_publisher_brief(report):
    if not isinstance(report, dict):
        return False, "invalid brief payload"

    constraints = report.get("constraints")
    acceptance = report.get("acceptance_criteria")
    if not isinstance(constraints, list) or len(constraints) < 5:
        return False, "constraints must be a list with at least 5 items"
    if not isinstance(acceptance, list) or len(acceptance) < 5:
        return False, "acceptance_criteria must be a list with at least 5 items"

    return True, "ok"


def gate_research_dossier(text):
    content = str(text or "")
    lowered = content.lower()
    has_facts = "facts" in lowered
    if has_facts:
        return True, "ok"
    return False, "research output missing facts section"


def gate_architect_outline(payload):
    if not isinstance(payload, dict):
        return False, "invalid architect payload"

    # Normalize common alias keys so valid responses are not rejected for naming variance.
    alias_keys = [
        "master_outline_markdown",
        "master_outline",
        "outline_markdown",
        "master_outline_md",
    ]
    for key in alias_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            payload["master_outline_markdown"] = value
            break

    return True, "ok"


def gate_rubric_report(report):
    if not isinstance(report, dict):
        return False, "invalid rubric report"
    scores = report.get("scores") or {}

    values = []
    for key in RUBRIC_KEYS:
        value = scores.get(key)
        if not isinstance(value, (int, float)):
            return False, f"score for {key} is not numeric"
        values.append(float(value))

    if min(values) < 4:
        return False, "one or more rubric scores below 4"
    if (sum(values) / len(values)) < 4:
        return False, "rubric average below 4"

    next_notes = report.get("next_writer_notes") or {}
    if not isinstance(next_notes, dict):
        return False, "next_writer_notes missing"
    for required_key in [
        "focus_topics",
        "continuity_watch",
        "must_carry_forward",
        "character_state_updates",
        "timeline_events",
        "unresolved_questions",
    ]:
        if required_key not in next_notes:
            return False, f"next_writer_notes missing {required_key}"

    for list_key in ["focus_topics", "continuity_watch", "must_carry_forward"]:
        value = next_notes.get(list_key)
        if not isinstance(value, list) or not value:
            return False, f"next_writer_notes.{list_key} must be a non-empty list"

    return True, "pass"


def gate_no_blocking_issues(report):
    if not isinstance(report, dict):
        return False, "invalid consistency report"
    issues = report.get("blocking_issues") or []
    if not isinstance(issues, list):
        return False, "blocking_issues must be a list"
    if issues:
        return False, "consistency review found blocking issues"
    return True, "pass"


def validate_required_artifacts(run_dir: Path, expected_section_count: int):
    required = [
        run_dir / "00_brief/book_brief.json",
        run_dir / "01_research/research_dossier.md",
        run_dir / "02_outline/master_outline.md",
        run_dir / "02_outline/chapter_specs/chapter_01.json",
        run_dir / "03_canon/canon.json",
        run_dir / "03_canon/context_store.json",
        run_dir / "03_canon/session_handoffs.jsonl",
        run_dir / "03_canon/continuity_state.json",
        run_dir / "04_drafts/chapter_01/assembled.md",
        run_dir / "04_drafts/chapter_01/edited.md",
        run_dir / "04_drafts/chapter_01/copy_edited.md",
        run_dir / "04_drafts/chapter_01/proofread.md",
        run_dir / "05_reviews/assembly_reviews/chapter_assembly_review.json",
        run_dir / "05_reviews/developmental_report.json",
        run_dir / "05_reviews/rubric_report.json",
        run_dir / "05_reviews/continuity_report.json",
        run_dir / "05_reviews/next_writer_notes.json",
        run_dir / "06_final/manuscript_v1.md",
    ]

    for idx in range(1, expected_section_count + 1):
        required.append(run_dir / f"04_drafts/chapter_01/section_{idx:02d}.md")
        required.append(run_dir / f"05_reviews/section_reviews/section_{idx:02d}_review.json")

    missing = [str(path) for path in required if not path.exists()]
    return {"valid": len(missing) == 0, "missing": missing}


def _word_count(text: str) -> int:
    return len((text or "").split())


def derive_context_tracking_strategy(book_payload: dict, canon_payload: dict, chapter_spec: dict):
    text_blob = " ".join(
        [
            str((book_payload or {}).get("premise", "")),
            str((chapter_spec or {}).get("purpose", "")),
            str((chapter_spec or {}).get("chapter_title", "")),
            str((canon_payload or {}).get("style_guide", "")),
        ]
    ).lower()

    time_markers = ["timeline", "year", "day", "night", "before", "after", "sequence", "history"]
    situation_markers = ["forgot", "memory", "identity", "amnesia", "confusion", "recall", "remember"]

    time_hits = sum(1 for marker in time_markers if marker in text_blob)
    situation_hits = sum(1 for marker in situation_markers if marker in text_blob)

    mode = "time_based" if time_hits >= situation_hits else "situation_based"
    return {
        "mode": mode,
        "time_hits": time_hits,
        "situation_hits": situation_hits,
        "policy": (
            "Prioritize chronological consistency and temporal causality."
            if mode == "time_based"
            else "Prioritize character-state/situation continuity and state transitions."
        ),
    }


def build_relevant_chapter_notes(rolling_memory: dict, chapter_number: int, section_goal: str, max_items: int = 6):
    chapter_summaries = (rolling_memory or {}).get("chapter_summaries") or []
    section_goal_tokens = {tok for tok in re.findall(r"[a-z0-9]+", (section_goal or "").lower()) if len(tok) > 3}

    scored = []
    for item in chapter_summaries:
        if not isinstance(item, dict):
            continue
        item_ch = int(item.get("chapter_number") or 0)
        recency = max(0, chapter_number - item_ch)
        summary_text = " ".join(
            [
                str(item.get("summary", "")),
                json.dumps(item.get("next_writer_notes", {})),
            ]
        ).lower()
        overlap = sum(1 for tok in section_goal_tokens if tok in summary_text)
        score = overlap * 10 - recency
        scored.append((score, item))

    scored.sort(key=lambda t: t[0], reverse=True)
    selected = [entry for _, entry in scored[:max_items]]
    unresolved = []
    for item in selected:
        loops = item.get("open_loops") or []
        if isinstance(loops, list):
            unresolved.extend(loops)

    return {
        "selected_notes": selected,
        "unresolved_threads": unresolved[:20],
        "note_usage_rule": "Use notes only when they support current section goals and do not conflict with current canon.",
    }


def chunk_items_by_word_budget(items, max_words):
    groups = []
    oversize = []
    current = []
    current_words = 0

    for item in items:
        text = item.get("text", "")
        words = _word_count(text)

        if words > max_words:
            oversize.append({"id": item.get("id"), "words": words, "max_words": max_words})
            if current:
                groups.append(current)
                current = []
                current_words = 0
            groups.append([item])
            continue

        if not current or (current_words + words) <= max_words:
            current.append(item)
            current_words += words
        else:
            groups.append(current)
            current = [item]
            current_words = words

    if current:
        groups.append(current)

    return groups, oversize


def _load_changes_log(path: Path):
    entries = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = parse_json_block(line, fallback=None)
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries


def _build_stage_attempt_summary(entries):
    summary = {}
    for entry in entries:
        action = entry.get("action")
        details = entry.get("details") or {}
        stage = details.get("stage")
        if not stage:
            continue
        bucket = summary.setdefault(stage, {"attempts": 0, "last_gate_ok": None, "last_gate_message": None})
        if action == "stage_start":
            bucket["attempts"] += 1
        elif action == "stage_result":
            bucket["last_gate_ok"] = details.get("gate_ok")
            bucket["last_gate_message"] = details.get("gate_message")
    return summary


def build_retro_report(run_dir: Path, summary: dict):
    reviews_dir = run_dir / "05_reviews"
    drafts_dir = run_dir / "04_drafts/chapter_01"
    final_dir = run_dir / "06_final"

    developmental = parse_json_block(read_text(reviews_dir / "developmental_report.json", "{}"), fallback={})
    rubric = parse_json_block(read_text(reviews_dir / "rubric_report.json", "{}"), fallback={})
    continuity = parse_json_block(read_text(reviews_dir / "continuity_report.json", "{}"), fallback={})
    publisher = parse_json_block(read_text(reviews_dir / "publisher_report.json", "{}"), fallback={})
    next_writer_notes = parse_json_block(read_text(reviews_dir / "next_writer_notes.json", "{}"), fallback={})
    changes_entries = _load_changes_log(run_dir / "changes.log")

    manuscript_text = read_text(final_dir / "manuscript_v1.md", "")
    stage_attempts = _build_stage_attempt_summary(changes_entries)
    rubric_scores = rubric.get("scores") or {}

    retro = {
        "run": {
            "run_dir": str(run_dir),
            "title": summary.get("title"),
            "chapter_number": summary.get("chapter_number"),
            "chapter_title": summary.get("chapter_title"),
            "section_title": summary.get("section_title"),
            "created_at": datetime.utcnow().isoformat(),
        },
        "output": {
            "final_word_count": _word_count(manuscript_text),
            "publisher_decision": summary.get("publisher_decision"),
            "artifact_validation": summary.get("artifact_validation"),
        },
        "quality": {
            "developmental": developmental,
            "rubric_scores": rubric_scores,
            "continuity": continuity,
            "publisher": publisher,
        },
        "workflow": {
            "stage_attempts": stage_attempts,
            "log_entries": len(changes_entries),
        },
        "handoff": {
            "next_writer_notes": next_writer_notes,
            "has_focus_topics": bool((next_writer_notes.get("focus_topics") if isinstance(next_writer_notes, dict) else None)),
            "has_continuity_watch": bool((next_writer_notes.get("continuity_watch") if isinstance(next_writer_notes, dict) else None)),
        },
    }

    low_scores = []
    if isinstance(rubric_scores, dict):
        for key, value in rubric_scores.items():
            if isinstance(value, (int, float)) and value < 4:
                low_scores.append({"dimension": key, "score": value})
    retro["improvement_targets"] = low_scores

    return retro


def build_retro_markdown(retro: dict) -> str:
    run = retro.get("run") or {}
    out = retro.get("output") or {}
    workflow = retro.get("workflow") or {}
    handoff = retro.get("handoff") or {}
    quality = retro.get("quality") or {}
    scores = quality.get("rubric_scores") or {}
    low_scores = retro.get("improvement_targets") or []

    lines = [
        "# Book Run Retrospective",
        "",
        f"- Title: {run.get('title', '')}",
        f"- Chapter: {run.get('chapter_number', '')} - {run.get('chapter_title', '')}",
        f"- Section: {run.get('section_title', '')}",
        f"- Publisher decision: {out.get('publisher_decision', '')}",
        f"- Final word count: {out.get('final_word_count', 0)}",
        f"- Log entries: {workflow.get('log_entries', 0)}",
        "",
        "## Rubric Scores",
    ]

    if isinstance(scores, dict) and scores:
        for key in sorted(scores.keys()):
            lines.append(f"- {key}: {scores.get(key)}")
    else:
        lines.append("- No rubric scores found")

    lines.extend([
        "",
        "## Continuity Handoff",
        f"- Has focus topics: {handoff.get('has_focus_topics', False)}",
        f"- Has continuity watch: {handoff.get('has_continuity_watch', False)}",
        "",
        "## Improvement Targets",
    ])

    if low_scores:
        for item in low_scores:
            lines.append(f"- {item.get('dimension')}: {item.get('score')}")
    else:
        lines.append("- None (all scored dimensions >= 4)")

    lines.extend([
        "",
        "## Stage Attempts",
    ])

    stage_attempts = (workflow.get("stage_attempts") or {})
    if stage_attempts:
        for stage, data in stage_attempts.items():
            lines.append(
                f"- {stage}: attempts={data.get('attempts', 0)}, gate_ok={data.get('last_gate_ok')}, gate_message={data.get('last_gate_message')}"
            )
    else:
        lines.append("- No stage attempt data found")

    return "\n".join(lines) + "\n"


def load_recent_jsonl(path: Path, limit: int = 50):
    if not path.exists():
        return []
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    tail = raw_lines[-max(1, int(limit)):]
    rows = []
    for line in tail:
        item = parse_json_block(line, fallback=None)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def build_quality_failure_review_markdown(entries: list) -> str:
    lines = [
        "# Quality Failure Review",
        "",
        "Recent quality gate failures from perpetual tracking log.",
        "",
    ]
    if not entries:
        lines.append("- No recent quality gate failures recorded.")
        return "\n".join(lines) + "\n"

    for item in entries:
        lines.append(
            "- "
            + f"stage={item.get('stage', '')}, "
            + f"profile={item.get('profile', '')}, "
            + f"model={item.get('model', '')}, "
            + f"attempt={item.get('attempt', '')}, "
            + f"message={item.get('gate_message', '')}"
        )
    return "\n".join(lines) + "\n"


def run_stage(
    *,
    orchestrator,
    lock_manager,
    changes_log,
    context_store,
    stage_id,
    agent_name,
    profile_name,
    prompt,
    output_path=None,
    parse_json=False,
    output_schema=None,
    gate_fn=None,
    max_retries=2,
    diagnostics_path=None,
    verbose=False,
):
    stage_instantiated_at = datetime.utcnow().isoformat()
    run_journal_path = context_store.get("_run_journal_path") if isinstance(context_store, dict) else None
    run_journal_path = Path(run_journal_path) if run_journal_path else None

    lock_before = lock_manager.get_lock_status(name="changes_log")
    lock_manager.log_agent_change(
        changes_log,
        agent_name,
        "lock_check",
        {"stage": stage_id, "lock_before": lock_before},
    )
    if verbose:
        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_instantiated",
            {"stage": stage_id, "instantiated_at": stage_instantiated_at},
        )
    append_run_event(
        run_journal_path,
        "stage_instantiated",
        {
            "stage": stage_id,
            "agent": agent_name,
            "profile": profile_name,
            "instantiated_at": stage_instantiated_at,
        },
    )

    last_raw = ""
    last_error = ""
    last_feedback = None
    parsed = None
    recovery_attempts = 1

    def persist_output(payload):
        if output_path is None:
            return
        with lock_manager.edit_lock(name="publisher_store"):
            if parse_json:
                write_json(output_path, payload)
            else:
                write_text(output_path, str(payload))

    def finalize_success(*, payload, raw_output, attempt, attempt_started_at, attempt_completed_at, recovered, gate_message, recovery_profile=None):
        persist_output(payload)

        if diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "event": "stage_success",
                    "instantiated_at": stage_instantiated_at,
                    "attempt": attempt,
                    "attempt_started_at": attempt_started_at,
                    "attempt_completed_at": attempt_completed_at,
                    "raw_output": raw_output,
                    "parsed_output": payload if parse_json else None,
                    "gate_ok": True,
                    "gate_message": gate_message,
                    "recovered": recovered,
                    "recovery_profile": recovery_profile,
                    "output_path": str(output_path) if output_path else None,
                },
            )

        context_store[stage_id] = {
            "agent": agent_name,
            "profile": profile_name,
            "output_path": str(output_path) if output_path else None,
            "lock_after": lock_manager.get_lock_status(name="changes_log"),
            "recovered": recovered,
        }

        handoff_dir = context_store.get("_handoff_dir")
        if handoff_dir:
            handoff_payload = {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "output_path": str(output_path) if output_path else None,
                "lock_after": context_store[stage_id]["lock_after"],
                "publisher_visible": True,
                "recovered": recovered,
            }
            write_json(Path(handoff_dir) / f"{stage_id}.json", handoff_payload)
            handoff_md = [
                f"# Stage Handoff: {stage_id}",
                "",
                f"- Agent: {agent_name}",
                f"- Profile: {profile_name}",
                f"- Output Path: {str(output_path) if output_path else 'inline'}",
                f"- Recovered: {recovered}",
                "",
                "## LLM Response",
                "",
            ]
            if parse_json:
                handoff_md.extend([
                    "```json",
                    json.dumps(payload, indent=2),
                    "```",
                ])
            else:
                handoff_md.append(str(payload))
            write_text(Path(handoff_dir) / f"{stage_id}.md", "\n".join(handoff_md) + "\n")

        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_complete",
            {
                "stage": stage_id,
                "attempt": attempt,
                "output_path": str(output_path) if output_path else None,
                "lock_after": context_store[stage_id]["lock_after"],
                "report_to_publisher": True,
                "recovered": recovered,
                "recovery_profile": recovery_profile,
            },
        )
        append_run_event(
            run_journal_path,
            "stage_complete",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "attempt": attempt,
                "output_path": str(output_path) if output_path else None,
                "recovered": recovered,
                "recovery_profile": recovery_profile,
            },
        )
        return payload

    def build_recovery_prompt(current_prompt):
        feedback_block = ""
        if isinstance(last_feedback, dict) and last_feedback:
            feedback_block = "\n\nLAST STRUCTURED OUTPUT:\n" + json.dumps(last_feedback, indent=2)
        elif last_feedback is not None:
            feedback_block = "\n\nLAST OUTPUT:\n" + str(last_feedback)
        raw_block = f"\n\nLAST RAW OUTPUT:\n{last_raw}" if last_raw else ""
        return (
            current_prompt
            + feedback_block
            + raw_block
            + "\n\nRECOVERY INSTRUCTION:\n"
            + "Your last attempt failed. Repair the output so it fully satisfies the required format and all quality constraints."
            + f"\nFailure reason: {last_error or 'unknown failure'}"
            + "\nDo not explain the failure. Return only the corrected final output."
        )

    for attempt in range(1, max_retries + 2):
        resource_block = build_resource_reference_block(context_store)
        prompt_with_feedback = prompt
        if resource_block:
            prompt_with_feedback = prompt_with_feedback + "\n\n" + resource_block

        if last_error:
            feedback_block = ""
            if last_feedback is not None:
                # If reviewer output is JSON, pretty-print it; else, show as text
                if isinstance(last_feedback, dict):
                    feedback_block = "\n\nPREVIOUS REVIEWER FEEDBACK (JSON):\n" + json.dumps(last_feedback, indent=2)
                else:
                    feedback_block = "\n\nPREVIOUS REVIEWER FEEDBACK:\n" + str(last_feedback)
            prompt_with_feedback = (
                prompt_with_feedback
                + feedback_block
                + "\n\nPREVIOUS ATTEMPT FAILED QUALITY GATE:\n"
                + last_error
                + "\nRevise output to satisfy all constraints and output format."
            )

        # Capture resolved route/model/options before invocation for upstream diagnostics.
        resolved_plan = None
        try:
            resolved_plan = orchestrator.plan_request(
                prompt_with_feedback,
                profile_name=profile_name,
                stream_override=False,
            )
        except AgentStackError:
            resolved_plan = None

        # Diagnostics: log prompt and context before agent call
        if diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                    "event": "pre_agent_call",
                    "prompt": prompt_with_feedback,
                    "context_store": dict(context_store),
                    "resolved_route": (resolved_plan or {}).get("route"),
                    "resolved_model": (resolved_plan or {}).get("model"),
                    "resolved_profile": ((resolved_plan or {}).get("profile") or {}).get("name"),
                    "resolved_stream": (resolved_plan or {}).get("stream"),
                    "resolved_options": (resolved_plan or {}).get("options"),
                },
            )
        attempt_started_at = datetime.utcnow().isoformat()
        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_start",
            {
                "stage": stage_id,
                "attempt": attempt,
                "route": (resolved_plan or {}).get("route"),
                "model": (resolved_plan or {}).get("model"),
                "profile": ((resolved_plan or {}).get("profile") or {}).get("name"),
            },
        )
        append_run_event(
            run_journal_path,
            "stage_attempt_start",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "attempt": attempt,
                "route": (resolved_plan or {}).get("route"),
                "model": (resolved_plan or {}).get("model"),
            },
        )

        try:
            raw = orchestrator.handle_request_with_overrides(
                prompt_with_feedback,
                profile_name=profile_name,
                stream_override=False,
            )
        except AgentStackError as e:
            last_error = f"[{e.code}] {e}"
            last_feedback = {"error": str(e), "error_code": e.code}
            if diagnostics_path is not None:
                append_jsonl(
                    diagnostics_path,
                    {
                        "stage": stage_id,
                        "agent": agent_name,
                        "profile": profile_name,
                        "attempt": attempt,
                        "event": "agent_call_error",
                        "error": str(e),
                        "error_code": e.code,
                        "prompt": prompt_with_feedback,
                        "context_store": dict(context_store),
                    },
                )
            lock_manager.log_agent_change(
                changes_log,
                agent_name,
                "stage_result",
                {
                    "stage": stage_id,
                    "attempt": attempt,
                    "gate_ok": False,
                    "gate_message": last_error,
                },
            )
            append_run_event(
                run_journal_path,
                "stage_attempt_error",
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                    "error": str(e),
                    "error_code": e.code,
                },
            )
            continue
        except Exception as e:
            wrapped_error = AgentUnexpectedError(
                f"Stage attempt failed unexpectedly: {e}",
                details={
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                },
            )
            last_error = f"[{wrapped_error.code}] {wrapped_error}"
            last_feedback = {"error": str(wrapped_error), "error_code": wrapped_error.code}
            if diagnostics_path is not None:
                append_jsonl(
                    diagnostics_path,
                    {
                        "stage": stage_id,
                        "agent": agent_name,
                        "profile": profile_name,
                        "attempt": attempt,
                        "event": "agent_call_error",
                        "error": str(wrapped_error),
                        "error_code": wrapped_error.code,
                        "prompt": prompt_with_feedback,
                        "context_store": dict(context_store),
                    },
                )
            lock_manager.log_agent_change(
                changes_log,
                agent_name,
                "stage_result",
                {
                    "stage": stage_id,
                    "attempt": attempt,
                    "gate_ok": False,
                    "gate_message": last_error,
                },
            )
            append_run_event(
                run_journal_path,
                "stage_attempt_error",
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "attempt": attempt,
                    "error": str(wrapped_error),
                    "error_code": wrapped_error.code,
                },
            )
            continue
        attempt_completed_at = datetime.utcnow().isoformat()
        last_raw = raw
        parsed = parse_json_block(raw, fallback={}) if parse_json else raw

        gate_ok = True
        gate_message = "ok"
        if parse_json and output_schema:
            gate_ok, gate_message = validate_stage_payload(output_schema, parsed)
        if gate_fn:
            if gate_ok:
                gate_ok, gate_message = gate_fn(parsed)

        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_result",
            {
                "stage": stage_id,
                "attempt": attempt,
                "gate_ok": gate_ok,
                "gate_message": gate_message,
            },
        )
        append_run_event(
            run_journal_path,
            "stage_attempt_result",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "attempt": attempt,
                "gate_ok": gate_ok,
                "gate_message": gate_message,
            },
        )

        if gate_ok:
            orchestrator.record_quality_gate_success(
                stage=stage_id,
                agent=agent_name,
                profile=profile_name,
                model=(resolved_plan or {}).get("model"),
                run_journal_path=str(run_journal_path) if run_journal_path else None,
                attempt=attempt,
            )
            return finalize_success(
                payload=parsed,
                raw_output=raw,
                attempt=attempt,
                attempt_started_at=attempt_started_at,
                attempt_completed_at=attempt_completed_at,
                recovered=False,
                gate_message=gate_message,
            )

        last_error = gate_message
        orchestrator.record_quality_gate_failure(
            stage=stage_id,
            agent=agent_name,
            profile=profile_name,
            model=(resolved_plan or {}).get("model"),
            gate_message=gate_message,
            run_journal_path=str(run_journal_path) if run_journal_path else None,
            attempt=attempt,
        )
        last_feedback = parsed
        if verbose and diagnostics_path is not None:
            append_jsonl(
                diagnostics_path,
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": profile_name,
                    "instantiated_at": stage_instantiated_at,
                    "attempt": attempt,
                    "attempt_started_at": attempt_started_at,
                    "attempt_completed_at": attempt_completed_at,
                    "prompt": prompt_with_feedback,
                    "raw_output": raw,
                    "parsed_output": parsed if parse_json else None,
                    "gate_ok": gate_ok,
                    "gate_message": gate_message,
                },
            )
    recovery_profile_name = profile_name
    for recovery_attempt in range(1, recovery_attempts + 1):
        recovery_prompt = build_recovery_prompt(prompt)
        recovery_started_at = datetime.utcnow().isoformat()
        append_run_event(
            run_journal_path,
            "stage_recovery_start",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": recovery_profile_name,
                "attempt": recovery_attempt,
                "failure_reason": last_error,
            },
        )
        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_recovery_start",
            {
                "stage": stage_id,
                "attempt": recovery_attempt,
                "profile": recovery_profile_name,
                "failure_reason": last_error,
            },
        )
        try:
            raw = orchestrator.handle_request_with_overrides(
                recovery_prompt,
                profile_name=recovery_profile_name,
                stream_override=False,
            )
        except AgentStackError as e:
            last_error = f"[{e.code}] {e}"
            last_feedback = {"error": str(e), "error_code": e.code}
            append_run_event(
                run_journal_path,
                "stage_recovery_error",
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": recovery_profile_name,
                    "attempt": recovery_attempt,
                    "error": str(e),
                    "error_code": e.code,
                },
            )
            continue
        except Exception as e:
            wrapped_error = AgentUnexpectedError(
                f"Stage recovery failed unexpectedly: {e}",
                details={
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": recovery_profile_name,
                    "attempt": recovery_attempt,
                },
            )
            last_error = f"[{wrapped_error.code}] {wrapped_error}"
            last_feedback = {"error": str(wrapped_error), "error_code": wrapped_error.code}
            append_run_event(
                run_journal_path,
                "stage_recovery_error",
                {
                    "stage": stage_id,
                    "agent": agent_name,
                    "profile": recovery_profile_name,
                    "attempt": recovery_attempt,
                    "error": str(wrapped_error),
                    "error_code": wrapped_error.code,
                },
            )
            continue

        recovery_completed_at = datetime.utcnow().isoformat()
        last_raw = raw
        parsed = parse_json_block(raw, fallback={}) if parse_json else raw
        gate_ok = True
        gate_message = "ok"
        if parse_json and output_schema:
            gate_ok, gate_message = validate_stage_payload(output_schema, parsed)
        if gate_fn:
            if gate_ok:
                gate_ok, gate_message = gate_fn(parsed)
        append_run_event(
            run_journal_path,
            "stage_recovery_result",
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": recovery_profile_name,
                "attempt": recovery_attempt,
                "gate_ok": gate_ok,
                "gate_message": gate_message,
            },
        )
        if gate_ok:
            orchestrator.record_quality_gate_success(
                stage=stage_id,
                agent=agent_name,
                profile=recovery_profile_name,
                model=None,
                run_journal_path=str(run_journal_path) if run_journal_path else None,
                attempt=max_retries + recovery_attempt,
            )
            return finalize_success(
                payload=parsed,
                raw_output=raw,
                attempt=max_retries + recovery_attempt + 1,
                attempt_started_at=recovery_started_at,
                attempt_completed_at=recovery_completed_at,
                recovered=True,
                gate_message=gate_message,
                recovery_profile=recovery_profile_name,
            )
        last_error = gate_message
        orchestrator.record_quality_gate_failure(
            stage=stage_id,
            agent=agent_name,
            profile=recovery_profile_name,
            model=None,
            gate_message=gate_message,
            run_journal_path=str(run_journal_path) if run_journal_path else None,
            attempt=max_retries + recovery_attempt,
        )
        last_feedback = parsed

    lock_manager.log_agent_change(
        changes_log,
        agent_name,
        "stage_failure",
        {
            "stage": stage_id,
            "attempt": max_retries + recovery_attempts + 1,
            "gate_ok": False,
            "gate_message": last_error,
        },
    )
    append_run_event(
        run_journal_path,
        "stage_failure",
        {
            "stage": stage_id,
            "agent": agent_name,
            "profile": profile_name,
            "gate_message": last_error,
        },
    )
    if diagnostics_path is not None:
        append_jsonl(
            diagnostics_path,
            {
                "stage": stage_id,
                "agent": agent_name,
                "profile": profile_name,
                "event": "stage_failure",
                "attempt": max_retries + recovery_attempts + 1,
                "gate_ok": False,
                "gate_message": last_error,
            },
        )
    raise StageQualityGateError(
        f"{stage_id} failed quality gate after retries: {last_error}",
        details={"stage": stage_id, "agent": agent_name, "profile": profile_name, "last_error": last_error},
    )


def run_flow(args):
    import traceback
    from datetime import datetime
    orchestrator = OrchestratorAgent()
    lock_manager = AgentLockManager()
    # Parent-level log: start
    parent_log = {
        "timestamp": datetime.utcnow().isoformat(),
        "agent": "book-flow-parent",
        "action": "run_start",
        "details": {
            "input_title": getattr(args, 'title', None),
            "input_chapter": getattr(args, 'chapter_title', None),
            "input_section": getattr(args, 'section_title', None),
            "input_goal": getattr(args, 'section_goal', None),
        },
    }
    output_root = Path(args.output_dir).expanduser()
    book_slug = slugify(args.title) if hasattr(args, "title") else "book-error"
    book_root = output_root / book_slug
    runs_root = book_root / "runs"
    ensure_dir(runs_root)
    framework_root = book_root / "framework"
    ensure_dir(framework_root)

    framework_skeleton_path = framework_root / "framework_skeleton.json"
    arc_tracker_path = framework_root / "arc_tracker.json"
    progress_index_path = framework_root / "progress_index.json"
    agent_context_status_path = framework_root / "agent_context_status.jsonl"

    if not framework_skeleton_path.exists():
        write_json(
            framework_skeleton_path,
            {
                "book_identity": {
                    "title_working": args.title,
                    "genre": args.genre,
                    "audience": args.audience,
                    "tone": args.tone,
                    "target_word_count": args.target_word_count,
                    "page_target": args.page_target,
                },
                "design_framework": {
                    "constraints": [],
                    "acceptance_criteria": [],
                    "book_structure": {},
                    "master_outline_markdown": "",
                },
                "chapter_skeleton": {
                    "chapter_number": args.chapter_number,
                    "chapter_title": args.chapter_title,
                    "purpose": args.section_goal,
                    "ending_hook": "",
                    "target_words": args.writer_words,
                    "sections": [],
                    "must_include": [],
                    "must_avoid": [],
                },
                "generated_at": datetime.utcnow().isoformat(),
            },
        )
    if not arc_tracker_path.exists():
        write_json(
            arc_tracker_path,
            {
                "story_arcs": [],
                "character_arcs": [],
                "open_loops": [],
                "chapter_progress": [],
                "last_updated": datetime.utcnow().isoformat(),
            },
        )
    if not progress_index_path.exists():
        write_json(
            progress_index_path,
            {
                "book": {"title": args.title},
                "completed_chapters": [],
                "last_run": None,
                "last_updated": datetime.utcnow().isoformat(),
            },
        )

    # Stable per-book history log for all requests tied to the same title.
    book_history_log = book_root / "book_history.jsonl"
    append_jsonl(book_history_log, parent_log)

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    run_name = f"{stamp}-ch{args.chapter_number:02d}-{slugify(args.section_title)}"
    run_dir = runs_root / run_name

    dirs = {
        "brief": run_dir / "00_brief",
        "research": run_dir / "01_research",
        "outline": run_dir / "02_outline",
        "chapter_specs": run_dir / "02_outline/chapter_specs",
        "canon": run_dir / "03_canon",
        "drafts_ch": run_dir / "04_drafts/chapter_01",
        "reviews": run_dir / "05_reviews",
        "section_reviews": run_dir / "05_reviews/section_reviews",
        "assembly_reviews": run_dir / "05_reviews/assembly_reviews",
        "final": run_dir / "06_final",
        "handoff": run_dir / "handoff",
        "diagnostics": run_dir / "diagnostics",
    }
    for path in dirs.values():
        ensure_dir(path)

    changes_log = run_dir / "changes.log"
    diagnostics_always = str(os.environ.get("BOOK_FLOW_DIAGNOSTICS_ALWAYS", "true")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    diagnostics_log = (dirs["diagnostics"] / "agent_diagnostics.jsonl") if (args.verbose or diagnostics_always) else None
    if not changes_log.exists():
        write_text(changes_log, "")

    context_store = {
        "book": {
            "title": args.title,
            "genre": args.genre,
            "audience": args.audience,
            "tone": args.tone,
            "premise": args.premise,
            "target_word_count": args.target_word_count,
            "page_target": args.page_target,
        },
        "chapter": {
            "number": args.chapter_number,
            "title": args.chapter_title,
            "section_title": args.section_title,
            "section_goal": args.section_goal,
            "writer_words": args.writer_words,
        },
    }
    context_store["_handoff_dir"] = str(dirs["handoff"])
    run_journal = run_dir / "run_journal.jsonl"
    context_store["_run_journal_path"] = str(run_journal)
    append_run_event(
        run_journal,
        "run_start",
        {
            "title": args.title,
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
        },
    )

    resource_tracker_path = Path(
        str(
            getattr(
                args,
                "resource_tracker_path",
                "/home/daravenrk/dragonlair/book_project/resource_tracker.json",
            )
        )
    )
    resource_events_path = Path(
        str(
            getattr(
                args,
                "resource_events_path",
                "/home/daravenrk/dragonlair/book_project/resource_events.jsonl",
            )
        )
    )
    ui_state_path = Path("/home/daravenrk/dragonlair/book_project/webui_state.json")
    ui_events_path = Path("/home/daravenrk/dragonlair/book_project/webui_events.jsonl")
    context_store["_resource_refs"] = {
        "resource_tracker": str(resource_tracker_path),
        "resource_events": str(resource_events_path),
        "ui_state": str(ui_state_path),
        "ui_events": str(ui_events_path),
    }
    context_store["_framework_refs"] = {
        "framework_skeleton": str(framework_skeleton_path),
        "arc_tracker": str(arc_tracker_path),
        "progress_index": str(progress_index_path),
        "agent_context_status": str(agent_context_status_path),
    }
    context_store["_resource_snapshot"] = read_json(resource_tracker_path, default={})
    write_json(dirs["handoff"] / "resource_references.json", {
        "refs": context_store["_resource_refs"],
        "snapshot": context_store["_resource_snapshot"],
    })

    # 1) Publisher brief
    publisher_contract = build_contract(
        role="Publisher / Executive Agent",
        objective="Define book brief and stage acceptance criteria for this run.",
        constraints=[
            "Return JSON only",
            "Include title_working, target_word_count, page_target, constraints, acceptance_criteria",
            "No prose outside JSON",
        ],
        inputs=context_store,
        output_format='JSON object with fields: title_working, genre, audience, target_word_count, page_target, tone, constraints, acceptance_criteria',
        failure_conditions=["missing fields", "invalid JSON", "vague acceptance criteria"],
    )
    # --- Publisher Brief with Title Generation and User Selection ---
    brief = None
    required_fields = [
        "title_working", "genre", "audience", "target_word_count", "page_target", "tone", "constraints", "acceptance_criteria"
    ]
    for attempt in range(args.max_retries + 1):
        brief = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id="publisher_brief",
            agent_name="book-publisher-brief",
            profile_name=args.publisher_brief_profile,
            prompt=publisher_contract,
            output_path=dirs["brief"] / "book_brief.json",
            parse_json=True,
            output_schema="publisher_brief",
            gate_fn=gate_publisher_brief,
            max_retries=1,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
        )
        missing = [f for f in required_fields if not brief.get(f)]
        # Fallback: always set title_working if missing, before breaking
        if "title_working" in missing:
            brief["title_working"] = args.title
            missing = [f for f in required_fields if not brief.get(f)]
        # Harden: after all attempts, always set before gate
        if attempt == args.max_retries and not brief.get("title_working"):
            brief["title_working"] = args.title
        # Debug: print brief before gate
        print("[DEBUG] publisher_brief fields just before gate:", json.dumps(brief, indent=2))
        missing = [f for f in required_fields if not brief.get(f)]
        if not missing:
            break
        # Try to fill missing fields from user input or model
        correction_path = Path("/home/daravenrk/dragonlair/book_project/webui_correction.json")
        user_data = {}
        if correction_path.exists():
            try:
                with open(correction_path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
            except (json.JSONDecodeError, OSError):
                user_data = {}
        for field in missing:
            # 1. User input
            if user_data.get(field):
                brief[field] = user_data[field]
                continue
            # 2. Model suggestion
            if field == "title_working":
                # Already set above, skip
                continue
            elif field == "genre":
                brief[field] = args.genre
            elif field == "audience":
                brief[field] = args.audience
            elif field == "target_word_count":
                brief[field] = args.target_word_count
            elif field == "page_target":
                brief[field] = args.page_target
            elif field == "tone":
                brief[field] = args.tone
            elif field == "constraints":
                # Model suggestion for constraints
                constraints_prompt = f"Suggest 5 concrete constraints for a book with the following premise and genre.\nPremise: {args.premise}\nGenre: {args.genre}\nReturn a JSON array of strings."
                try:
                    constraints_response = orchestrator.handle_request_with_overrides(
                        constraints_prompt,
                        profile_name=args.publisher_brief_profile,
                        stream_override=False,
                    )
                    constraints = json.loads(constraints_response) if isinstance(constraints_response, str) else []
                    if constraints:
                        brief[field] = constraints
                        continue
                except (AgentStackError, json.JSONDecodeError, TypeError, ValueError):
                    pass
                brief[field] = ["Must be original", "No plagiarism", "Consistent tone", "Meets genre expectations", "Engages target audience"]
            elif field == "acceptance_criteria":
                criteria_prompt = f"Suggest 5 measurable acceptance criteria for a book with the following premise and genre.\nPremise: {args.premise}\nGenre: {args.genre}\nReturn a JSON array of strings."
                try:
                    criteria_response = orchestrator.handle_request_with_overrides(
                        criteria_prompt,
                        profile_name=args.publisher_brief_profile,
                        stream_override=False,
                    )
                    criteria = json.loads(criteria_response) if isinstance(criteria_response, str) else []
                    if criteria:
                        brief[field] = criteria
                        continue
                except (AgentStackError, json.JSONDecodeError, TypeError, ValueError):
                    pass
                brief[field] = ["All sections present", "No major plot holes", "Meets word count", "Adheres to brief", "Passes editorial review"]
    context_store["permanent_memory"] = {"book_brief": brief}
    write_json(dirs["canon"] / "context_store.json", context_store)
    write_agent_context_status(
        agent_context_status_path,
        {
            "phase": "publisher_brief_complete",
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "expectations": {
                "constraints": _normalize_list(brief.get("constraints")),
                "acceptance_criteria": _normalize_list(brief.get("acceptance_criteria")),
            },
        },
    )

    # 2) Research
    research_contract = build_contract(
        role="Research Agent",
        objective="Produce research dossier and facts for chapter drafting.",
        constraints=["Return markdown only", "Include source caveats", "Separate facts from assumptions"],
        inputs={"book_brief": brief, "chapter": context_store["chapter"]},
        output_format="Markdown with headings: Overview, Facts, Worldbuilding Notes, Do-Not-Claim-Without-Review",
        failure_conditions=["missing facts", "no caveats", "not actionable"],
    )
    research_md = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="research",
        agent_name="book-researcher",
        profile_name="book-researcher",
        prompt=research_contract,
        output_path=dirs["research"] / "research_dossier.md",
        parse_json=False,
        gate_fn=gate_research_dossier,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 3) Architect
    architect_contract = build_contract(
        role="Concept Architect Agent",
        objective="Create master outline and book structure.",
        constraints=[
            "Return JSON only",
            "Include top-level keys master_outline_markdown, book_structure, pacing_notes",
            "master_outline_markdown must be non-empty markdown text",
            "book_structure must be an object or array",
            "Do not wrap output in markdown code fences",
        ],
        inputs={"book_brief": brief, "research_dossier": research_md},
        output_format=(
            'JSON object with keys: master_outline_markdown, book_structure, pacing_notes. '
            'Example: {"master_outline_markdown":"# Outline...","book_structure":{"acts":[]},"pacing_notes":"..."}'
        ),
        failure_conditions=["missing master outline", "missing structure", "invalid JSON"],
    )
    outline_payload = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="architect_outline",
        agent_name="book-architect",
        profile_name="book-architect",
        prompt=architect_contract,
        output_path=dirs["outline"] / "book_structure.json",
        parse_json=True,
        output_schema="architect_outline",
        gate_fn=gate_architect_outline,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )
    write_text(dirs["outline"] / "master_outline.md", str(outline_payload.get("master_outline_markdown", "")))

    # 4) Chapter planner
    planner_contract = build_contract(
        role="Chapter Planner Agent",
        objective="Generate a bounded chapter specification for the requested chapter.",
        constraints=["Return JSON only", "Include sections array", "Include must_include/must_avoid and ending_hook"],
        inputs={"book_brief": brief, "book_structure": outline_payload, "chapter": context_store["chapter"]},
        output_format='JSON with keys: chapter_number, chapter_title, purpose, target_words, sections, must_include, must_avoid, ending_hook',
        failure_conditions=["vague sections", "missing ending hook", "invalid JSON"],
    )
    chapter_spec = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="chapter_planner",
        agent_name="book-chapter-planner",
        profile_name="book-chapter-planner",
        prompt=planner_contract,
        output_path=dirs["chapter_specs"] / "chapter_01.json",
        parse_json=True,
        output_schema="chapter_planner",
        gate_fn=gate_chapter_spec,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    framework_skeleton = build_framework_skeleton(
        brief=brief,
        outline_payload=outline_payload,
        chapter_spec=chapter_spec,
        chapter_number=args.chapter_number,
    )
    write_json(framework_skeleton_path, framework_skeleton)
    write_agent_context_status(
        agent_context_status_path,
        {
            "phase": "framework_skeleton_updated",
            "chapter_number": args.chapter_number,
            "chapter_title": chapter_spec.get("chapter_title"),
            "section_title": args.section_title,
            "expectations": {
                "must_include": _normalize_list(chapter_spec.get("must_include")),
                "must_avoid": _normalize_list(chapter_spec.get("must_avoid")),
                "ending_hook": chapter_spec.get("ending_hook"),
            },
        },
    )


    # 5) Canon manager
    canon_contract = build_contract(
        role="Canon / Memory Agent",
        objective="Initialize and persist canon for this chapter run.",
        constraints=["Return JSON only", "Include canon, timeline, character_bible, open_loops, style_guide"],
        inputs={"book_brief": brief, "chapter_spec": chapter_spec, "rolling_context": context_store.get("rolling_memory", {})},
        output_format='JSON with keys: canon, timeline, character_bible, open_loops, style_guide',
        failure_conditions=["missing canon", "missing timeline", "invalid JSON"],
    )
    canon_payload = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="canon",
        agent_name="book-canon",
        profile_name="book-canon",
        prompt=canon_contract,
        output_path=dirs["canon"] / "canon.json",
        parse_json=True,
        output_schema="canon",
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )
    write_json(dirs["canon"] / "timeline.json", canon_payload.get("timeline", {}))
    write_json(dirs["canon"] / "character_bible.json", canon_payload.get("character_bible", {}))
    write_json(dirs["canon"] / "open_loops.json", canon_payload.get("open_loops", []))
    write_text(dirs["canon"] / "style_guide.md", str(canon_payload.get("style_guide", "")))
    context_tracking_strategy = derive_context_tracking_strategy(context_store.get("book", {}), canon_payload, chapter_spec)

    # --- Writing Assistant Worldbuilding Integration ---
    wa_context = {
        "genre": brief.get("genre", ""),
        "setting": canon_payload.get("canon", {}).get("setting", ""),
        "era": canon_payload.get("canon", {}).get("era", ""),
        "notes": brief.get("premise", ""),
        "needs": chapter_spec.get("purpose", ""),
        "themes": chapter_spec.get("purpose", ""),
        "history": canon_payload.get("timeline", {}),
        "focus": chapter_spec.get("purpose", ""),
        "diversity": "broad, global, inclusive",
    }
    names_md = generate_names(wa_context)
    technology_md = generate_technology(wa_context)
    personalities_md = generate_personalities(wa_context)
    dates_md = generate_dates_history(wa_context)
    # Write outputs to book_project/
    book_project_root = Path(args.output_dir)
    write_text(book_project_root / "names.md", names_md)
    write_text(book_project_root / "technology.md", technology_md)
    write_text(book_project_root / "personalities.md", personalities_md)
    write_text(book_project_root / "history.md", dates_md)

    # 6) Writer + section consistency review per section
    chapter_sections = chapter_spec.get("sections") or []
    if len(chapter_sections) < 2:
        raise ChapterSpecValidationError(
            "Chapter spec must provide at least 2 sections for sequencing/assembly consistency checks",
            details={"section_count": len(chapter_sections)},
        )

    section_texts = []
    section_summaries = []
    continuity_state = parse_json_block(
        read_text(dirs["canon"] / "continuity_state.json", "{}"),
        fallback={},
    )
    previous_next_writer_notes = parse_json_block(
        read_text(dirs["canon"] / "next_writer_notes.json", "{}"),
        fallback={},
    )

    for idx, section_item in enumerate(chapter_sections, start=1):
        section_title = ""
        section_goal = ""
        if isinstance(section_item, dict):
            section_title = str(section_item.get("title") or section_item.get("name") or f"Section {idx}")
            section_goal = str(section_item.get("objective") or section_item.get("goal") or "Advance chapter objective")
        else:
            section_title = str(section_item)
            section_goal = "Advance chapter objective"

        relevant_notes_packet = build_relevant_chapter_notes(
            context_store.get("rolling_memory", {}),
            args.chapter_number,
            section_goal,
            max_items=6,
        )
        local_task_memory = {
            "chapter_spec": chapter_spec,
            "canon": canon_payload,
            "recent_chapter_summaries": context_store.get("rolling_memory", {}).get("chapter_summaries", []),
            "previous_next_writer_notes": previous_next_writer_notes,
            "previous_section_summaries": section_summaries,
            "continuity_state": continuity_state,
            "context_tracking_strategy": context_tracking_strategy,
            "relevant_notes": relevant_notes_packet,
            "section_title": section_title,
            "section_goal": section_goal,
        }
        writer_contract = build_contract(
            role="Section Writer Agent",
            objective=f"Draft section {idx:02d} for the chapter.",
            constraints=[
                "Return markdown only",
                f"Target words around {args.writer_words}",
                "Respect canon and style guide",
                "Use chapter notes only if relevant to this section goal",
                "Follow provided context tracking strategy for continuity",
            ],
            inputs=local_task_memory,
            output_format="Markdown with heading, coherent section body, and transition sentence",
            failure_conditions=["canon contradiction", "missing transition", "off-topic section"],
        )
        section_text = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"writer_section_{idx:02d}",
            agent_name="book-writer",
            profile_name="book-writer",
            prompt=writer_contract,
            output_path=dirs["drafts_ch"] / f"section_{idx:02d}.md",
            parse_json=False,
            gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.6), "draft too short"),
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
        )
        section_texts.append(section_text)

        section_review_contract = build_contract(
            role="Section Continuity Reviewer",
            objective="Review drafted section for continuity/timeline/character-state contradictions before assembly.",
            constraints=["Return JSON only", "Include blocking_issues, warnings, section_summary, continuity_state_updates"],
            inputs={
                "section_title": section_title,
                "section_goal": section_goal,
                "section_text": section_text,
                "canon": canon_payload,
                "previous_section_summaries": section_summaries,
            },
            output_format='JSON with keys: blocking_issues, warnings, section_summary, continuity_state_updates',
            failure_conditions=["invalid JSON", "missing summary", "undetected major contradiction"],
        )
        section_review = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"section_review_{idx:02d}",
            agent_name="book-continuity",
            profile_name="book-continuity",
            prompt=section_review_contract,
            output_path=dirs["section_reviews"] / f"section_{idx:02d}_review.json",
            parse_json=True,
            output_schema="section_review",
            gate_fn=gate_no_blocking_issues,
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
        )
        section_summaries.append(
            {
                "section": idx,
                "title": section_title,
                "summary": section_review.get("section_summary", ""),
                "continuity_state_updates": section_review.get("continuity_state_updates", []),
            }
        )
        continuity_state.setdefault("section_updates", [])
        continuity_state["section_updates"].append(
            {
                "section": idx,
                "title": section_title,
                "updates": section_review.get("continuity_state_updates", []),
            }
        )

    # 6.5) Story architect review (post-writer concept/structure validation)
    story_architect_contract = build_contract(
        role="Story Architect Agent",
        objective="Validate concept and structure of the drafted section before downstream editing.",
        constraints=["Return JSON only", "Include concept_validation and structure_validation scores with notes"],
        inputs={"draft_sections": section_texts, "chapter_spec": chapter_spec, "book_structure": outline_payload},
        output_format='JSON with keys: concept_validation, structure_validation, notes, revision_focus',
        failure_conditions=["invalid JSON", "missing validation scores", "non-actionable notes"],
    )
    story_architect_review = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="story_architect_review",
        agent_name="book-architect",
        profile_name="book-architect",
        prompt=story_architect_contract,
        output_path=dirs["reviews"] / "story_architect_review.json",
        parse_json=True,
        output_schema="story_architect_review",
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 7) Assembler + assembly consistency review
    section_bundle_text = "\n\n".join([f"## Section {i + 1}\n\n{text}" for i, text in enumerate(section_texts)])

    merge_items = [
        {
            "id": f"section_{idx:02d}",
            "text": text,
            "summary": section_summaries[idx - 1] if idx - 1 < len(section_summaries) else {},
        }
        for idx, text in enumerate(section_texts, start=1)
    ]
    merge_stats = {
        "initial_sections": len(merge_items),
        "merge_levels": [],
        "oversize_items": [],
    }

    max_merge_words = max(400, int(args.merge_context_words))
    level = 0
    while len(merge_items) > 1:
        level += 1
        groups, oversize = chunk_items_by_word_budget(merge_items, max_merge_words)
        merge_stats["oversize_items"].extend(oversize)
        merge_stats["merge_levels"].append(
            {
                "level": level,
                "input_items": len(merge_items),
                "groups": len(groups),
                "max_merge_words": max_merge_words,
            }
        )

        next_level = []
        for group_idx, group in enumerate(groups, start=1):
            if len(group) == 1:
                next_level.append(group[0])
                continue

            group_texts = [item.get("text", "") for item in group]
            group_summaries = [item.get("summary", {}) for item in group]
            assembler_contract = build_contract(
                role="Chapter Assembler Agent",
                objective="Assemble chapter draft from section output.",
                constraints=["Return markdown only", "Remove repetition", "Smooth transitions"],
                inputs={
                    "sections": group_texts,
                    "section_summaries": group_summaries,
                    "chapter_spec": chapter_spec,
                    "canon": canon_payload,
                    "context_tracking_strategy": context_tracking_strategy,
                },
                output_format="Markdown chapter draft",
                failure_conditions=["repetitive output", "broken flow", "new major facts invented"],
            )
            merged_text = run_stage(
                orchestrator=orchestrator,
                lock_manager=lock_manager,
                changes_log=changes_log,
                context_store=context_store,
                stage_id=f"assembler_l{level:02d}_g{group_idx:02d}",
                agent_name="book-assembler",
                profile_name="book-assembler",
                prompt=assembler_contract,
                output_path=None,
                parse_json=False,
                gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.35), "merged chunk too short"),
                max_retries=args.max_retries,
                diagnostics_path=diagnostics_log,
                verbose=args.verbose,
            )
            next_level.append(
                {
                    "id": f"L{level:02d}G{group_idx:02d}",
                    "text": merged_text,
                    "summary": {
                        "level": level,
                        "group": group_idx,
                        "source_ids": [item.get("id") for item in group],
                    },
                }
            )

        merge_items = next_level

    assembled = merge_items[0].get("text", "") if merge_items else ""
    write_text(dirs["drafts_ch"] / "assembled.md", assembled)

    assembly_review_contract = build_contract(
        role="Assembly Continuity Reviewer",
        objective="Review assembled chapter for sequencing inconsistencies introduced by combining section drafts.",
        constraints=["Return JSON only", "Include blocking_issues, warnings, continuity_notes"],
        inputs={
            "section_bundle": section_bundle_text,
            "section_summaries": section_summaries,
            "assembled_chapter": assembled,
            "canon": canon_payload,
        },
        output_format='JSON with keys: blocking_issues, warnings, continuity_notes',
        failure_conditions=["invalid JSON", "missing continuity notes", "undetected sequence contradiction"],
    )
    run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="chapter_assembly_review",
        agent_name="book-continuity",
        profile_name="book-continuity",
        prompt=assembly_review_contract,
        output_path=dirs["assembly_reviews"] / "chapter_assembly_review.json",
        parse_json=True,
        output_schema="assembly_review",
        gate_fn=gate_no_blocking_issues,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 8) Developmental editor with scoring gate
    dev_contract = build_contract(
        role="Developmental Editor Agent",
        objective="Score chapter and return rewrite guidance if weak.",
        constraints=["Return JSON only", "Include pass, scores, notes, rewrite_instructions"],
        inputs={
            "assembled": assembled,
            "chapter_spec": chapter_spec,
            "acceptance_criteria": brief.get("acceptance_criteria", []),
            "story_architect_review": story_architect_review,
        },
        output_format='JSON with keys: pass, scores, notes, rewrite_instructions',
        failure_conditions=["missing score dimensions", "missing rewrite instructions on fail", "invalid JSON"],
    )
    dev_report = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="developmental_editor",
        agent_name="book-developmental-editor",
        profile_name="book-developmental-editor",
        prompt=dev_contract,
        output_path=dirs["reviews"] / "developmental_report.json",
        parse_json=True,
        output_schema="developmental_editor",
        gate_fn=gate_developmental,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 9) Line editor
    line_contract = build_contract(
        role="Line Editor / Style Agent",
        objective="Polish chapter while preserving meaning and canon.",
        constraints=["Return markdown only", "Enforce style guide", "Avoid cliches and repeated openings"],
        inputs={"assembled": assembled, "developmental_report": dev_report, "style_guide": canon_payload.get("style_guide", "")},
        output_format="Markdown polished chapter",
        failure_conditions=["style drift", "added contradictions", "meaning loss"],
    )
    polished = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="line_editor",
        agent_name="book-line-editor",
        profile_name="book-line-editor",
        prompt=line_contract,
        output_path=dirs["drafts_ch"] / "edited.md",
        parse_json=False,
        gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.7), "polished chapter too short"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 9.5) Copy editor stage
    copy_contract = build_contract(
        role="Copy Editor Agent",
        objective="Correct grammar and mechanics while preserving narrative meaning.",
        constraints=["Return markdown only", "Preserve plot and meaning", "Enforce grammar and consistency"],
        inputs={"polished": polished, "style_guide": canon_payload.get("style_guide", "")},
        output_format="Markdown copy-edited chapter",
        failure_conditions=["meaning drift", "new contradictions", "mechanical errors left unresolved"],
    )
    copy_edited = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="copy_editor",
        agent_name="book-copy-editor",
        profile_name="book-copy-editor",
        prompt=copy_contract,
        output_path=dirs["drafts_ch"] / "copy_edited.md",
        parse_json=False,
        gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.7), "copy-edited chapter too short"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 9.6) Proofreader stage
    proof_contract = build_contract(
        role="Proofreader Agent",
        objective="Run final typo/punctuation pass before review scoring.",
        constraints=["Return markdown only", "No structural rewrites", "No new facts"],
        inputs={"copy_edited": copy_edited},
        output_format="Markdown proofread chapter",
        failure_conditions=["typos remain", "punctuation defects", "introduced narrative changes"],
    )
    proofread = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="proofreader",
        agent_name="book-proofreader",
        profile_name="book-proofreader",
        prompt=proof_contract,
        output_path=dirs["drafts_ch"] / "proofread.md",
        parse_json=False,
        gate_fn=lambda text: (len(text.split()) >= int(args.writer_words * 0.7), "proofread chapter too short"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 9.7) Rubric reviewer stage (10-point scoring + next-writer handoff)
    rubric_contract = build_contract(
        role="Session Reviewer Agent",
        objective="Score this writing session using the full 10-point rubric and produce next-writer handoff notes.",
        constraints=[
            "Return JSON only",
            "Scores must include all 10 rubric dimensions",
            "Include next_writer_notes with focus topics and continuity carry-forward",
        ],
        inputs={
            "proofread": proofread,
            "chapter_spec": chapter_spec,
            "canon": canon_payload,
            "developmental_report": dev_report,
        },
        output_format='JSON with keys: scores{concept_validation,structure_validation,chapter_coherence,sentence_clarity,grammar_correction,continuity_tracking,fact_verification,tone_consistency,genre_compliance,reader_engagement_score}, notes, next_writer_notes',
        failure_conditions=["missing score keys", "scores below threshold", "missing next_writer_notes"],
    )
    rubric_report = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="session_reviewer",
        agent_name="book-continuity",
        profile_name="book-continuity",
        prompt=rubric_contract,
        output_path=dirs["reviews"] / "rubric_report.json",
        parse_json=True,
        output_schema="session_reviewer",
        gate_fn=gate_rubric_report,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    next_writer_notes = rubric_report.get("next_writer_notes", {})
    write_json(dirs["reviews"] / "next_writer_notes.json", next_writer_notes)
    write_json(dirs["canon"] / "next_writer_notes.json", next_writer_notes)

    continuity_state["next_writer_notes"] = next_writer_notes
    continuity_state["last_rubric_scores"] = rubric_report.get("scores", {})
    write_json(dirs["canon"] / "continuity_state.json", continuity_state)

    append_jsonl(
        dirs["canon"] / "session_handoffs.jsonl",
        {
            "timestamp": datetime.utcnow().isoformat(),
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "next_writer_notes": next_writer_notes,
            "rubric_scores": rubric_report.get("scores", {}),
        },
    )
    write_agent_context_status(
        agent_context_status_path,
        {
            "phase": "next_writer_handoff",
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "expectations": {
                "focus_topics": _normalize_list(next_writer_notes.get("focus_topics")),
                "continuity_watch": _normalize_list(next_writer_notes.get("continuity_watch")),
                "must_carry_forward": _normalize_list(next_writer_notes.get("must_carry_forward")),
            },
        },
    )

    # 10) Continuity audit
    continuity_contract = build_contract(
        role="Continuity / QA Agent",
        objective="Audit polished chapter for continuity and canon violations.",
        constraints=["Return JSON only", "Include blocking_issues, warnings, patch_tasks"],
        inputs={
            "polished": proofread,
            "canon": canon_payload,
            "open_loops": canon_payload.get("open_loops", []),
            "rubric_report": rubric_report,
        },
        output_format='JSON with keys: blocking_issues, warnings, patch_tasks, summary',
        failure_conditions=["invalid JSON", "missing issue lists", "non-actionable patches"],
    )
    continuity = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="continuity",
        agent_name="book-continuity",
        profile_name="book-continuity",
        prompt=continuity_contract,
        output_path=dirs["reviews"] / "continuity_report.json",
        parse_json=True,
        output_schema="continuity",
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 11) Publisher final gate
    publisher_qa_contract = build_contract(
        role="Publisher QA Agent",
        objective="Approve or request revision for final chapter output.",
        constraints=["Return JSON only", "Set decision to APPROVE or REVISE", "Provide required_fixes"],
        inputs={"polished": proofread, "continuity_report": continuity, "chapter_spec": chapter_spec, "rubric_report": rubric_report},
        output_format='JSON with keys: decision, scores, notes, required_fixes, summary',
        failure_conditions=["invalid JSON", "missing decision", "missing required_fixes on REVISE"],
    )

    # --- Publisher QA with retry on revision request ---
    publisher_qa = None
    publisher_attempts = 0
    max_publisher_retries = max(1, args.max_retries + 1)
    last_publisher_prompt = publisher_qa_contract
    current_manuscript = proofread
    forced_completion = False
    while publisher_attempts < max_publisher_retries:
        publisher_attempts += 1
        publisher_qa = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id="publisher_qa",
            agent_name="book-publisher",
            profile_name="book-publisher",
            prompt=last_publisher_prompt,
            output_path=dirs["reviews"] / "publisher_report.json",
            parse_json=True,
            output_schema="publisher_qa",
            max_retries=1,  # Only one attempt per outer loop
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
        )
        decision = str(publisher_qa.get("decision", "")).upper()
        if decision == "APPROVE":
            break
        append_run_event(
            run_journal,
            "publisher_revision_cycle",
            {
                "attempt": publisher_attempts,
                "decision": decision,
                "required_fixes": publisher_qa.get("required_fixes", []),
            },
        )

        autofix_contract = build_contract(
            role="Book Editor Recovery Agent",
            objective="Revise the manuscript so publisher and continuity issues are corrected and the chapter can complete successfully.",
            constraints=[
                "Return markdown only",
                "Apply all required_fixes and patch_tasks",
                "Preserve chapter intent, canon, and factual continuity",
                "Do not explain changes outside the manuscript",
            ],
            inputs={
                "manuscript": current_manuscript,
                "publisher_report": publisher_qa,
                "continuity_report": continuity,
                "chapter_spec": chapter_spec,
                "rubric_report": rubric_report,
            },
            output_format="Markdown revised chapter ready for copy edit and proofreading",
            failure_conditions=["required fixes omitted", "new contradictions introduced", "non-markdown response"],
        )
        revised_manuscript = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"publisher_autofix_{publisher_attempts:02d}",
            agent_name="book-editor",
            profile_name=args.editor_profile,
            prompt=autofix_contract,
            output_path=dirs["drafts_ch"] / f"publisher_autofix_{publisher_attempts:02d}.md",
            parse_json=False,
            gate_fn=lambda text: (len(str(text).split()) >= int(args.writer_words * 0.65), "autofix manuscript too short"),
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
        )
        copy_edited = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"publisher_autofix_copy_{publisher_attempts:02d}",
            agent_name="book-copy-editor",
            profile_name="book-copy-editor",
            prompt=build_contract(
                role="Copy Editor Agent",
                objective="Correct grammar and mechanics after publisher-mandated revisions.",
                constraints=["Return markdown only", "Preserve meaning", "Fix mechanics and consistency"],
                inputs={"polished": revised_manuscript, "style_guide": canon_payload.get("style_guide", "")},
                output_format="Markdown copy-edited chapter",
                failure_conditions=["meaning drift", "new contradictions", "mechanical defects remain"],
            ),
            output_path=dirs["drafts_ch"] / f"publisher_autofix_copy_{publisher_attempts:02d}.md",
            parse_json=False,
            gate_fn=lambda text: (len(str(text).split()) >= int(args.writer_words * 0.65), "autofix copy-edited chapter too short"),
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
        )
        proofread = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"publisher_autofix_proof_{publisher_attempts:02d}",
            agent_name="book-proofreader",
            profile_name="book-proofreader",
            prompt=build_contract(
                role="Proofreader Agent",
                objective="Run final typo and punctuation repair after publisher revisions.",
                constraints=["Return markdown only", "No structural rewrites", "No new facts"],
                inputs={"copy_edited": copy_edited},
                output_format="Markdown proofread chapter",
                failure_conditions=["typos remain", "punctuation defects", "narrative changes introduced"],
            ),
            output_path=dirs["drafts_ch"] / f"publisher_autofix_proof_{publisher_attempts:02d}.md",
            parse_json=False,
            gate_fn=lambda text: (len(str(text).split()) >= int(args.writer_words * 0.65), "autofix proofread chapter too short"),
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
        )
        current_manuscript = proofread
        continuity_contract = build_contract(
            role="Continuity / QA Agent",
            objective="Audit revised chapter for continuity and canon violations.",
            constraints=["Return JSON only", "Include blocking_issues, warnings, patch_tasks"],
            inputs={
                "polished": current_manuscript,
                "canon": canon_payload,
                "open_loops": canon_payload.get("open_loops", []),
                "rubric_report": rubric_report,
            },
            output_format='JSON with keys: blocking_issues, warnings, patch_tasks, summary',
            failure_conditions=["invalid JSON", "missing issue lists", "non-actionable patches"],
        )
        continuity = run_stage(
            orchestrator=orchestrator,
            lock_manager=lock_manager,
            changes_log=changes_log,
            context_store=context_store,
            stage_id=f"continuity_recheck_{publisher_attempts:02d}",
            agent_name="book-continuity",
            profile_name="book-continuity",
            prompt=continuity_contract,
            output_path=dirs["reviews"] / f"continuity_report_{publisher_attempts:02d}.json",
            parse_json=True,
            output_schema="continuity",
            max_retries=args.max_retries,
            diagnostics_path=diagnostics_log,
            verbose=args.verbose,
        )
        last_publisher_prompt = build_contract(
            role="Publisher QA Agent",
            objective="Approve or request revision for final chapter output.",
            constraints=["Return JSON only", "Set decision to APPROVE or REVISE", "Provide required_fixes"],
            inputs={"polished": current_manuscript, "continuity_report": continuity, "chapter_spec": chapter_spec, "rubric_report": rubric_report},
            output_format='JSON with keys: decision, scores, notes, required_fixes, summary',
            failure_conditions=["invalid JSON", "missing decision", "missing required_fixes on REVISE"],
        )

    if publisher_qa and str(publisher_qa.get("decision", "")).upper() != "APPROVE":
        forced_completion = True
        append_run_event(
            run_journal,
            "forced_completion",
            {
                "publisher_attempts": publisher_attempts,
                "final_decision": publisher_qa.get("decision"),
            },
        )

    # Persistent memory updates and final export
    chapter_summary = {
        "chapter_number": args.chapter_number,
        "summary": publisher_qa.get("summary", ""),
        "new_facts": continuity.get("patch_tasks", []),
        "open_loops": canon_payload.get("open_loops", []),
        "next_writer_notes": next_writer_notes,
    }
    rolling_memory = parse_json_block(read_text(dirs["canon"] / "rolling_memory.json", "{}"), fallback={})
    rolling_memory.setdefault("chapter_summaries", [])
    rolling_memory["chapter_summaries"].append(chapter_summary)
    context_store["rolling_memory"] = rolling_memory
    write_json(dirs["canon"] / "rolling_memory.json", rolling_memory)
    write_json(dirs["canon"] / "context_store.json", context_store)

    arc_tracker_existing = read_json(arc_tracker_path, default={})
    arc_tracker = update_arc_tracker(
        arc_tracker_existing,
        chapter_number=args.chapter_number,
        chapter_title=args.chapter_title,
        section_title=args.section_title,
        next_writer_notes=next_writer_notes,
        continuity_state=continuity_state,
        canon_payload=canon_payload,
        rubric_report=rubric_report,
    )
    write_json(arc_tracker_path, arc_tracker)

    progress_index = read_json(progress_index_path, default={})
    chapters = progress_index.get("completed_chapters") if isinstance(progress_index, dict) else []
    chapters = chapters if isinstance(chapters, list) else []
    chapters.append(
        {
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "publisher_decision": publisher_qa.get("decision"),
            "run_dir": str(run_dir),
            "updated_at": datetime.utcnow().isoformat(),
        }
    )
    progress_index["completed_chapters"] = chapters
    progress_index["last_run"] = str(run_dir)
    progress_index["last_updated"] = datetime.utcnow().isoformat()
    write_json(progress_index_path, progress_index)

    write_agent_context_status(
        agent_context_status_path,
        {
            "phase": "chapter_complete",
            "chapter_number": args.chapter_number,
            "chapter_title": args.chapter_title,
            "section_title": args.section_title,
            "expectations": {
                "publisher_decision": publisher_qa.get("decision"),
                "open_loops": _normalize_list(canon_payload.get("open_loops")),
                "next_unresolved_questions": _normalize_list(next_writer_notes.get("unresolved_questions")),
            },
        },
    )

    write_text(dirs["final"] / "manuscript_v1.md", current_manuscript)
    write_text(dirs["final"] / "manuscript_v2.md", current_manuscript)

    validation = validate_required_artifacts(run_dir, expected_section_count=len(chapter_sections))
    summary = {
        "run_dir": str(run_dir),
        "book_root": str(book_root),
        "framework_root": str(framework_root),
        "runs_root": str(runs_root),
        "title": args.title,
        "chapter_number": args.chapter_number,
        "chapter_title": args.chapter_title,
        "section_title": args.section_title,
        "section_count": len(chapter_sections),
        "publisher_decision": publisher_qa.get("decision"),
        "publisher_attempts": publisher_attempts,
        "forced_completion": forced_completion,
        "artifact_validation": validation,
        "merge_stats": merge_stats,
        "context_tracking_strategy": context_tracking_strategy,
        "changes_log": str(changes_log),
        "run_journal": str(run_journal),
        "diagnostics_log": str(diagnostics_log) if diagnostics_log else None,
        "framework_files": {
            "framework_skeleton": str(framework_skeleton_path),
            "arc_tracker": str(arc_tracker_path),
            "progress_index": str(progress_index_path),
            "agent_context_status": str(agent_context_status_path),
        },
        "agent_behavior_tracking": {
            "quality_failures_log": str(orchestrator.quality_failures_log_path),
            "reward_ledger": str(orchestrator.agent_rewards_path),
            "reward_events": str(orchestrator.agent_reward_events_path),
        },
        "verbose": bool(args.verbose),
    }
    write_json(run_dir / "run_summary.json", summary)

    retro_dir = run_dir / "07_retro"
    ensure_dir(retro_dir)
    retro = build_retro_report(run_dir, summary)
    write_json(retro_dir / "retrospective.json", retro)
    write_text(retro_dir / "retrospective.md", build_retro_markdown(retro))
    recent_quality_failures = load_recent_jsonl(orchestrator.quality_failures_log_path, limit=50)
    write_json(retro_dir / "quality_failures_review.json", {
        "source_log": str(orchestrator.quality_failures_log_path),
        "count": len(recent_quality_failures),
        "entries": recent_quality_failures,
    })
    write_text(
        retro_dir / "quality_failures_review.md",
        build_quality_failure_review_markdown(recent_quality_failures),
    )

    parent_log_success = {
        "timestamp": datetime.utcnow().isoformat(),
        "agent": "book-flow-parent",
        "action": "run_success",
        "details": {
            "output_dir": str(run_dir),
            "summary_keys": list(summary.keys()) if isinstance(summary, dict) else [],
        },
    }

    # --- Post-processing: Archive and export completed book run ---
    try:
        import subprocess
        from datetime import datetime
        archive_name = f"{book_slug}_run_{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.tar.gz"
        archive_path = str(runs_root / archive_name)
        # Create tar.gz of the run_dir
        subprocess.run([
            "tar", "-czf", archive_path, "-C", str(runs_root), run_name
        ], check=True)
        # SCP to remote server
        remote_path = f"192.168.86.34:/media/daravenrk/The_Device/WrittenBooks/{archive_name}"
        scp_result = subprocess.run([
            "scp", archive_path, remote_path
        ], capture_output=True, text=True)
        export_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "book-flow-parent",
            "action": "export_book_run",
            "details": {
                "archive": archive_path,
                "remote": remote_path,
                "scp_returncode": scp_result.returncode,
                "scp_stdout": scp_result.stdout,
                "scp_stderr": scp_result.stderr,
            },
        }
        append_jsonl(changes_log, export_log)
    except Exception as export_err:
        wrapped_export_err = BookExportError(
            f"Book run export failed: {export_err}",
            details={
                "run_dir": str(run_dir),
                "runs_root": str(runs_root),
            },
        )
        error_log = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent": "book-flow-parent",
            "action": "export_error",
            "details": {
                "error": str(wrapped_export_err),
                "error_code": wrapped_export_err.code,
                "details": wrapped_export_err.details,
            },
        }
        append_jsonl(changes_log, error_log)

    append_jsonl(changes_log, parent_log_success)
    append_run_event(
        run_journal,
        "run_success",
        {
            "publisher_decision": publisher_qa.get("decision"),
            "publisher_attempts": publisher_attempts,
            "forced_completion": forced_completion,
            "run_summary": str(run_dir / "run_summary.json"),
        },
    )
    print(json.dumps(summary, indent=2))
    return json.dumps(summary, indent=2)


def build_parser():
    p = argparse.ArgumentParser(description="Run hierarchical book writing flow with quality gates")
    p.add_argument("--title", required=True)
    p.add_argument("--genre", default="speculative fiction")
    p.add_argument("--audience", default="adult")
    p.add_argument("--tone", default="cinematic and emotionally grounded")
    p.add_argument("--premise", required=True)

    p.add_argument("--chapter-number", "--chapter_number", dest="chapter_number", type=int, default=1)
    p.add_argument("--chapter-title", "--chapter_title", dest="chapter_title", required=True)
    p.add_argument("--section-title", "--section_title", dest="section_title", required=True)
    p.add_argument("--section-goal", "--section_goal", dest="section_goal", required=True)
    p.add_argument("--writer-words", "--writer_words", dest="writer_words", type=int, default=1400)
    p.add_argument("--target-word-count", "--target_word_count", dest="target_word_count", type=int, default=125000)
    p.add_argument("--page-target", "--page_target", dest="page_target", type=int, default=450)
    p.add_argument("--max-retries", "--max_retries", dest="max_retries", type=int, default=2)
    p.add_argument("--merge-context-words", "--merge_context_words", dest="merge_context_words", type=int, default=3500)
    p.add_argument("-v", "--verbose", action="store_true", help="Enable detailed diagnostics logging")

    p.add_argument("--writer-profile", "--writer_profile", dest="writer_profile", default="book-writer")
    p.add_argument("--editor-profile", "--editor_profile", dest="editor_profile", default="book-editor")
    p.add_argument("--publisher-brief-profile", "--publisher_brief_profile", dest="publisher_brief_profile", default="book-publisher-brief")
    p.add_argument("--publisher-profile", "--publisher_profile", dest="publisher_profile", default="book-publisher")

    p.add_argument("--output-dir", "--output_dir", dest="output_dir", default="/home/daravenrk/dragonlair/book_project")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    run_flow(args)


if __name__ == "__main__":
    main()
