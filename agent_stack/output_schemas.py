RUBRIC_SCORE_KEYS = [
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


def _type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value, expected_type):
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
    if expected_type == "null":
        return value is None
    return False


def _format_path(path_parts):
    if not path_parts:
        return "payload"
    rendered = "payload"
    for part in path_parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def _validate_node(value, schema, path_parts, errors):
    expected_types = schema.get("type")
    if isinstance(expected_types, str):
        expected_types = [expected_types]
    if expected_types:
        if not any(_matches_type(value, item) for item in expected_types):
            expected_label = expected_types[0] if len(expected_types) == 1 else " or ".join(expected_types)
            errors.append(f"{_format_path(path_parts)} must be {expected_label}, got {_type_name(value)}")
            return

    if value is None:
        return

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(item) for item in schema["enum"])
        errors.append(f"{_format_path(path_parts)} must be one of: {allowed}")
        return

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(value.strip()) < min_length:
            errors.append(f"{_format_path(path_parts)} must have length >= {min_length}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            errors.append(f"{_format_path(path_parts)} must be >= {minimum}")

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            errors.append(f"{_format_path(path_parts)} must have at least {min_items} items")
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(value):
                _validate_node(item, item_schema, path_parts + [idx], errors)
        return

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{_format_path(path_parts)} missing required field '{key}'")

        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                _validate_node(value[key], child_schema, path_parts + [key], errors)


def validate_payload(payload, schema):
    errors = []
    _validate_node(payload, schema, [], errors)
    return errors


STAGE_OUTPUT_SCHEMAS = {
    "publisher_brief": {
        "type": "object",
        "required": [
            "title_working",
            "genre",
            "audience",
            "target_word_count",
            "page_target",
            "tone",
            "constraints",
            "acceptance_criteria",
        ],
        "properties": {
            "title_working": {"type": "string", "minLength": 1},
            "genre": {"type": "string", "minLength": 1},
            "audience": {"type": "string", "minLength": 1},
            "target_word_count": {"type": ["string", "integer", "number"]},
            "page_target": {"type": ["string", "integer", "number"]},
            "tone": {"type": "string", "minLength": 1},
            "constraints": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
            "acceptance_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
        },
    },
    "architect_outline": {
        "type": "object",
        "required": ["master_outline_markdown", "book_structure"],
        "properties": {
            "master_outline_markdown": {"type": "string", "minLength": 1},
            "book_structure": {"type": ["object", "array"]},
            "pacing_notes": {"type": ["string", "array", "object"]},
        },
    },
    "chapter_planner": {
        "type": "object",
        "required": [
            "chapter_number",
            "chapter_title",
            "purpose",
            "target_words",
            "sections",
            "must_include",
            "must_avoid",
            "ending_hook",
        ],
        "properties": {
            "chapter_number": {"type": ["integer", "number", "string"]},
            "chapter_title": {"type": "string", "minLength": 1},
            "purpose": {"type": "string", "minLength": 1},
            "target_words": {"type": ["integer", "number", "string"]},
            "sections": {"type": "array", "minItems": 2, "items": {"type": ["string", "object"]}},
            "must_include": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "must_avoid": {"type": "array", "items": {"type": "string", "minLength": 1}},
            "ending_hook": {"type": "string", "minLength": 1},
        },
    },
    "canon": {
        "type": "object",
        "required": ["canon", "timeline", "character_bible", "open_loops", "style_guide"],
        "properties": {
            "canon": {"type": "object"},
            "timeline": {"type": ["object", "array"]},
            "character_bible": {"type": ["object", "array"]},
            "open_loops": {"type": "array"},
            "style_guide": {"type": "string", "minLength": 1},
        },
    },
    "section_review": {
        "type": "object",
        "required": ["blocking_issues", "warnings", "section_summary", "continuity_state_updates"],
        "properties": {
            "blocking_issues": {"type": "array"},
            "warnings": {"type": "array"},
            "section_summary": {"type": "string", "minLength": 1},
            "continuity_state_updates": {"type": "array"},
        },
    },
    "story_architect_review": {
        "type": "object",
        "required": ["concept_validation", "structure_validation", "notes", "revision_focus"],
        "properties": {
            "concept_validation": {"type": ["integer", "number"]},
            "structure_validation": {"type": ["integer", "number"]},
            "notes": {"type": ["string", "array", "object"]},
            "revision_focus": {"type": ["string", "array", "object"]},
        },
    },
    "assembly_review": {
        "type": "object",
        "required": ["blocking_issues", "warnings", "continuity_notes"],
        "properties": {
            "blocking_issues": {"type": "array"},
            "warnings": {"type": "array"},
            "continuity_notes": {"type": ["string", "array", "object"]},
        },
    },
    "developmental_editor": {
        "type": "object",
        "required": ["pass", "scores", "notes", "rewrite_instructions"],
        "properties": {
            "pass": {"type": "boolean"},
            "scores": {"type": "object"},
            "notes": {"type": ["string", "array", "object"]},
            "rewrite_instructions": {"type": ["string", "array", "object"]},
        },
    },
    "session_reviewer": {
        "type": "object",
        "required": ["scores", "notes", "next_writer_notes"],
        "properties": {
            "scores": {
                "type": "object",
                "required": RUBRIC_SCORE_KEYS,
                "properties": {
                    key: {"type": ["integer", "number"]}
                    for key in RUBRIC_SCORE_KEYS
                },
            },
            "notes": {"type": ["string", "array", "object"]},
            "next_writer_notes": {
                "type": "object",
                "required": [
                    "focus_topics",
                    "continuity_watch",
                    "must_carry_forward",
                    "character_state_updates",
                    "timeline_events",
                    "unresolved_questions",
                ],
                "properties": {
                    "focus_topics": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                    "continuity_watch": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                    "must_carry_forward": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                    "character_state_updates": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "timeline_events": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "unresolved_questions": {"type": "array", "items": {"type": "string", "minLength": 1}},
                },
            },
        },
    },
    "continuity": {
        "type": "object",
        "required": ["blocking_issues", "warnings", "patch_tasks", "summary"],
        "properties": {
            "blocking_issues": {"type": "array"},
            "warnings": {"type": "array"},
            "patch_tasks": {"type": "array"},
            "summary": {"type": ["string", "array", "object"]},
        },
    },
    "publisher_qa": {
        "type": "object",
        "required": ["decision", "required_fixes", "summary"],
        "properties": {
            "decision": {"type": "string", "minLength": 1},
            "scores": {"type": ["object", "array"]},
            "notes": {"type": ["string", "array", "object"]},
            "required_fixes": {"type": "array"},
            "summary": {"type": ["string", "array", "object"]},
        },
    },
    "story_skeleton": {
        "type": "object",
        "required": ["story_spine", "major_beats", "open_loops", "character_arcs", "chapter_frames"],
        "properties": {
            "story_spine": {"type": "string", "minLength": 10},
            "major_beats": {"type": "array", "minItems": 1},
            "open_loops": {"type": "array"},
            "character_arcs": {"type": "array"},
            "chapter_frames": {"type": "array", "minItems": 1},
            "series_threads": {"type": "array"},
        },
    },
}


def validate_stage_payload(schema_name, payload):
    schema = STAGE_OUTPUT_SCHEMAS.get(schema_name)
    if schema is None:
        raise ValueError(f"Unknown stage output schema: {schema_name}")
    errors = validate_payload(payload, schema)
    if errors:
        return False, errors[0]
    return True, "ok"