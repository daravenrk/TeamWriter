"""Story Skeleton Pre-Run (Todo 88).

Generates a complete story skeleton (story_spine, major_beats, open_loops,
character_arcs, chapter_frames, series_threads) using a high-context fast
model on AMD before any chapter writing begins.

Usage:
    python3 -m agent_stack.skeleton_flow \\
        --title "My Novel" \\
        --premise "A young mage discovers..." \\
        --genre "fantasy" \\
        --chapters 12 \\
        [--audience "adult"] \\
        [--tone "cinematic and emotionally grounded"] \\
        [--series] \\
        [--series-title "The Dragon Cycle"] \\
        [--refresh-skeleton] \\
        [--output-dir /home/daravenrk/dragonlair/book_project] \\
        [-v]

The skeleton is saved to:
    <output-dir>/<book-slug>/framework/story_skeleton.json

Subsequent chapter runs load this artifact to guide the writer and reviews.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from .exceptions import AgentStackError, FrameworkIntegrityError, StageQualityGateError
from .orchestrator import OrchestratorAgent
from .output_schemas import validate_stage_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:60]


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default if default is not None else {}


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _parse_json_block(text: str) -> dict | None:
    """Extract the first JSON object from a text response."""
    text = (text or "").strip()
    # Try direct parse first
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # Try extracting from a markdown code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    # Try first { ... } span
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
                        result = json.loads(text[start : i + 1])
                        if isinstance(result, dict):
                            return result
                    except json.JSONDecodeError:
                        break
    return None


# ---------------------------------------------------------------------------
# Skeleton contract builder
# ---------------------------------------------------------------------------

def build_skeleton_contract(args: argparse.Namespace) -> str:
    series_note = ""
    if args.series:
        series_title = getattr(args, "series_title", None) or "this series"
        series_note = (
            f"\nThis book is part of a series called '{series_title}'. "
            "For any open loop or character arc that intentionally continues beyond this book, "
            "set resolves_chapter to \"series\" and add a series_threads entry. "
            "The series_threads array must list every thread that carries beyond this book with notes on what the next book inherits."
        )

    return f"""You are a Story Architect. Generate a complete structural story skeleton for the following book.

BOOK BRIEF:
- Title: {args.title}
- Genre: {args.genre}
- Audience: {args.audience}
- Tone: {args.tone}
- Premise: {args.premise}
- Total chapters: {args.chapters}
- Target word count: {getattr(args, "target_word_count", 100000):,}{series_note}

Return ONLY a valid JSON object with exactly these top-level keys:

{{
  "story_spine": "<single sentence capturing the full narrative arc>",
  "major_beats": [
    {{"beat": "<event name>", "chapter": <int>, "type": "<inciting_incident|midpoint|dark_night|climax|resolution|other>", "description": "<1-2 sentences>"}}
  ],
  "open_loops": [
    {{"loop": "<thread name>", "opens_chapter": <int>, "resolves_chapter": <int or "series">, "resolve_type": "<answered|subverted|deferred>", "description": "<what this thread is and why it matters>"}}
  ],
  "character_arcs": [
    {{"name": "<character name>", "role": "<protagonist|antagonist|supporting|minor>", "starting_state": "<brief>", "arc_milestones": [{{"chapter": <int>, "state": "<brief>"}}], "ending_state": "<brief>"}}
  ],
  "chapter_frames": [
    {{
      "chapter": <int>,
      "title": "<working title>",
      "purpose": "<what this chapter accomplishes in the story>",
      "opens_loops": ["<loop name>"],
      "sustains_loops": ["<loop name — present but NOT resolved here>"],
      "closes_loops": ["<loop name>"],
      "must_set_up": ["<thing that a later chapter depends on>"],
      "must_not_resolve": ["<loop name that must remain open>"],
      "tone": "<tone for this chapter>",
      "hard_constraints": ["<2-3 hardest rules this chapter's writer must follow>"]
    }}
  ],
  "series_threads": []
}}

RULES:
- Generate a chapter_frames entry for EVERY chapter from 1 to {args.chapters}. Do not skip any.
- Every open_loop must have both opens_chapter and resolves_chapter set.
- resolves_chapter must be >= opens_chapter (a loop cannot resolve before it opens).
- Every loop listed in a chapter's closes_loops must have resolves_chapter equal to that chapter number.
- Every loop listed in a chapter's must_not_resolve must appear in sustains_loops for that chapter.
- Character arcs must cover the full span of the book, not just early chapters.
- Return ONLY the JSON object. No markdown. No explanation. No prose outside the JSON.
"""


# ---------------------------------------------------------------------------
# Validation gate
# ---------------------------------------------------------------------------

def gate_skeleton(skeleton: dict, expected_chapters: int) -> tuple[bool, str]:
    """Validate structural completeness of the story skeleton."""
    issues: list[str] = []

    story_spine = skeleton.get("story_spine")
    if not isinstance(story_spine, str) or len(story_spine.strip()) < 10:
        issues.append("story_spine missing or too short")

    major_beats = skeleton.get("major_beats") or []
    if not isinstance(major_beats, list) or len(major_beats) < 2:
        issues.append("major_beats must have at least 2 entries")

    open_loops = skeleton.get("open_loops") or []
    if isinstance(open_loops, list):
        for i, loop in enumerate(open_loops):
            if not isinstance(loop, dict):
                continue
            opens = loop.get("opens_chapter")
            resolves = loop.get("resolves_chapter")
            if opens is None:
                issues.append(f"open_loops[{i}] missing opens_chapter")
            if resolves is None:
                issues.append(f"open_loops[{i}] missing resolves_chapter")
            if isinstance(opens, int) and isinstance(resolves, int) and resolves < opens:
                issues.append(
                    f"open_loops[{i}] '{loop.get('loop', '')}': "
                    f"resolves_chapter={resolves} is before opens_chapter={opens}"
                )

    chapter_frames = skeleton.get("chapter_frames") or []
    if not isinstance(chapter_frames, list):
        issues.append("chapter_frames must be an array")
    else:
        frame_chapters = {int(f["chapter"]) for f in chapter_frames if isinstance(f, dict) and "chapter" in f}
        for ch in range(1, expected_chapters + 1):
            if ch not in frame_chapters:
                issues.append(f"chapter_frames missing entry for chapter {ch}")

    character_arcs = skeleton.get("character_arcs") or []
    if not isinstance(character_arcs, list) or len(character_arcs) == 0:
        issues.append("character_arcs must have at least one entry")

    if issues:
        return False, f"skeleton gate failed ({len(issues)} issue(s)): " + "; ".join(issues[:5])
    return True, "ok"


# ---------------------------------------------------------------------------
# Arc tracker pre-population from skeleton
# ---------------------------------------------------------------------------

def pre_populate_arc_tracker_from_skeleton(
    skeleton: dict,
    arc_tracker_path: Path,
    chapter_number: int,
) -> None:
    """Seed arc_tracker.open_loops from skeleton before chapter 1 writes.

    Loops are tagged [PLANNED] to distinguish them from runtime-discovered loops.
    Existing loops (from prior runs) are preserved.
    """
    existing = _read_json(arc_tracker_path, default={})
    existing.setdefault("story_arcs", [])
    existing.setdefault("character_arcs", [])
    existing.setdefault("open_loops", [])
    existing.setdefault("chapter_progress", [])

    # Build a deduplicated set of existing loop keys
    existing_keys = {str(l).lower()[:60] for l in existing["open_loops"]}

    skeleton_loops = skeleton.get("open_loops") or []
    for loop in skeleton_loops:
        if not isinstance(loop, dict):
            continue
        name = str(loop.get("loop") or "").strip()
        if not name:
            continue
        key = name.lower()[:60]
        if key in existing_keys:
            continue
        # Tag as planned with resolution metadata
        annotated = (
            f"[PLANNED] {name} "
            f"(opens ch{loop.get('opens_chapter', '?')}, "
            f"resolves ch{loop.get('resolves_chapter', '?')}, "
            f"type={loop.get('resolve_type', 'unknown')})"
        )
        existing["open_loops"].append(annotated)
        existing_keys.add(key)

    existing["last_updated"] = datetime.utcnow().isoformat()
    existing["skeleton_seeded"] = True
    _write_json(arc_tracker_path, existing)


# ---------------------------------------------------------------------------
# Main skeleton runner
# ---------------------------------------------------------------------------

def run_skeleton(args: argparse.Namespace) -> dict:
    """Run the story skeleton pre-pass and return the skeleton artifact."""
    output_root = Path(getattr(args, "output_dir", "/home/daravenrk/dragonlair/book_project"))
    book_slug = _slugify(args.title)
    framework_root = output_root / book_slug / "framework"
    framework_root.mkdir(parents=True, exist_ok=True)

    skeleton_path = framework_root / "story_skeleton.json"
    skeleton_log = framework_root / "skeleton_run_log.jsonl"

    # Reuse existing skeleton unless --refresh-skeleton is set
    if skeleton_path.exists() and not getattr(args, "refresh_skeleton", False):
        existing = _read_json(skeleton_path)
        if existing and isinstance(existing, dict) and existing.get("story_spine"):
            print(f"[skeleton] Re-using existing skeleton: {skeleton_path}", file=sys.stderr)
            _append_jsonl(skeleton_log, {
                "event": "skeleton_reused",
                "path": str(skeleton_path),
                "timestamp": datetime.utcnow().isoformat(),
            })
            return existing

    # Build orchestrator
    orchestrator = OrchestratorAgent(verbose=getattr(args, "verbose", False))

    # Build contract
    contract = build_skeleton_contract(args)

    _append_jsonl(skeleton_log, {
        "event": "skeleton_run_start",
        "title": args.title,
        "chapters": args.chapters,
        "series": getattr(args, "series", False),
        "timestamp": datetime.utcnow().isoformat(),
    })

    max_retries = getattr(args, "max_retries", 2)
    last_error = ""
    skeleton: dict | None = None

    for attempt in range(1, max_retries + 2):
        prompt = contract
        if attempt > 1 and last_error:
            prompt = (
                f"Your previous attempt failed validation with this error:\n{last_error}\n\n"
                "Fix the issues and return the corrected JSON skeleton.\n\n"
                + contract
            )

        try:
            raw = orchestrator.run(
                prompt=prompt,
                agent_name="book-story-skeleton",
                profile_name="book-story-skeleton",
            )
        except AgentStackError as e:
            last_error = str(e)
            _append_jsonl(skeleton_log, {
                "event": "skeleton_attempt_failed",
                "attempt": attempt,
                "error": last_error,
                "timestamp": datetime.utcnow().isoformat(),
            })
            if attempt > max_retries + 1:
                raise
            continue

        parsed = _parse_json_block(raw if isinstance(raw, str) else json.dumps(raw))
        if parsed is None:
            last_error = "response did not contain a valid JSON object"
            _append_jsonl(skeleton_log, {
                "event": "skeleton_parse_failed",
                "attempt": attempt,
                "error": last_error,
                "timestamp": datetime.utcnow().isoformat(),
            })
            continue

        # Schema validation
        schema_ok, schema_msg = validate_stage_payload("story_skeleton", parsed)
        if not schema_ok:
            last_error = f"schema validation failed: {schema_msg}"
            _append_jsonl(skeleton_log, {
                "event": "skeleton_schema_failed",
                "attempt": attempt,
                "error": last_error,
                "timestamp": datetime.utcnow().isoformat(),
            })
            continue

        # Structural gate
        gate_ok, gate_msg = gate_skeleton(parsed, args.chapters)
        if not gate_ok:
            last_error = gate_msg
            _append_jsonl(skeleton_log, {
                "event": "skeleton_gate_failed",
                "attempt": attempt,
                "error": last_error,
                "timestamp": datetime.utcnow().isoformat(),
            })
            continue

        # Passed — write and return
        skeleton = parsed
        skeleton["_meta"] = {
            "generated_at": datetime.utcnow().isoformat(),
            "title": args.title,
            "chapters": args.chapters,
            "series": getattr(args, "series", False),
            "attempt": attempt,
        }
        _write_json(skeleton_path, skeleton)
        _append_jsonl(skeleton_log, {
            "event": "skeleton_accepted",
            "attempt": attempt,
            "open_loops": len(parsed.get("open_loops") or []),
            "chapter_frames": len(parsed.get("chapter_frames") or []),
            "character_arcs": len(parsed.get("character_arcs") or []),
            "timestamp": datetime.utcnow().isoformat(),
        })
        print(f"[skeleton] Written: {skeleton_path}", file=sys.stderr)
        return skeleton

    raise StageQualityGateError(
        f"Story skeleton failed after {max_retries + 1} attempts. Last error: {last_error}",
        details={"last_error": last_error, "title": args.title},
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skeleton_flow",
        description="Generate a story skeleton pre-run artifact for a book or series.",
    )
    p.add_argument("--title", required=True, help="Working title of the book")
    p.add_argument("--premise", required=True, help="Core story premise")
    p.add_argument("--chapters", type=int, required=True, help="Total number of chapters")
    p.add_argument("--genre", default="speculative fiction")
    p.add_argument("--audience", default="adult")
    p.add_argument("--tone", default="cinematic and emotionally grounded")
    p.add_argument("--target-word-count", "--target_word_count", dest="target_word_count", type=int, default=100000)
    p.add_argument("--series", action="store_true", help="Mark this as part of a multi-book series")
    p.add_argument("--series-title", "--series_title", dest="series_title", default=None)
    p.add_argument("--refresh-skeleton", "--refresh_skeleton", dest="refresh_skeleton", action="store_true",
                   help="Force re-run even if a skeleton already exists")
    p.add_argument("--max-retries", "--max_retries", dest="max_retries", type=int, default=2)
    p.add_argument("--output-dir", "--output_dir", dest="output_dir",
                   default="/home/daravenrk/dragonlair/book_project")
    p.add_argument("--pre-populate-arc-tracker", "--pre_populate_arc_tracker",
                   dest="pre_populate_arc_tracker", action="store_true",
                   help="Seed arc_tracker.json with planned open loops from the skeleton")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    skeleton = run_skeleton(args)

    if getattr(args, "pre_populate_arc_tracker", False):
        output_root = Path(args.output_dir)
        book_slug = _slugify(args.title)
        arc_tracker_path = output_root / book_slug / "framework" / "arc_tracker.json"
        pre_populate_arc_tracker_from_skeleton(skeleton, arc_tracker_path, chapter_number=1)
        print(f"[skeleton] arc_tracker pre-populated: {arc_tracker_path}", file=sys.stderr)

    # Print summary to stdout
    frames = len(skeleton.get("chapter_frames") or [])
    loops = len(skeleton.get("open_loops") or [])
    arcs = len(skeleton.get("character_arcs") or [])
    beats = len(skeleton.get("major_beats") or [])
    print(
        f"Skeleton complete: {frames} chapter frames, {loops} open loops, "
        f"{arcs} character arcs, {beats} major beats."
    )
    print(f"Story spine: {skeleton.get('story_spine', '(not set)')}")


if __name__ == "__main__":
    main()
