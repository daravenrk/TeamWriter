import argparse
import json
import os
import re
from pathlib import Path

PROFILE_DIR = Path(__file__).parent / "agent_profiles"
SUPPORTED_ROUTES = {"ollama_amd", "ollama_nvidia"}
DEFAULT_MAX_SYSTEM_PROMPT_CHARS = max(1, int(os.environ.get("AGENT_MAX_SYSTEM_PROMPT_CHARS", "12000")))
REQUIRED_FRONTMATTER_KEYS = ("name", "route", "model")
REQUIRED_SECTION_KEYS = {
    "purpose": "# Purpose",
    "system_behavior": "# System Behavior",
    "actions": "# Actions",
}
ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "route",
    "model",
    "default_stream",
    "intent_keywords",
    "priority",
    "num_ctx",
    "num_predict",
    "temperature",
    "think",
    "num_gpus",
    "timeout_seconds",
    "retry_limit",
    "allowed_routes",
    "model_allowlist",
    "adaptive_strategy",
    "adaptive_candidates",
    "adaptive_min_ctx",
    "adaptive_max_ctx",
}
PROFILE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PLACEHOLDER_PATTERN = re.compile(r"\b(todo|tbd|fixme)\b", re.IGNORECASE)


def _parse_bool(value):
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"expected boolean value, got {value!r}")


def _parse_int(value):
    return int(str(value).strip())


def _parse_float(value):
    return float(str(value).strip())


def _split_profile_document(content):
    if not content.startswith("---\n"):
        return None, None, ["missing opening frontmatter delimiter '---'"]
    parts = content.split("\n---\n", 1)
    if len(parts) != 2:
        return None, None, ["missing closing frontmatter delimiter '---'"]
    return parts[0][4:], parts[1], []


def _parse_frontmatter(frontmatter_text):
    data = {}
    errors = []
    duplicate_keys = []
    for index, raw_line in enumerate(frontmatter_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            errors.append(f"frontmatter line {index} is not key:value syntax")
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in data:
            duplicate_keys.append(key)
        data[key] = value
    for key in duplicate_keys:
        errors.append(f"duplicate frontmatter key: {key}")
    return data, errors


def _parse_sections(body):
    sections = {}
    current = None
    lines = []
    for raw in body.splitlines():
        if raw.startswith("# "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = raw[2:].strip().lower().replace(" ", "_")
            lines = []
            continue
        lines.append(raw)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def _render_system_prompt(profile_name, sections):
    blocks = [f"Agent Profile: {profile_name or 'default'}"]
    ordered_sections = [
        ("purpose", "Purpose"),
        ("system_behavior", "System Behavior"),
        ("actions", "Actions"),
    ]
    rendered = set()

    for key, title in ordered_sections:
        content = str(sections.get(key) or "").strip()
        if not content:
            continue
        rendered.add(key)
        blocks.extend(["", f"{title}:", content])

    for key, value in sections.items():
        if key in rendered:
            continue
        content = str(value or "").strip()
        if not content:
            continue
        title = " ".join(part.capitalize() for part in key.split("_") if part) or "Section"
        blocks.extend(["", f"{title}:", content])

    return "\n".join(blocks).strip()


def validate_profile(path, max_system_prompt_chars=DEFAULT_MAX_SYSTEM_PROMPT_CHARS):
    content = path.read_text(encoding="utf-8")
    frontmatter_text, body, split_errors = _split_profile_document(content)
    report = {
        "path": str(path),
        "name": None,
        "errors": list(split_errors),
        "warnings": [],
        "stats": {
            "system_prompt_chars": 0,
            "body_chars": len(body or ""),
        },
    }
    if split_errors:
        return report

    frontmatter, parse_errors = _parse_frontmatter(frontmatter_text)
    report["errors"].extend(parse_errors)
    report["name"] = frontmatter.get("name") or path.stem

    unknown_keys = sorted(set(frontmatter) - ALLOWED_FRONTMATTER_KEYS)
    if unknown_keys:
        report["errors"].append(f"unsupported frontmatter keys: {', '.join(unknown_keys)}")

    for key in REQUIRED_FRONTMATTER_KEYS:
        if not str(frontmatter.get(key) or "").strip():
            report["errors"].append(f"missing required frontmatter key: {key}")

    profile_name = str(frontmatter.get("name") or "").strip()
    if profile_name and not PROFILE_NAME_PATTERN.match(profile_name):
        report["errors"].append("profile name must match ^[a-z0-9][a-z0-9-]*$")

    route = str(frontmatter.get("route") or "").strip()
    if route and route not in SUPPORTED_ROUTES:
        report["errors"].append(f"unsupported route: {route}")

    if "default_stream" in frontmatter:
        try:
            _parse_bool(frontmatter["default_stream"])
        except ValueError as exc:
            report["errors"].append(f"default_stream {exc}")

    if "think" in frontmatter:
        try:
            _parse_bool(frontmatter["think"])
        except ValueError as exc:
            report["errors"].append(f"think {exc}")

    for int_key in ("num_ctx", "num_predict", "priority", "num_gpus", "timeout_seconds", "retry_limit"):
        if int_key not in frontmatter:
            continue
        try:
            parsed = _parse_int(frontmatter[int_key])
        except ValueError:
            report["errors"].append(f"{int_key} must be an integer")
            continue
        if int_key in {"num_ctx", "num_predict", "timeout_seconds"} and parsed <= 0:
            report["errors"].append(f"{int_key} must be > 0")
        if int_key in {"num_gpus", "retry_limit"} and parsed < 0:
            report["errors"].append(f"{int_key} must be >= 0")

    if "temperature" in frontmatter:
        try:
            temperature = _parse_float(frontmatter["temperature"])
            if temperature < 0.0 or temperature > 2.0:
                report["errors"].append("temperature must be between 0.0 and 2.0")
        except ValueError:
            report["errors"].append("temperature must be a float")

    for list_key in ("intent_keywords", "allowed_routes", "model_allowlist"):
        if list_key not in frontmatter:
            continue
        values = [item.strip() for item in str(frontmatter[list_key]).split(",") if item.strip()]
        if not values:
            report["errors"].append(f"{list_key} must contain at least one value when present")
        if list_key == "allowed_routes":
            unsupported = sorted({value for value in values if value not in SUPPORTED_ROUTES})
            if unsupported:
                report["errors"].append(f"allowed_routes contains unsupported routes: {', '.join(unsupported)}")

    sections = _parse_sections(body)
    if not sections:
        report["errors"].append("missing markdown sections")

    for section_key, heading in REQUIRED_SECTION_KEYS.items():
        if section_key not in sections:
            report["errors"].append(f"missing required section: {heading}")
            continue
        if not str(sections.get(section_key) or "").strip():
            report["errors"].append(f"section must not be empty: {heading}")

    rendered_prompt = _render_system_prompt(profile_name, sections)
    prompt_chars = len(rendered_prompt)
    report["stats"]["system_prompt_chars"] = prompt_chars
    if prompt_chars > max_system_prompt_chars:
        report["errors"].append(
            f"rendered system prompt is too large: {prompt_chars} chars > {max_system_prompt_chars}"
        )
    elif prompt_chars > int(max_system_prompt_chars * 0.8):
        report["warnings"].append(
            f"rendered system prompt is near the limit: {prompt_chars}/{max_system_prompt_chars} chars"
        )

    if PLACEHOLDER_PATTERN.search(body):
        report["warnings"].append("profile body contains placeholder text (todo/tbd/fixme)")

    return report


def lint_profiles(profile_dir=PROFILE_DIR, max_system_prompt_chars=DEFAULT_MAX_SYSTEM_PROMPT_CHARS):
    profile_dir = Path(profile_dir)
    reports = [
        validate_profile(path, max_system_prompt_chars=max_system_prompt_chars)
        for path in sorted(profile_dir.glob("*.agent.md"))
    ]

    paths_by_name = {}
    for report in reports:
        name = str(report.get("name") or "").strip()
        if not name:
            continue
        paths_by_name.setdefault(name, []).append(report)

    for name, grouped in paths_by_name.items():
        if len(grouped) < 2:
            continue
        for report in grouped:
            report["errors"].append(f"duplicate profile name: {name}")

    error_count = sum(len(report["errors"]) for report in reports)
    warning_count = sum(len(report["warnings"]) for report in reports)
    return {
        "profile_dir": str(profile_dir),
        "max_system_prompt_chars": int(max_system_prompt_chars),
        "profile_count": len(reports),
        "valid": error_count == 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "profiles": reports,
    }


def _print_text_report(report):
    for profile in report["profiles"]:
        label = profile.get("name") or Path(profile["path"]).name
        if profile["errors"]:
            print(f"[FAIL] {label}")
            for error in profile["errors"]:
                print(f"  - error: {error}")
        elif profile["warnings"]:
            print(f"[WARN] {label}")
            for warning in profile["warnings"]:
                print(f"  - warning: {warning}")
        else:
            print(f"[OK]   {label}")
        print(f"  - system_prompt_chars: {profile['stats']['system_prompt_chars']}")
    status = "passed" if report["valid"] else "failed"
    print(
        f"Profile lint {status}: {report['profile_count']} profiles, "
        f"{report['error_count']} errors, {report['warning_count']} warnings"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate Dragonlair markdown agent profiles")
    parser.add_argument("--profile-dir", default=str(PROFILE_DIR))
    parser.add_argument("--max-system-prompt-chars", type=int, default=DEFAULT_MAX_SYSTEM_PROMPT_CHARS)
    parser.add_argument("--json", action="store_true", help="Emit full JSON report")
    args = parser.parse_args(argv)

    report = lint_profiles(
        profile_dir=Path(args.profile_dir),
        max_system_prompt_chars=max(1, int(args.max_system_prompt_chars)),
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_text_report(report)
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
