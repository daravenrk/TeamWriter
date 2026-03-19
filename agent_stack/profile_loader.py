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


def _parse_csv(value, *, lower=False):
    items = []
    for raw in str(value).split(","):
        item = raw.strip()
        if not item:
            continue
        items.append(item.lower() if lower else item)
    return items


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

    if "timeout_seconds" in data:
        parsed = _parse_int(data["timeout_seconds"])
        if parsed is not None and parsed > 0:
            data["timeout_seconds"] = parsed
        else:
            data.pop("timeout_seconds", None)

    if "retry_limit" in data:
        parsed = _parse_int(data["retry_limit"])
        if parsed is not None and parsed >= 0:
            data["retry_limit"] = parsed
        else:
            data.pop("retry_limit", None)

    if "allowed_routes" in data:
        data["allowed_routes"] = _parse_csv(data["allowed_routes"], lower=True)
    else:
        data["allowed_routes"] = []

    if "model_allowlist" in data:
        data["model_allowlist"] = _parse_csv(data["model_allowlist"])
    else:
        data["model_allowlist"] = []

    # Adaptive model-selection fields.
    # adaptive_strategy: "fast" | "quality" | "balanced" — drives how the orchestrator
    #   weights speed vs output quality when choosing among adaptive_candidates.
    # adaptive_candidates: comma-separated model names the orchestrator may pick from.
    # adaptive_min_ctx / adaptive_max_ctx: allowed num_ctx range the orchestrator may
    #   shrink or grow to match observed prompt complexity.
    if "adaptive_strategy" in data:
        strat = str(data["adaptive_strategy"]).strip().lower()
        data["adaptive_strategy"] = strat if strat in {"fast", "quality", "balanced"} else "balanced"
    else:
        data["adaptive_strategy"] = None  # not opted in

    if "adaptive_candidates" in data:
        data["adaptive_candidates"] = _parse_csv(data["adaptive_candidates"])
    else:
        data["adaptive_candidates"] = []

    if "adaptive_min_ctx" in data:
        parsed = _parse_int(data["adaptive_min_ctx"])
        data["adaptive_min_ctx"] = parsed if parsed is not None and parsed > 0 else None
    else:
        data["adaptive_min_ctx"] = None

    if "adaptive_max_ctx" in data:
        parsed = _parse_int(data["adaptive_max_ctx"])
        data["adaptive_max_ctx"] = parsed if parsed is not None and parsed > 0 else None
    else:
        data["adaptive_max_ctx"] = None

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


def validate_agent_profiles(profiles):
    """Validate agent profile collection for syntax/semantic errors.
    
    Checks:
    - All required fields present and non-empty (name, route, model)
    - No duplicate profile names
    - Valid field types and ranges
    - think field is boolean if present
    - temperature is 0.0-1.0 if present
    - num_ctx/num_predict are positive integers if present
    - priority is integer if present
    
    Returns:
        tuple: (is_valid: bool, errors: list[str])
        Errors list is empty if valid, otherwise contains error messages.
    """
    errors = []
    seen_names = set()
    
    for profile in profiles:
        source = profile.get("source", "<unknown>")
        name = profile.get("name", "").strip()
        route = profile.get("route", "").strip()
        model = profile.get("model", "").strip()
        
        # Check required fields
        if not name:
            errors.append(f"  {source}: missing or empty 'name' field")
        elif name in seen_names:
            errors.append(f"  {source}: duplicate profile name '{name}'")
        else:
            seen_names.add(name)
            
        if not route:
            errors.append(f"  {source}: missing or empty 'route' field")
            
        if not model:
            errors.append(f"  {source}: missing or empty 'model' field")
        
        # Check optional field types
        options = profile.get("options", {})
        
        # think field validation
        if "think" in options:
            if not isinstance(options["think"], bool):
                errors.append(
                    f"  {source} ({name}): 'think' must be boolean, got {type(options['think']).__name__}: "
                    f"{options['think']}"
                )
        
        # temperature validation (0.0-1.0)
        if "temperature" in options:
            temp = options["temperature"]
            if not isinstance(temp, (int, float)) or not (0.0 <= temp <= 1.0):
                errors.append(
                    f"  {source} ({name}): 'temperature' must be float 0.0-1.0, got {temp}"
                )
        
        # num_ctx/num_predict validation (positive integers)
        if "num_ctx" in options:
            ctx = options["num_ctx"]
            if not isinstance(ctx, int) or ctx <= 0:
                errors.append(
                    f"  {source} ({name}): 'num_ctx' must be positive integer, got {ctx}"
                )
        
        if "num_predict" in options:
            pred = options["num_predict"]
            if not isinstance(pred, int) or pred <= 0:
                errors.append(
                    f"  {source} ({name}): 'num_predict' must be positive integer, got {pred}"
                )
        
        # num_gpus validation (non-negative integer)
        if "num_gpus" in options:
            gpus = options["num_gpus"]
            if not isinstance(gpus, int) or gpus < 0:
                errors.append(
                    f"  {source} ({name}): 'num_gpus' must be non-negative integer, got {gpus}"
                )
        
        # priority validation (integer)
        priority = profile.get("priority")
        if priority is not None and not isinstance(priority, int):
            errors.append(
                f"  {source} ({name}): 'priority' must be integer, got {type(priority).__name__}: {priority}"
            )
        
        # allowed_routes validation (list of strings)
        allowed = profile.get("allowed_routes", [])
        if not isinstance(allowed, list) or (allowed and not all(isinstance(r, str) for r in allowed)):
            errors.append(
                f"  {source} ({name}): 'allowed_routes' must be list of strings"
            )
        
        # adaptive_strategy validation
        adaptive = profile.get("adaptive_strategy")
        if adaptive and adaptive not in {"fast", "quality", "balanced"}:
            errors.append(
                f"  {source} ({name}): 'adaptive_strategy' must be fast|quality|balanced, got {adaptive}"
            )
    
    is_valid = len(errors) == 0
    return is_valid, errors
