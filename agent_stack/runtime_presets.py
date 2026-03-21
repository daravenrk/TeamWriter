import json
from pathlib import Path


DEFAULT_RUNTIME_PRESETS_PATH = Path(__file__).parent / "runtime_presets.json"
SUPPORTED_ROUTES = {"ollama_amd", "ollama_nvidia"}
ALLOWED_OPTION_KEYS = {
    "num_ctx",
    "num_predict",
    "temperature",
    "think",
    "num_gpu",
    "num_gpus",
}


def _parse_bool(value):
    if isinstance(value, bool):
        return value
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


def _normalize_options(options, preset_name):
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise ValueError(f"runtime preset '{preset_name}' options must be an object")

    normalized = {}
    unknown = sorted(set(options) - ALLOWED_OPTION_KEYS)
    if unknown:
        raise ValueError(
            f"runtime preset '{preset_name}' has unsupported option keys: {', '.join(unknown)}"
        )

    for key, value in options.items():
        if key in {"num_ctx", "num_predict", "num_gpu", "num_gpus"}:
            parsed = _parse_int(value)
            if parsed <= 0 and key not in {"num_gpu", "num_gpus"}:
                raise ValueError(f"runtime preset '{preset_name}' option {key} must be > 0")
            if parsed < 0 and key in {"num_gpu", "num_gpus"}:
                raise ValueError(f"runtime preset '{preset_name}' option {key} must be >= 0")
            normalized[key] = parsed
        elif key == "temperature":
            parsed = _parse_float(value)
            if parsed < 0.0 or parsed > 2.0:
                raise ValueError(
                    f"runtime preset '{preset_name}' option temperature must be between 0.0 and 2.0"
                )
            normalized[key] = parsed
        elif key == "think":
            normalized[key] = _parse_bool(value)

    return normalized


def load_runtime_presets(path=DEFAULT_RUNTIME_PRESETS_PATH):
    preset_path = Path(path)
    if not preset_path.exists():
        return {}

    try:
        raw = json.loads(preset_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid runtime preset JSON in {preset_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"runtime preset file {preset_path} must contain a JSON object")

    entries = raw.get("presets", raw)
    if not isinstance(entries, dict):
        raise ValueError(f"runtime preset file {preset_path} must contain a 'presets' object")

    normalized = {}
    for preset_name, payload in entries.items():
        if not isinstance(payload, dict):
            raise ValueError(f"runtime preset '{preset_name}' must be an object")

        route = str(payload.get("route") or "").strip()
        model = str(payload.get("model") or "").strip()
        if route not in SUPPORTED_ROUTES:
            raise ValueError(
                f"runtime preset '{preset_name}' must use a supported route: {sorted(SUPPORTED_ROUTES)}"
            )
        if not model:
            raise ValueError(f"runtime preset '{preset_name}' is missing model")

        normalized[preset_name] = {
            "route": route,
            "model": model,
            "options": _normalize_options(payload.get("options"), preset_name),
        }

    return normalized