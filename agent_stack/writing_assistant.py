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

def generate_names(context):
    """Generate character names using the writing-assistant agent."""
    templates = _load_prompt_templates()
    prompt = _extract_section(templates, "Names")
    prompt = _fill_template(prompt, context)
    agent = OrchestratorAgent()
    return agent.handle_request_with_overrides(
        prompt,
        profile_name="writing-assistant",
        model_override="qwen3.5:4b",
        stream_override=False,
    )

def generate_technology(context):
    """Generate technology/inventions using the writing-assistant agent."""
    templates = _load_prompt_templates()
    prompt = _extract_section(templates, "Technology")
    prompt = _fill_template(prompt, context)
    agent = OrchestratorAgent()
    return agent.handle_request_with_overrides(
        prompt,
        profile_name="writing-assistant",
        model_override="qwen3.5:4b",
        stream_override=False,
    )

def generate_personalities(context):
    """Generate personalities using the writing-assistant agent."""
    templates = _load_prompt_templates()
    prompt = _extract_section(templates, "Personalities")
    prompt = _fill_template(prompt, context)
    agent = OrchestratorAgent()
    return agent.handle_request_with_overrides(
        prompt,
        profile_name="writing-assistant",
        model_override="qwen3.5:4b",
        stream_override=False,
    )

def generate_dates_history(context):
    """Generate dates/history using the writing-assistant agent."""
    templates = _load_prompt_templates()
    prompt = _extract_section(templates, "Dates & History")
    prompt = _fill_template(prompt, context)
    agent = OrchestratorAgent()
    return agent.handle_request_with_overrides(
        prompt,
        profile_name="writing-assistant",
        model_override="qwen3.5:4b",
        stream_override=False,
    )
