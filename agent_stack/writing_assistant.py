# Writing Assistant Agent Implementation (stub)

"""
This module provides functions to generate names, technology, personalities, and dates/history using the high-context writing-assistant agent profile.

Functions:
- generate_names(context)
- generate_technology(context)
- generate_personalities(context)
- generate_dates_history(context)

Each function should:
- Format the appropriate prompt using the templates in writing-assistant.prompts.md
- Call the orchestrator with the writing-assistant profile
- Return markdown output for export
"""


from pathlib import Path
from .orchestrator import OrchestratorAgent

PROMPT_PATH = Path(__file__).parent / "agent_profiles" / "writing-assistant.prompts.md"

def _load_prompt_templates():
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()

def _fill_template(template, context):
    # Simple curly-brace replacement
    for key, value in context.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template

def _extract_section(text, header):
    # Extracts the section under a given header (## Header) from the prompt file
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == f"## {header}".lower():
            start = i + 1
            break
    if start is None:
        return ""
    # Find next section or end
    for j in range(start, len(lines)):
        if lines[j].startswith("## ") and j > start:
            return "\n".join(lines[start:j]).strip()
    return "\n".join(lines[start:]).strip()


def _stringify(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if str(item).strip())
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if isinstance(item, (list, tuple, set)):
                joined = ", ".join(str(x) for x in item if str(x).strip())
                if joined:
                    parts.append(f"{key}: {joined}")
            elif isinstance(item, dict):
                inner = ", ".join(f"{k}={v}" for k, v in item.items())
                if inner:
                    parts.append(f"{key}: {inner}")
            elif str(item).strip():
                parts.append(f"{key}: {item}")
        return "; ".join(parts)
    return str(value)


def _clean_lines(*values):
    lines = []
    for value in values:
        text = _stringify(value).strip()
        if text:
            lines.append(text)
    return lines


def _fallback_names(context, err):
    genre = _stringify(context.get("genre") or "unknown genre")
    setting = _stringify(context.get("setting") or "unspecified setting")
    focus = _stringify(context.get("focus") or "chapter objective")
    return "\n".join(
        [
            "# Names",
            "",
            "_Deterministic fallback generated from run context._",
            "",
            f"_Reason: {err}_",
            "",
            "## Candidate Character Names",
            "- Elias Rowan",
            "- Mira Sol",
            "- Jonah Vale",
            "- Tessa Marlow",
            "- Coach Larkin",
            "",
            "## Naming Constraints",
            f"- Genre alignment: {genre}",
            f"- Setting alignment: {setting}",
            f"- Focus alignment: {focus}",
        ]
    ) + "\n"


def _fallback_technology(context, err):
    notes = _clean_lines(context.get("notes"), context.get("needs"), context.get("themes"))
    lines = [
        "# Technology",
        "",
        "_Deterministic fallback generated from run context._",
        "",
        f"_Reason: {err}_",
        "",
        "## Tools and Objects",
        "- Weathered ash bat with a high resonance sweet spot",
        "- Scuffed leather glove with repaired webbing",
        "- Chalked baseline markers and wire backstop",
        "- Portable radio carrying local game commentary",
        "",
        "## Usage Notes",
        "- Treat each object as sensory-first, not competition-first.",
        "- Prioritize sound, vibration, and touch in descriptions.",
        "",
        "## Context Signals",
    ]
    if notes:
        lines.extend(f"- {line}" for line in notes)
    else:
        lines.append("- No additional context signals provided.")
    return "\n".join(lines) + "\n"


def _fallback_personalities(context, err):
    genre = _stringify(context.get("genre") or "coming-of-age")
    focus = _stringify(context.get("focus") or "character growth")
    return "\n".join(
        [
            "# Personalities",
            "",
            "_Deterministic fallback generated from run context._",
            "",
            f"_Reason: {err}_",
            "",
            "## Protagonist Temperament",
            "- Quietly observant; notices pattern before instruction.",
            "- Emotionally sincere; avoids performative confidence.",
            "- Motivated by joy and wonder rather than status.",
            "",
            "## Supporting Character Temperament",
            "- Encouraging but restrained mentor voice.",
            "- Grounded peers with contrasting energy levels.",
            "",
            "## Continuity Constraints",
            f"- Genre: {genre}",
            f"- Chapter focus: {focus}",
        ]
    ) + "\n"


def _fallback_history(context, err):
    history = _stringify(context.get("history") or "No timeline provided")
    era = _stringify(context.get("era") or "unspecified")
    return "\n".join(
        [
            "# History",
            "",
            "_Deterministic fallback generated from run context._",
            "",
            f"_Reason: {err}_",
            "",
            "## Era",
            f"- {era}",
            "",
            "## Timeline Seeds",
            "- First encounter with the field and its ambient sounds.",
            "- First bat-contact resonance event tied to emotional awakening.",
            "- First deliberate step into participation rather than observation.",
            "",
            "## Source Timeline Context",
            f"- {history}",
        ]
    ) + "\n"


def _run_writing_assistant(prompt, fallback_builder, context):
    # Strict runtime preset forbids model_override; use profile defaults.
    try:
        agent = OrchestratorAgent()
        output = agent.handle_request_with_overrides(
            prompt,
            profile_name="writing-assistant",
            stream_override=False,
        )
        if isinstance(output, str) and output.strip():
            return output
        return fallback_builder(context, "empty model output")
    except Exception as err:
        return fallback_builder(context, str(err))

def generate_names(context):
    """Generate character names using the writing-assistant agent."""
    templates = _load_prompt_templates()
    prompt = _extract_section(templates, "Names")
    prompt = _fill_template(prompt, context)
    return _run_writing_assistant(prompt, _fallback_names, context)

def generate_technology(context):
    """Generate technology/inventions using the writing-assistant agent."""
    templates = _load_prompt_templates()
    prompt = _extract_section(templates, "Technology")
    prompt = _fill_template(prompt, context)
    return _run_writing_assistant(prompt, _fallback_technology, context)

def generate_personalities(context):
    """Generate personalities using the writing-assistant agent."""
    templates = _load_prompt_templates()
    prompt = _extract_section(templates, "Personalities")
    prompt = _fill_template(prompt, context)
    return _run_writing_assistant(prompt, _fallback_personalities, context)

def generate_dates_history(context):
    """Generate dates/history using the writing-assistant agent."""
    templates = _load_prompt_templates()
    prompt = _extract_section(templates, "Dates & History")
    prompt = _fill_template(prompt, context)
    return _run_writing_assistant(prompt, _fallback_history, context)
