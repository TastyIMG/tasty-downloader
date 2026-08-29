import os
import re
import shutil
import subprocess
from pathlib import Path

import folder_paths

from .config_store import get_r2_config
from .paths import DEFAULT_PUSH_FOLDERS
from .registry import registry_filenames

PERCENT_RE = re.compile(r",\s*(\d+(?:\.\d+)?)%")
BYTES_RE = re.compile(
    r"Transferred:\s+([0-9.]+\s*[KMGT]?i?B)\s*/\s*([0-9.]+\s*[KMGT]?i?B)",
    re.IGNORECASE,
)


def run_cmd(cmd, timeout=60):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def rclone_bin():
    path = shutil.which("rclone")
    if not path:
        raise RuntimeError(
            "rclone not found on PATH. Install with: curl -fsSL https://rclone.org/install.sh | sudo bash"
        )
    return path


def rclone_available():
    return bool(shutil.which("rclone"))


def ensure_rclone_remote(r2):
    rclone = rclone_bin()
    remote = r2.get("remote_name") or "tasty-r2"
    endpoint = r2.get("endpoint") or f"https://{r2['account_id']}.r2.cloudflarestorage.com"
    cmd = [
        rclone,
        "config",
        "create",
        remote,
        "s3",
        "provider",
        "Cloudflare",
        "access_key_id",
        r2["access_key_id"],
        "secret_access_key",
        r2["secret_access_key"],
        "endpoint",
        endpoint,
        "region",
        "auto",
        "no_check_bucket",
        "true",
    ]
    result = run_cmd(cmd)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "rclone config failed").strip()
        raise RuntimeError(err)
    return remote


def scan_push_candidates():
    registered = registry_filenames()
    r2 = get_r2_config()
    folders = r2.get("push_folders") or DEFAULT_PUSH_FOLDERS
    models_dir = folder_paths.models_dir
    results = []

    for save_path in folders:
        folder = os.path.join(models_dir, save_path)
        if not os.path.isdir(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if name.startswith(".") or name.endswith(".partial"):
                continue
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            if name in registered:
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            results.append(
                {
                    "filename": name,
                    "save_path": save_path,
                    "for_model": Path(name).stem,
                    "size": size,
                }
            )

    results.sort(key=lambda item: (item["save_path"], item["filename"].lower()))
    return results


def parse_size_to_bytes(text):
    text = text.strip().replace(",", "")
    match = re.match(r"^([0-9.]+)\s*([KMGT]?i?B)$", text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    key = match.group(2).upper()
    if key.endswith("IB") or key == "B":
        mult_map = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4}
        return int(value * mult_map.get(key, 1))
    mult = {"KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    return int(value * mult.get(key, 1))


def parse_rclone_progress(line, fallback_total=0):
    percent = None
    downloaded = None
    total = fallback_total or None

    pct = PERCENT_RE.search(line)
    if pct:
        percent = round(float(pct.group(1)))

    sizes = BYTES_RE.search(line)
    if sizes:
        downloaded = parse_size_to_bytes(sizes.group(1))
        total = parse_size_to_bytes(sizes.group(2)) or total
        if percent is None and downloaded is not None and total:
            percent = round(downloaded * 100 / total)

    return downloaded, total, percent
