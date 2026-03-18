"""Living Skeleton — Two-Tier Documentation System (Todo 90).

This module manages the two documentation layers that every chapter run reads
from and writes to:

  TIER 1 — SOURCE OF TRUTH (immutable, per-chapter)
  --------------------------------------------------
  framework/canonical/ch<N>_record.json
  Written exactly once after a chapter is accepted by the publisher.  Never
  overwritten.  Contains locked law_items, character states, timeline events,
  open/closed loops, and continuity constraints that ALL future writers and
  reviewers must respect.

  TIER 2 — LIVING SKELETON (predictive, mutable)
  -----------------------------------------------
  framework/living_skeleton.json
  Rewritten (not appended) after each chapter acceptance.  Accepted chapter
  frames are locked and reference their canonical record.  Future chapter
  frames carry hard constraints propagated from every canonical record so far.
  This is the guidance document writers use to understand where the story is
  going NEXT.

  MASTER INDEX
  ------------
  framework/doc_index.json
  Single lookup table for all documentation artifacts in the book.  Any tool
  or agent that needs to locate «the accepted manuscript for chapter 3» or
  «the law items from chapter 1» reads this file first.

Workflow per accepted chapter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  1. run_living_skeleton_update() is called from book_flow.py post-acceptance.
  2. extract_chapter_canon() runs a quick AMD/qwen3.5:9b extraction pass on
     the accepted manuscript.  Produces: law_items, character_states,
     timeline_events, open_loops_opened/closed, continuity_constraints,
     delta_from_skeleton, accepted_content_summary.
  3. write_canonical_record() produces framework/canonical/ch<N>_record.json.
     This file is write-once.
  4. update_living_skeleton_json() reads the existing living_skeleton.json (or
     seeds from story_skeleton.json / framework_skeleton.json if it doesn't
     exist yet), locks the accepted frame, propagates constraints forward into
     all future frames, and writes the updated file.
  5. update_doc_index() keeps doc_index.json current.

Writer injection (called from book_flow.py before writing starts)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  load_law_context(framework_root, for_chapter_number) returns a pre-formatted
  text block listing all law_items and continuity_constraints from every
  previously accepted chapter.  This block is injected into the writer's
  contract so the writer both knows the hard law AND can see the living
  skeleton's current prediction for their chapter.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CANONICAL_DIR = "canonical"
RECORD_PREFIX = "ch"
RECORD_SUFFIX = "_record.json"
LIVING_SKELETON_FILE = "living_skeleton.json"
DOC_INDEX_FILE = "doc_index.json"
SKELETON_UPDATER_PROFILE = "book-skeleton-updater"
MAX_EXTRACTION_RETRIES = 2

# ---------------------------------------------------------------------------
# IO helpers (minimal, no book_flow dependency)
# ---------------------------------------------------------------------------

def _read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {} if default is None else default


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _word_count(text: str) -> int:
    return len((text or "").split())


def _canonical_record_path(framework_root: Path, chapter_number: int) -> Path:
    return framework_root / CANONICAL_DIR / f"{RECORD_PREFIX}{chapter_number:02d}{RECORD_SUFFIX}"


def _parse_json_block(text: str) -> dict | None:
    """Extract the first JSON object from an LLM response."""
    text = (text or "").strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        result = json.loads(text[start:i + 1])
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        break
    return None


def _normalize_list(value) -> list:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# ---------------------------------------------------------------------------
# Extraction contract builder
# ---------------------------------------------------------------------------

def build_extraction_contract(
    chapter_number: int,
    chapter_title: str,
    accepted_manuscript: str,
    skeleton_frame: dict | None,
    canon_payload: dict,
    next_writer_notes: dict,
) -> str:
    skeleton_block = ""
    if skeleton_frame:
        planned_purpose = skeleton_frame.get("purpose") or ""
        planned_must_not = skeleton_frame.get("must_not_resolve") or []
        planned_constraints = skeleton_frame.get("hard_constraints") or []
        skeleton_block = (
            f"\n\nORIGINAL PLANNED SKELETON FRAME FOR THIS CHAPTER:\n"
            f"  Planned purpose: {planned_purpose}\n"
            f"  Must not resolve: {', '.join(str(x) for x in planned_must_not) or 'none'}\n"
            f"  Hard constraints: {'; '.join(str(x) for x in planned_constraints) or 'none'}"
        )

    open_loops_known = _normalize_list(
        (canon_payload.get("open_loops") if isinstance(canon_payload, dict) else None) or []
    )
    character_names = list(
        (canon_payload.get("character_bible") or {}).keys()
        if isinstance(canon_payload.get("character_bible"), dict)
        else []
    )[:8]
    must_carry = _normalize_list(
        (next_writer_notes.get("must_carry_forward") if isinstance(next_writer_notes, dict) else None) or []
    )

    word_ct = _word_count(accepted_manuscript)

    return f"""You are a Story Archivist. Extract the permanent canonical record from this accepted chapter.

ACCEPTED CHAPTER:
  Number: {chapter_number}
  Title: {chapter_title}
  Word count: {word_ct:,}
{skeleton_block}

ACCEPTED MANUSCRIPT (full text follows):
---BEGIN MANUSCRIPT---
{accepted_manuscript[:12000]}{'...[truncated]' if len(accepted_manuscript) > 12000 else ''}
---END MANUSCRIPT---

KNOWN OPEN LOOPS GOING INTO THIS CHAPTER:
{chr(10).join(f'  - {l}' for l in open_loops_known) or '  (none tracked yet)'}

CHARACTERS TO TRACK STATE FOR:
{chr(10).join(f'  - {n}' for n in character_names) or '  (derive from manuscript)'}

RUBRIC CARRY-FORWARD NOTES:
{chr(10).join(f'  - {n}' for n in must_carry) or '  (none)'}

Return ONLY a valid JSON object with exactly these keys:

{{
  "accepted_content_summary": "<2-3 sentence factual summary of what actually happened in this chapter>",
  "law_items": [
    "<specific, concrete, falsifiable fact established in this chapter that all future writing must respect>"
  ],
  "character_states": {{
    "<Character Name>": "<their state at end of this chapter — location, knowledge, physical condition, relationships>"
  }},
  "timeline_events": [
    "<event that occurred, stated concretely with who/what/when if determinable>"
  ],
  "open_loops_opened": [
    {{"loop": "<thread name>", "description": "<what the unresolved thread is and why it matters>"}}
  ],
  "open_loops_closed": [
    "<loop name that was definitively resolved in this chapter>"
  ],
  "continuity_constraints": [
    "<specific thing the next writer MUST NOT do or MUST maintain, derived from this chapter's events>"
  ],
  "delta_from_skeleton": "<what changed from the original plan, or null if no skeleton existed>"
}}

RULES:
- law_items must be specific and falsifiable (not vague). Bad: "the world is dangerous." Good: "Kael's right arm is broken after the fire in the east wing."
- character_states must cover every named character who appears in this chapter.
- continuity_constraints must be actionable instructions to the next writer.
- open_loops_closed must only list loops that are definitively and permanently resolved, not just paused.
- Return ONLY the JSON object. No markdown. No explanation.
"""


# ---------------------------------------------------------------------------
# AI extraction pass
# ---------------------------------------------------------------------------

def extract_chapter_canon(
    orchestrator,
    chapter_number: int,
    chapter_title: str,
    accepted_manuscript: str,
    skeleton_frame: dict | None,
    canon_payload: dict,
    next_writer_notes: dict,
    verbose: bool = False,
) -> dict | None:
    """Run AI extraction pass to produce structured canonical facts.

    Returns the extraction dict on success, or None if all retries fail.
    The caller must handle the None case by building a minimal record from
    arc_tracker data instead.
    """
    try:
        from .exceptions import AgentStackError
    except ImportError:
        AgentStackError = Exception

    prompt = build_extraction_contract(
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        accepted_manuscript=accepted_manuscript,
        skeleton_frame=skeleton_frame,
        canon_payload=canon_payload,
        next_writer_notes=next_writer_notes,
    )

    for attempt in range(1, MAX_EXTRACTION_RETRIES + 2):
        try:
            raw = orchestrator.run(
                prompt=prompt,
                agent_name=SKELETON_UPDATER_PROFILE,
                profile_name=SKELETON_UPDATER_PROFILE,
            )
        except AgentStackError as exc:
            if verbose:
                print(f"[living_skeleton] extraction attempt {attempt} failed: {exc}", file=sys.stderr)
            continue

        parsed = _parse_json_block(raw if isinstance(raw, str) else json.dumps(raw))
        if parsed is None:
            if verbose:
                print(f"[living_skeleton] extraction attempt {attempt}: no JSON in response", file=sys.stderr)
            continue

        required = ["accepted_content_summary", "law_items", "character_states",
                    "timeline_events", "open_loops_opened", "open_loops_closed",
                    "continuity_constraints"]
        missing = [k for k in required if k not in parsed]
        if missing:
            if verbose:
                print(f"[living_skeleton] extraction attempt {attempt}: missing keys {missing}", file=sys.stderr)
            prompt = (
                f"Your previous attempt was missing these keys: {missing}. "
                "Fix and return the complete JSON.\n\n" + prompt
            )
            continue

        return parsed  # success

    return None


# ---------------------------------------------------------------------------
# Canonical record writer (write-once)
# ---------------------------------------------------------------------------

def write_canonical_record(
    framework_root: Path,
    chapter_number: int,
    chapter_title: str,
    section_title: str,
    extraction: dict | None,
    arc_tracker: dict,
    rubric_report: dict,
    run_dir: Path,
    manuscript_path: Path,
    word_count: int,
) -> Path:
    """Write the immutable ch<N>_record.json.

    If the record already exists it is NOT overwritten — canonical records
    are write-once.  Returns the path regardless of whether it was written.
    """
    record_path = _canonical_record_path(framework_root, chapter_number)
    if record_path.exists():
        # Already accepted — do not overwrite
        return record_path

    # Prefer AI extraction; fall back to arc_tracker data when extraction failed
    if extraction:
        law_items = _normalize_list(extraction.get("law_items"))
        character_states = extraction.get("character_states") or {}
        timeline_events = _normalize_list(extraction.get("timeline_events"))
        open_loops_opened = extraction.get("open_loops_opened") or []
        open_loops_closed = _normalize_list(extraction.get("open_loops_closed"))
        continuity_constraints = _normalize_list(extraction.get("continuity_constraints"))
        accepted_content_summary = str(extraction.get("accepted_content_summary") or "")
        delta_from_skeleton = extraction.get("delta_from_skeleton")
    else:
        # Minimal fallback from arc_tracker
        last_progress = {}
        if isinstance(arc_tracker.get("chapter_progress"), list):
            for p in arc_tracker["chapter_progress"]:
                if isinstance(p, dict) and p.get("chapter_number") == chapter_number:
                    last_progress = p
        law_items = _normalize_list(last_progress.get("timeline_events"))
        character_states = {}
        for entry in _normalize_list(arc_tracker.get("character_arcs")):
            character_states[str(entry)] = "see arc_tracker"
        timeline_events = _normalize_list(last_progress.get("timeline_events"))
        open_loops_opened = []
        open_loops_closed = []
        continuity_constraints = _normalize_list(arc_tracker.get("open_loops"))[:5]
        accepted_content_summary = f"Chapter {chapter_number}: {chapter_title} (extraction failed; fallback record)"
        delta_from_skeleton = None

    rubric_scores = (rubric_report or {}).get("scores") if isinstance(rubric_report, dict) else {}

    record = {
        "chapter_number": chapter_number,
        "chapter_title": chapter_title,
        "section_title": section_title,
        "accepted_at": datetime.utcnow().isoformat(),
        "accepted_by": "publisher-qa",
        "run_dir": str(run_dir),
        "manuscript_path": str(manuscript_path),
        "word_count": word_count,
        "accepted_content_summary": accepted_content_summary,
        "law_items": law_items,
        "character_states": character_states,
        "timeline_events": timeline_events,
        "open_loops_opened": open_loops_opened,
        "open_loops_closed": open_loops_closed,
        "continuity_constraints": continuity_constraints,
        "delta_from_skeleton": delta_from_skeleton,
        "rubric_summary": {k: v for k, v in (rubric_scores or {}).items()},
        "_extraction_succeeded": extraction is not None,
    }

    _write_json(record_path, record)
    return record_path


# ---------------------------------------------------------------------------
# Living skeleton structural update
# ---------------------------------------------------------------------------

def _seed_living_skeleton_from_story_skeleton(framework_root: Path) -> dict:
    """Seed living_skeleton.json from story_skeleton.json, or from
    framework_skeleton.json if the story skeleton doesn't exist."""
    story_skeleton_path = framework_root / "story_skeleton.json"
    if story_skeleton_path.exists():
        base = _read_json(story_skeleton_path) or {}
        # Convert to living skeleton format (spec_version 2)
        living = {
            "spec_version": 2,
            "seeded_from": "story_skeleton",
            "story_spine": base.get("story_spine", ""),
            "major_beats": base.get("major_beats") or [],
            "open_loops": [],
            "character_arcs": base.get("character_arcs") or [],
            "chapter_frames": [],
            "series_threads": base.get("series_threads") or [],
        }
        # Annotate major beats with planned status
        for beat in living["major_beats"]:
            if isinstance(beat, dict):
                beat.setdefault("status", "planned")

        # Convert open_loops from story_skeleton format to living format
        for loop in (base.get("open_loops") or []):
            if not isinstance(loop, dict):
                continue
            living["open_loops"].append({
                "loop": loop.get("loop", ""),
                "opens_chapter": loop.get("opens_chapter"),
                "resolves_chapter": loop.get("resolves_chapter"),
                "resolve_type": loop.get("resolve_type", "answered"),
                "description": loop.get("description", ""),
                "status": "planned",
                "resolution_notes": "",
            })

        # Convert chapter_frames with planned status
        for frame in (base.get("chapter_frames") or []):
            if not isinstance(frame, dict):
                continue
            new_frame = dict(frame)
            new_frame["status"] = "planned"
            new_frame.setdefault("law_record_path", None)
            new_frame.setdefault("accepted_content_summary", None)
            new_frame.setdefault("law_items", [])
            new_frame.setdefault("extra_hard_constraints", [])
            living["chapter_frames"].append(new_frame)

        return living

    # Fall back to framework_skeleton if story_skeleton doesn't exist
    fw = _read_json(framework_root / "framework_skeleton.json") or {}
    book_id = fw.get("book_identity") or {}
    book_struct = (fw.get("design_framework") or {}).get("book_structure") or {}
    acts = book_struct.get("acts") or [] if isinstance(book_struct, dict) else []
    return {
        "spec_version": 2,
        "seeded_from": "framework_skeleton",
        "story_spine": book_id.get("title_working", ""),
        "major_beats": [
            {
                "beat": act.get("act_name", ""),
                "chapter": act.get("chapter_range", ""),
                "type": "act",
                "status": "planned",
                "description": act.get("goal", ""),
            }
            for act in acts if isinstance(act, dict)
        ],
        "open_loops": [],
        "character_arcs": [],
        "chapter_frames": [],
        "series_threads": [],
    }


def update_living_skeleton_json(
    framework_root: Path,
    chapter_number: int,
    canonical_record: dict,
) -> dict:
    """Structurally update living_skeleton.json after a chapter is accepted.

    Rules
    -----
    - The accepted chapter's frame is locked: status → 'accepted', law fields filled.
    - Continuity constraints from the canonical record are appended to
      extra_hard_constraints of ALL future chapter frames.
    - open_loops from the record's open_loops_opened → status 'open'.
    - open_loops from the record's open_loops_closed → status 'resolved'.
    - character_arcs updated with current states from canonical record.
    - Major beats in the accepted chapter's range → status 'locked'.
    - Returns the updated living skeleton dict.
    """
    living_path = framework_root / LIVING_SKELETON_FILE
    # Load existing or seed fresh
    if living_path.exists():
        living = _read_json(living_path) or {}
        if not living.get("spec_version"):
            living = _seed_living_skeleton_from_story_skeleton(framework_root)
    else:
        living = _seed_living_skeleton_from_story_skeleton(framework_root)

    living.setdefault("spec_version", 2)
    living.setdefault("chapter_frames", [])
    living.setdefault("open_loops", [])
    living.setdefault("character_arcs", [])
    living.setdefault("major_beats", [])
    living.setdefault("series_threads", [])

    # ---- Lock the accepted chapter frame ----
    law_items = canonical_record.get("law_items") or []
    continuity_constraints = canonical_record.get("continuity_constraints") or []
    accepted_summary = canonical_record.get("accepted_content_summary") or ""
    ch_record_rel = f"{CANONICAL_DIR}/{RECORD_PREFIX}{chapter_number:02d}{RECORD_SUFFIX}"

    frame_found = False
    for frame in living["chapter_frames"]:
        if not isinstance(frame, dict):
            continue
        frame_ch = frame.get("chapter")
        try:
            frame_ch_int = int(frame_ch)
        except (TypeError, ValueError):
            continue
        if frame_ch_int == chapter_number:
            frame["status"] = "accepted"
            frame["law_record_path"] = ch_record_rel
            frame["accepted_content_summary"] = accepted_summary
            frame["law_items"] = law_items
            frame_found = True
        elif frame_ch_int > chapter_number:
            # Propagate constraints into future frames
            frame.setdefault("extra_hard_constraints", [])
            for constraint in continuity_constraints:
                tag = f"[from ch{chapter_number:02d}] {constraint}"
                if tag not in frame["extra_hard_constraints"]:
                    frame["extra_hard_constraints"].append(tag)

    # If chapter wasn't in the frame list (no story_skeleton), create an entry
    if not frame_found:
        living["chapter_frames"].append({
            "chapter": chapter_number,
            "status": "accepted",
            "title": canonical_record.get("chapter_title") or f"Chapter {chapter_number}",
            "purpose": accepted_summary,
            "law_record_path": ch_record_rel,
            "accepted_content_summary": accepted_summary,
            "law_items": law_items,
            "extra_hard_constraints": [],
            "opens_loops": [
                (l.get("loop") if isinstance(l, dict) else str(l))
                for l in (canonical_record.get("open_loops_opened") or [])
            ],
            "closes_loops": canonical_record.get("open_loops_closed") or [],
        })

    # ---- Update open_loops status ----
    opened_names = set()
    for loop_entry in (canonical_record.get("open_loops_opened") or []):
        if isinstance(loop_entry, dict):
            name = loop_entry.get("loop") or ""
        else:
            name = str(loop_entry)
        if name:
            opened_names.add(name.lower())

    closed_names = {str(n).lower() for n in (canonical_record.get("open_loops_closed") or [])}

    existing_loop_keys = {
        (l.get("loop") or "").lower(): i
        for i, l in enumerate(living["open_loops"])
        if isinstance(l, dict)
    }

    for name in opened_names:
        if name not in existing_loop_keys:
            # Find the opened loop descriptor from the canonical record
            desc = ""
            for entry in (canonical_record.get("open_loops_opened") or []):
                if isinstance(entry, dict) and (entry.get("loop") or "").lower() == name:
                    desc = entry.get("description") or ""
            living["open_loops"].append({
                "loop": name,
                "opens_chapter": chapter_number,
                "resolves_chapter": None,
                "description": desc,
                "status": "open",
                "resolution_notes": "",
            })
        else:
            idx = existing_loop_keys[name]
            living["open_loops"][idx]["status"] = "open"

    for name in closed_names:
        if name in existing_loop_keys:
            idx = existing_loop_keys[name]
            living["open_loops"][idx]["status"] = "resolved"
            living["open_loops"][idx]["resolution_notes"] = f"Resolved in chapter {chapter_number}"

    # ---- Update major_beats status ----
    for beat in living["major_beats"]:
        if not isinstance(beat, dict):
            continue
        beat_ch = beat.get("chapter")
        try:
            beat_ch_int = int(str(beat_ch).split("-")[0])
        except (TypeError, ValueError, AttributeError):
            continue
        if beat_ch_int <= chapter_number and beat.get("status") != "locked":
            beat["status"] = "locked"

    # ---- Update character_arcs with current states ----
    char_states = canonical_record.get("character_states") or {}
    for char_name, current_state in char_states.items():
        arc_found = False
        for arc in living["character_arcs"]:
            if not isinstance(arc, dict):
                continue
            if str(arc.get("name") or "").lower() == char_name.lower():
                arc.setdefault("arc_milestones", [])
                arc["arc_milestones"].append({
                    "chapter": chapter_number,
                    "state": current_state,
                    "source": "canonical_record",
                })
                arc["current_state"] = current_state
                arc["current_state_after_chapter"] = chapter_number
                arc_found = True
                break
        if not arc_found:
            living["character_arcs"].append({
                "name": char_name,
                "role": "derived",
                "current_state": current_state,
                "current_state_after_chapter": chapter_number,
                "arc_milestones": [{"chapter": chapter_number, "state": current_state, "source": "canonical_record"}],
            })

    living["last_updated_after_chapter"] = chapter_number
    living["last_updated_at"] = datetime.utcnow().isoformat()

    _write_json(living_path, living)
    return living


# ---------------------------------------------------------------------------
# Doc index updater
# ---------------------------------------------------------------------------

def update_doc_index(
    framework_root: Path,
    book_root: Path,
    chapter_number: int,
    canonical_record: dict,
    run_dir: Path,
    manuscript_path: Path,
) -> dict:
    """Update framework/doc_index.json — the master artifact registry."""
    index_path = framework_root / DOC_INDEX_FILE
    index = _read_json(index_path) or {}

    # Ensure structure exists
    index.setdefault("tiers", {})
    index["tiers"].setdefault("source_of_truth", {
        "description": (
            "Immutable per-chapter canonical records. "
            "These are the historical facts of the story. "
            "Writers MUST treat every law_item and continuity_constraint "
            "as an inviolable rule when writing future chapters."
        ),
        "canonical_records_dir": f"{CANONICAL_DIR}/",
        "accepted_chapters": [],
    })
    index["tiers"].setdefault("living_guidance", {
        "description": (
            "Predictive living skeleton — updated after each acceptance. "
            "Future chapter frames carry hard constraints propagated from all "
            "canonical records. Writers follow this for guidance on where the "
            "story is going NEXT, while the canonical records remain the "
            "unchangeable record of where the story has BEEN."
        ),
        "living_skeleton": LIVING_SKELETON_FILE,
        "original_story_skeleton": "story_skeleton.json",
    })
    index.setdefault("framework_artifacts", {
        "arc_tracker": "arc_tracker.json",
        "progress_index": "progress_index.json",
        "framework_skeleton": "framework_skeleton.json",
        "agent_context_status": "agent_context_status.jsonl",
        "skeleton_run_log": "skeleton_run_log.jsonl",
    })

    # Remove any existing entry for this chapter (idempotent)
    existing_entries = index["tiers"]["source_of_truth"]["accepted_chapters"]
    existing_entries = [e for e in existing_entries if e.get("chapter_number") != chapter_number]

    # Make paths relative to book_root for portability
    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(book_root))
        except ValueError:
            return str(p)

    existing_entries.append({
        "chapter_number": chapter_number,
        "chapter_title": canonical_record.get("chapter_title", ""),
        "section_title": canonical_record.get("section_title", ""),
        "accepted_at": canonical_record.get("accepted_at", datetime.utcnow().isoformat()),
        "canonical_record": _rel(framework_root / CANONICAL_DIR / f"{RECORD_PREFIX}{chapter_number:02d}{RECORD_SUFFIX}"),
        "manuscript_path": _rel(manuscript_path),
        "run_dir": _rel(run_dir),
        "word_count": canonical_record.get("word_count", 0),
        "law_item_count": len(canonical_record.get("law_items") or []),
        "constraint_count": len(canonical_record.get("continuity_constraints") or []),
    })

    existing_entries.sort(key=lambda e: e.get("chapter_number", 0))
    index["tiers"]["source_of_truth"]["accepted_chapters"] = existing_entries
    index["last_updated"] = datetime.utcnow().isoformat()
    index["last_chapter_accepted"] = chapter_number

    _write_json(index_path, index)
    return index


# ---------------------------------------------------------------------------
# Law context loader (for writer injection)
# ---------------------------------------------------------------------------

def load_law_context(framework_root: Path, for_chapter_number: int) -> str:
    """Load all canonical records for chapters 1..(for_chapter_number - 1)
    and format them as a structured text block for writer contract injection.

    Returns an empty string if no canonical records exist yet (chapter 1).
    """
    canonical_dir = framework_root / CANONICAL_DIR
    if not canonical_dir.exists():
        return ""

    past_chapters = []
    for ch_num in range(1, for_chapter_number):
        record_path = _canonical_record_path(framework_root, ch_num)
        if record_path.exists():
            record = _read_json(record_path)
            if record:
                past_chapters.append(record)

    if not past_chapters:
        return ""

    lines = [
        "=" * 72,
        "CANONICAL LAW — Past Accepted Chapters (You MUST Respect These)",
        "=" * 72,
        (
            "The following are locked facts established by previously accepted "
            "chapters. Every law_item is inviolable. Every continuity_constraint "
            "is a hard rule you must not violate. Contradicting these is an "
            "automatic quality gate failure."
        ),
        "",
    ]

    for rec in past_chapters:
        ch = rec.get("chapter_number", "?")
        title = rec.get("chapter_title", "")
        summary = rec.get("accepted_content_summary", "")
        law_items = _normalize_list(rec.get("law_items"))
        char_states = rec.get("character_states") or {}
        timeline = _normalize_list(rec.get("timeline_events"))
        constraints = _normalize_list(rec.get("continuity_constraints"))
        loops_opened = rec.get("open_loops_opened") or []
        loops_closed = _normalize_list(rec.get("open_loops_closed"))

        lines.append(f"── Chapter {ch}: \"{title}\" [ACCEPTED] ──")
        if summary:
            lines.append(f"  Summary: {summary}")
        if law_items:
            lines.append("  LAW ITEMS (inviolable facts):")
            for item in law_items:
                lines.append(f"    • {item}")
        if constraints:
            lines.append("  CONTINUITY CONSTRAINTS (hard rules for future writers):")
            for c in constraints:
                lines.append(f"    ✖ {c}")
        if char_states:
            lines.append("  CHARACTER STATES after this chapter:")
            for name, state in char_states.items():
                lines.append(f"    [{name}] {state}")
        if timeline:
            lines.append("  TIMELINE EVENTS:")
            for ev in timeline:
                lines.append(f"    → {ev}")
        if loops_opened:
            lines.append("  OPEN LOOPS INTRODUCED (must persist and eventually resolve):")
            for lp in loops_opened:
                if isinstance(lp, dict):
                    lines.append(f"    ◯ {lp.get('loop', '')}: {lp.get('description', '')}")
                else:
                    lines.append(f"    ◯ {lp}")
        if loops_closed:
            lines.append("  LOOPS RESOLVED (do not re-open these):")
            for lp in loops_closed:
                lines.append(f"    ✔ {lp}")
        lines.append("")

    lines.append("=" * 72)
    return "\n".join(lines)


def get_future_frame(framework_root: Path, chapter_number: int) -> dict | None:
    """Return the living skeleton's current frame prediction for the given
    chapter. Returns None if the living skeleton doesn't exist or has no frame
    for this chapter. Used to inject skeleton guidance into the writer brief."""
    living_path = framework_root / LIVING_SKELETON_FILE
    if not living_path.exists():
        # Try original story_skeleton as fallback
        story_path = framework_root / "story_skeleton.json"
        if not story_path.exists():
            return None
        living = _read_json(story_path)
    else:
        living = _read_json(living_path)

    frames = living.get("chapter_frames") or []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        try:
            if int(frame.get("chapter", -1)) == chapter_number:
                return frame
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Main entry point (called from book_flow.py post-acceptance)
# ---------------------------------------------------------------------------

def run_living_skeleton_update(
    framework_root: Path,
    book_root: Path,
    chapter_number: int,
    chapter_title: str,
    section_title: str,
    accepted_manuscript: str,
    arc_tracker: dict,
    canon_payload: dict,
    next_writer_notes: dict,
    rubric_report: dict,
    run_dir: Path,
    orchestrator,
    verbose: bool = False,
) -> dict:
    """Orchestrate the full post-acceptance documentation update.

    Safe to call even if sub-steps fail — errors are caught, logged, and
    a partial result dict is returned so the chapter run is never blocked.

    Returns a summary dict with keys:
        canonical_record_path, living_skeleton_path, doc_index_path,
        extraction_succeeded, error (if any)
    """
    result: dict = {
        "chapter_number": chapter_number,
        "canonical_record_path": None,
        "living_skeleton_path": str(framework_root / LIVING_SKELETON_FILE),
        "doc_index_path": str(framework_root / DOC_INDEX_FILE),
        "extraction_succeeded": False,
        "error": None,
    }
    update_log = framework_root / "living_skeleton_update_log.jsonl"

    try:
        _append_jsonl(update_log, {
            "event": "update_start",
            "chapter_number": chapter_number,
            "chapter_title": chapter_title,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Find the original planned skeleton frame (if story_skeleton exists)
        skeleton_frame = get_future_frame(framework_root, chapter_number)

        # 1. AI extraction pass
        extraction = None
        try:
            extraction = extract_chapter_canon(
                orchestrator=orchestrator,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                accepted_manuscript=accepted_manuscript,
                skeleton_frame=skeleton_frame,
                canon_payload=canon_payload,
                next_writer_notes=next_writer_notes,
                verbose=verbose,
            )
        except Exception as exc:
            if verbose:
                print(f"[living_skeleton] extraction exception: {exc}", file=sys.stderr)
            _append_jsonl(update_log, {
                "event": "extraction_failed",
                "chapter_number": chapter_number,
                "error": str(exc),
                "timestamp": datetime.utcnow().isoformat(),
            })

        result["extraction_succeeded"] = extraction is not None

        # 2. Write canonical record (write-once)
        manuscript_path = run_dir / "06_final" / "manuscript_v2.md"
        if not manuscript_path.exists():
            manuscript_path = run_dir / "06_final" / "manuscript_v1.md"
        word_count = _word_count(accepted_manuscript)

        record_path = write_canonical_record(
            framework_root=framework_root,
            chapter_number=chapter_number,
            chapter_title=chapter_title,
            section_title=section_title,
            extraction=extraction,
            arc_tracker=arc_tracker,
            rubric_report=rubric_report,
            run_dir=run_dir,
            manuscript_path=manuscript_path,
            word_count=word_count,
        )
        result["canonical_record_path"] = str(record_path)
        canonical_record = _read_json(record_path)

        _append_jsonl(update_log, {
            "event": "canonical_record_written",
            "chapter_number": chapter_number,
            "path": str(record_path),
            "law_items": len(canonical_record.get("law_items") or []),
            "constraints": len(canonical_record.get("continuity_constraints") or []),
            "extraction_succeeded": extraction is not None,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # 3. Update living skeleton (structural, no AI)
        update_living_skeleton_json(
            framework_root=framework_root,
            chapter_number=chapter_number,
            canonical_record=canonical_record,
        )

        _append_jsonl(update_log, {
            "event": "living_skeleton_updated",
            "chapter_number": chapter_number,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # 4. Update doc index
        update_doc_index(
            framework_root=framework_root,
            book_root=book_root,
            chapter_number=chapter_number,
            canonical_record=canonical_record,
            run_dir=run_dir,
            manuscript_path=manuscript_path,
        )

        _append_jsonl(update_log, {
            "event": "doc_index_updated",
            "chapter_number": chapter_number,
            "timestamp": datetime.utcnow().isoformat(),
        })

        if verbose:
            print(
                f"[living_skeleton] chapter {chapter_number} accepted: "
                f"canonical record written, living skeleton updated, doc index current.",
                file=sys.stderr,
            )

    except Exception as exc:
        result["error"] = str(exc)
        _append_jsonl(update_log, {
            "event": "update_error",
            "chapter_number": chapter_number,
            "error": str(exc),
            "timestamp": datetime.utcnow().isoformat(),
        })
        if verbose:
            import traceback
            print(f"[living_skeleton] ERROR: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="living_skeleton",
        description="Inspect or manually trigger a living skeleton update.",
    )
    sub = p.add_subparsers(dest="cmd")

    # show-index: print doc_index.json in readable form
    sub.add_parser("show-index", help="Print the doc_index.json for a book.")

    # show-law: print law context for a chapter
    show_law = sub.add_parser("show-law", help="Print canonical law context for a given chapter.")
    show_law.add_argument("--chapter", type=int, required=True)

    # show-frame: print living skeleton frame for a chapter
    show_frame = sub.add_parser("show-frame", help="Print the living skeleton frame for a given chapter.")
    show_frame.add_argument("--chapter", type=int, required=True)

    for sp in [p, show_law, show_frame]:
        sp.add_argument(
            "--book-root",
            default=None,
            help="Path to the book root (e.g. book_project/my-book). "
                 "If not set, uses --output-dir + --title.",
        )
        sp.add_argument("--output-dir", default="/home/daravenrk/dragonlair/book_project")
        sp.add_argument("--title", default=None)

    return p


def _resolve_book_root(args) -> Path:
    if getattr(args, "book_root", None):
        return Path(args.book_root)
    if getattr(args, "title", None):
        import re as _re
        slug = _re.sub(r"[^\w\s-]", "", args.title.lower().strip())
        slug = _re.sub(r"[\s_-]+", "-", slug)[:60]
        return Path(args.output_dir) / slug
    raise ValueError("Provide --book-root or --title")


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    book_root = _resolve_book_root(args)
    framework_root = book_root / "framework"

    if args.cmd == "show-index":
        index = _read_json(framework_root / DOC_INDEX_FILE)
        print(json.dumps(index, indent=2))

    elif args.cmd == "show-law":
        print(load_law_context(framework_root, for_chapter_number=args.chapter))

    elif args.cmd == "show-frame":
        frame = get_future_frame(framework_root, args.chapter)
        print(json.dumps(frame, indent=2) if frame else "(no frame found)")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
