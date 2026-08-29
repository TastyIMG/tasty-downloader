import json
import os
from pathlib import Path

from .config_store import load_config, save_config
from .paths import DEFAULT_REGISTRY_PATH, LOCAL_REGISTRY_PATH


def get_registry_path():
    env_path = os.environ.get("TASTY_R2_REGISTRY_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()

    custom_path = (load_config().get("registry_path") or "").strip()
    if custom_path:
        return Path(custom_path).expanduser()

    return DEFAULT_REGISTRY_PATH


def parse_registry(data):
    if isinstance(data, dict) and "models" in data:
        return data["models"]
    if isinstance(data, list):
        return data
    return []


def is_placeholder_entry(entry):
    url = (entry.get("url") or "").lower()
    filename = (entry.get("filename") or "").lower()
    if "pub-xxxx" in url or "example.com" in url:
        return True
    if filename.startswith("example_"):
        return True
    return False


def read_registry_file(path):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        entries = parse_registry(json.load(f))
    return [e for e in entries if isinstance(e, dict) and not is_placeholder_entry(e)]


def read_config_models():
    models = load_config().get("models") or []
    if not isinstance(models, list):
        return []
    return [e for e in models if isinstance(e, dict) and not is_placeholder_entry(e)]


def load_registry():
    """Bundled registry.json ← config.json models ← legacy registry.local.json."""
    by_filename = {}
    for entry in read_registry_file(get_registry_path()):
        filename = entry.get("filename")
        if filename:
            by_filename[filename] = entry
    for entry in read_config_models():
        filename = entry.get("filename")
        if filename:
            by_filename[filename] = entry
    # Legacy personal file (pre-combined config); still merged if present.
    for entry in read_registry_file(LOCAL_REGISTRY_PATH):
        filename = entry.get("filename")
        if filename:
            by_filename[filename] = entry
    return list(by_filename.values())


def find_entry(filename):
    for entry in load_registry():
        if entry.get("filename") == filename:
            return entry
    return None


def registry_filenames():
    return {entry.get("filename") for entry in load_registry() if entry.get("filename")}


def append_local_registry(entry):
    """Append/replace a personal model row inside config.json (single sync file)."""
    cfg = load_config()
    models = [m for m in (cfg.get("models") or []) if isinstance(m, dict)]

    # One-time absorb of legacy registry.local.json into config.
    for legacy in read_registry_file(LOCAL_REGISTRY_PATH):
        filename = legacy.get("filename")
        if not filename:
            continue
        if not any(m.get("filename") == filename for m in models):
            models.append(legacy)

    models = [m for m in models if m.get("filename") != entry.get("filename")]
    models.append(entry)
    cfg["models"] = models
    save_config(cfg)
