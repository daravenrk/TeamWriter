import glob
import os


def _parse_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value, default=None):
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def _parse_float(value, default=None):
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


def _parse_frontmatter(frontmatter):
    data = {}
    for raw in frontmatter.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()

    if "intent_keywords" in data:
        data["intent_keywords"] = [w.strip().lower() for w in data["intent_keywords"].split(",") if w.strip()]
    else:
        data["intent_keywords"] = []

    if "priority" in data:
        try:
            data["priority"] = int(data["priority"])
        except ValueError:
            data["priority"] = 0
    else:
        data["priority"] = 0

    if "default_stream" in data:
        data["default_stream"] = _parse_bool(data["default_stream"])
    else:
        data["default_stream"] = False

    options = {}
    if "num_ctx" in data:
        parsed = _parse_int(data["num_ctx"])
        if parsed is not None:
            options["num_ctx"] = parsed
    if "num_predict" in data:
        parsed = _parse_int(data["num_predict"])
        if parsed is not None:
            options["num_predict"] = parsed
    if "temperature" in data:
        parsed = _parse_float(data["temperature"])
        if parsed is not None:
            options["temperature"] = parsed
    if "think" in data:
        options["think"] = _parse_bool(data["think"])
    # Add num_gpus to options if present
    if "num_gpus" in data:
        parsed = _parse_int(data["num_gpus"])
        if parsed is not None:
            options["num_gpus"] = parsed
    data["options"] = options

    return data


def _parse_markdown_sections(body):
    sections = {}
    current = "content"
    lines = []
    for raw in body.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("# "):
            sections[current] = "\n".join(lines).strip()
            current = line[2:].strip().lower().replace(" ", "_")
            lines = []
        else:
            lines.append(line)
    sections[current] = "\n".join(lines).strip()
    return sections


def load_agent_profiles(profile_dir):
    profiles = []
    pattern = os.path.join(profile_dir, "*.agent.md")
    for path in sorted(glob.glob(pattern)):
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()

        if not content.startswith("---\n"):
            continue
        parts = content.split("\n---\n", 1)
        if len(parts) != 2:
            continue
        frontmatter = parts[0][4:]
        body = parts[1]

        profile = _parse_frontmatter(frontmatter)
        profile["sections"] = _parse_markdown_sections(body)
        profile["source"] = path

        if "name" in profile and "route" in profile and "model" in profile:
            profiles.append(profile)

    profile_set = str(os.environ.get("AGENT_PROFILE_SET", "all")).strip().lower()
    if profile_set in {"book", "books"}:
        profiles = [p for p in profiles if str(p.get("name", "")).startswith("book-")]
    elif profile_set in {"code", "coding"}:
        profiles = [p for p in profiles if not str(p.get("name", "")).startswith("book-")]

    profiles.sort(key=lambda p: p.get("priority", 0), reverse=True)
    return profiles
