import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_NB, LOCK_UN, flock

from .lock_manager import AgentLockManager
from .orchestrator import OrchestratorAgent


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


def write_json(path: Path, payload):
    write_text(path, json.dumps(payload, indent=2))


def append_jsonl(path: Path, payload):
    ensure_dir(path.parent)
    with file_lock(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")


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


def gate_chapter_spec(spec):
    sections = spec.get("sections") or []
    required = ["chapter_title", "purpose", "ending_hook"]
    if any(not spec.get(key) for key in required):
        return False, "missing required chapter spec fields"
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


def gate_rubric_report(report):
    if not isinstance(report, dict):
        return False, "invalid rubric report"
    scores = report.get("scores") or {}
    if not isinstance(scores, dict):
        return False, "scores missing"

    missing = [k for k in RUBRIC_KEYS if k not in scores]
    if missing:
        return False, f"missing rubric keys: {', '.join(missing)}"

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
    gate_fn=None,
    max_retries=2,
    diagnostics_path=None,
    verbose=False,
):
    stage_instantiated_at = datetime.utcnow().isoformat()

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

    last_raw = ""
    last_error = ""
    parsed = None

    for attempt in range(1, max_retries + 2):
        attempt_started_at = datetime.utcnow().isoformat()
        lock_manager.log_agent_change(
            changes_log,
            agent_name,
            "stage_start",
            {"stage": stage_id, "attempt": attempt},
        )

        if last_error:
            prompt_with_feedback = (
                prompt
                + "\n\nPREVIOUS ATTEMPT FAILED QUALITY GATE:\n"
                + last_error
                + "\nRevise output to satisfy all constraints and output format."
            )
        else:
            prompt_with_feedback = prompt

        raw = orchestrator.handle_request_with_overrides(
            prompt_with_feedback,
            profile_name=profile_name,
            stream_override=False,
        )
        attempt_completed_at = datetime.utcnow().isoformat()
        last_raw = raw
        parsed = parse_json_block(raw, fallback={}) if parse_json else raw

        gate_ok = True
        gate_message = "ok"
        if gate_fn:
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

        if gate_ok:
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
            break

        last_error = gate_message
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
    else:
        raise RuntimeError(f"{stage_id} failed quality gate after retries: {last_error}")

    if output_path is not None:
        with lock_manager.edit_lock(name="publisher_store"):
            if parse_json:
                write_json(output_path, parsed)
            else:
                write_text(output_path, str(parsed))

    context_store[stage_id] = {
        "agent": agent_name,
        "profile": profile_name,
        "output_path": str(output_path) if output_path else None,
        "lock_after": lock_manager.get_lock_status(name="changes_log"),
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
        }
        write_json(Path(handoff_dir) / f"{stage_id}.json", handoff_payload)
        # Flat-file handoff channel for downstream agents.
        handoff_md = [
            f"# Stage Handoff: {stage_id}",
            "",
            f"- Agent: {agent_name}",
            f"- Profile: {profile_name}",
            f"- Output Path: {str(output_path) if output_path else 'inline'}",
            "",
            "## LLM Response",
            "",
        ]
        if parse_json:
            handoff_md.extend([
                "```json",
                json.dumps(parsed, indent=2),
                "```",
            ])
        else:
            handoff_md.append(str(parsed))
        write_text(Path(handoff_dir) / f"{stage_id}.md", "\n".join(handoff_md) + "\n")

    lock_manager.log_agent_change(
        changes_log,
        agent_name,
        "stage_complete",
        {
            "stage": stage_id,
            "output_path": str(output_path) if output_path else None,
            "lock_after": context_store[stage_id]["lock_after"],
            "report_to_publisher": True,
        },
    )

    return parsed


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
    run_dir = Path(args.output_dir).expanduser() / (slugify(args.title) if hasattr(args, 'title') else "book-error")
    changes_log = run_dir / "changes.log"
    ensure_dir(changes_log.parent)
    append_jsonl(changes_log, parent_log)

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    run_name = f"{stamp}-{slugify(args.title)}-ch{args.chapter_number:02d}-{slugify(args.section_title)}"
    run_dir = Path(args.output_dir).expanduser() / run_name

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
    diagnostics_log = (dirs["diagnostics"] / "agent_diagnostics.jsonl") if args.verbose else None
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
    brief = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="publisher_brief",
        agent_name="book-publisher",
        profile_name="book-publisher",
        prompt=publisher_contract,
        output_path=dirs["brief"] / "book_brief.json",
        parse_json=True,
        gate_fn=lambda obj: (bool(obj.get("constraints")) and bool(obj.get("acceptance_criteria")), "missing constraints or acceptance criteria"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )
    context_store["permanent_memory"] = {"book_brief": brief}
    write_json(dirs["canon"] / "context_store.json", context_store)

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
        gate_fn=lambda text: ("facts" in text.lower(), "research output missing facts section"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )

    # 3) Architect
    architect_contract = build_contract(
        role="Concept Architect Agent",
        objective="Create master outline and book structure.",
        constraints=["Return JSON only", "Include master_outline_markdown and book_structure"],
        inputs={"book_brief": brief, "research_dossier": research_md},
        output_format='JSON with keys: master_outline_markdown, book_structure, pacing_notes',
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
        gate_fn=lambda obj: (bool(obj.get("master_outline_markdown")), "master_outline_markdown missing"),
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
        gate_fn=gate_chapter_spec,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
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
        gate_fn=lambda obj: (bool(obj.get("canon")), "canon field missing"),
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
    )
    write_json(dirs["canon"] / "timeline.json", canon_payload.get("timeline", {}))
    write_json(dirs["canon"] / "character_bible.json", canon_payload.get("character_bible", {}))
    write_json(dirs["canon"] / "open_loops.json", canon_payload.get("open_loops", []))
    write_text(dirs["canon"] / "style_guide.md", str(canon_payload.get("style_guide", "")))
    context_tracking_strategy = derive_context_tracking_strategy(context_store.get("book", {}), canon_payload, chapter_spec)

    # 6) Writer + section consistency review per section
    chapter_sections = chapter_spec.get("sections") or []
    if len(chapter_sections) < 2:
        raise RuntimeError("Chapter spec must provide at least 2 sections for sequencing/assembly consistency checks")

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
        gate_fn=lambda obj: (
            isinstance(obj.get("concept_validation"), (int, float)) and isinstance(obj.get("structure_validation"), (int, float)),
            "story architect scores missing",
        ),
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
        gate_fn=lambda obj: (not obj.get("blocking_issues"), "blocking continuity issues found"),
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
    publisher_qa = run_stage(
        orchestrator=orchestrator,
        lock_manager=lock_manager,
        changes_log=changes_log,
        context_store=context_store,
        stage_id="publisher_qa",
        agent_name="book-publisher",
        profile_name="book-publisher",
        prompt=publisher_qa_contract,
        output_path=dirs["reviews"] / "publisher_report.json",
        parse_json=True,
        gate_fn=gate_publisher,
        max_retries=args.max_retries,
        diagnostics_path=diagnostics_log,
        verbose=args.verbose,
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

    write_text(dirs["final"] / "manuscript_v1.md", proofread)
    write_text(dirs["final"] / "manuscript_v2.md", proofread)

    validation = validate_required_artifacts(run_dir, expected_section_count=len(chapter_sections))
    summary = {
        "run_dir": str(run_dir),
        "title": args.title,
        "chapter_number": args.chapter_number,
        "chapter_title": args.chapter_title,
        "section_title": args.section_title,
        "section_count": len(chapter_sections),
        "publisher_decision": publisher_qa.get("decision"),
        "artifact_validation": validation,
        "merge_stats": merge_stats,
        "context_tracking_strategy": context_tracking_strategy,
        "changes_log": str(changes_log),
        "diagnostics_log": str(diagnostics_log) if diagnostics_log else None,
        "verbose": bool(args.verbose),
    }
    write_json(run_dir / "run_summary.json", summary)

    retro_dir = run_dir / "07_retro"
    ensure_dir(retro_dir)
    retro = build_retro_report(run_dir, summary)
    write_json(retro_dir / "retrospective.json", retro)
    write_text(retro_dir / "retrospective.md", build_retro_markdown(retro))

    parent_log_success = {
        "timestamp": datetime.utcnow().isoformat(),
        "agent": "book-flow-parent",
        "action": "run_success",
        "details": {
            "output_dir": str(run_dir),
            "summary_keys": list(summary.keys()) if isinstance(summary, dict) else [],
        },
    }
    append_jsonl(changes_log, parent_log_success)
    print(json.dumps(summary, indent=2))
    return json.dumps(summary, indent=2)


def build_parser():
    p = argparse.ArgumentParser(description="Run hierarchical book writing flow with quality gates")
    p.add_argument("--title", required=True)
    p.add_argument("--genre", default="speculative fiction")
    p.add_argument("--audience", default="adult")
    p.add_argument("--tone", default="cinematic and emotionally grounded")
    p.add_argument("--premise", required=True)

    p.add_argument("--chapter-number", type=int, default=1)
    p.add_argument("--chapter-title", required=True)
    p.add_argument("--section-title", required=True)
    p.add_argument("--section-goal", required=True)
    p.add_argument("--writer-words", type=int, default=1400)
    p.add_argument("--target-word-count", type=int, default=125000)
    p.add_argument("--page-target", type=int, default=450)
    p.add_argument("--max-retries", type=int, default=2)
    p.add_argument("--merge-context-words", type=int, default=3500)
    p.add_argument("-v", "--verbose", action="store_true", help="Enable detailed diagnostics logging")

    p.add_argument("--writer-profile", default="book-writer")
    p.add_argument("--editor-profile", default="book-editor")
    p.add_argument("--publisher-profile", default="book-publisher")

    p.add_argument("--output-dir", default="/home/daravenrk/dragonlair/book_project")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    run_flow(args)


if __name__ == "__main__":
    main()
