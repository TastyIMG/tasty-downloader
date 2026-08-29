import json
import os
from pathlib import Path

from .config_store import load_config
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


def load_registry():
    entries = read_registry_file(get_registry_path())
    local_entries = read_registry_file(LOCAL_REGISTRY_PATH)
    if not local_entries:
        return entries

    by_filename = {entry.get("filename"): entry for entry in entries if entry.get("filename")}
    for entry in local_entries:
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
    if LOCAL_REGISTRY_PATH.exists():
        with open(LOCAL_REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = []

    if isinstance(data, dict) and "models" in data:
        models = [m for m in data["models"] if m.get("filename") != entry["filename"]]
        models.append(entry)
        data["models"] = models
    elif isinstance(data, list):
        data = [m for m in data if m.get("filename") != entry["filename"]]
        data.append(entry)
    else:
        data = [entry]

    with open(LOCAL_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
