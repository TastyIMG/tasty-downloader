import asyncio
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import threading
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import folder_paths

from .config_store import get_r2_config
from .paths import DEFAULT_PUSH_FOLDERS, EXTENSION_DIR
from .streaming import client_disconnected

RCLONE_BIN_DIR = EXTENSION_DIR / "bin"
RCLONE_VERSION = "v1.68.2"
_install_lock = threading.Lock()

PERCENT_RE = re.compile(r",\s*(\d+(?:\.\d+)?)%")
BYTES_RE = re.compile(
    r"Transferred:\s+([0-9.]+\s*[KMGT]?i?B)\s*/\s*([0-9.]+\s*[KMGT]?i?B)",
    re.IGNORECASE,
)
# rclone --use-json-log stats lines
JSON_LINE_RE = re.compile(r"\{.*\}")


def run_cmd(cmd, timeout=60):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _rclone_exe_name():
    return "rclone.exe" if platform.system() == "Windows" else "rclone"


def _bundled_rclone_path():
    return RCLONE_BIN_DIR / _rclone_exe_name()


def _platform_archive_suffix():
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine in ("i386", "i686", "x86"):
        arch = "386"
    else:
        return None
    if system == "linux":
        return f"linux-{arch}"
    if system == "darwin":
        return f"osx-{arch}"
    if system == "windows":
        return f"windows-{arch}"
    return None


def _rclone_platform_supported():
    return _platform_archive_suffix() is not None


def _rclone_download_url():
    suffix = _platform_archive_suffix()
    if not suffix:
        raise RuntimeError(
            f"Unsupported platform for bundled rclone: {platform.system()} {platform.machine()}"
        )
    return f"https://downloads.rclone.org/rclone-{RCLONE_VERSION}-{suffix}.zip"


def _mark_executable(path):
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _extract_rclone_from_zip(zip_path, dest_path):
    with zipfile.ZipFile(zip_path) as archive:
        member = None
        target_name = _rclone_exe_name()
        for name in archive.namelist():
            base = name.rsplit("/", 1)[-1]
            if base == target_name or base == "rclone":
                member = name
                break
        if not member:
            raise RuntimeError("rclone binary not found inside downloaded archive")
        with archive.open(member) as src, open(dest_path, "wb") as out:
            shutil.copyfileobj(src, out)


def ensure_rclone_binary():
    bundled = _bundled_rclone_path()
    if bundled.is_file():
        if os.access(bundled, os.X_OK):
            return str(bundled)
        _mark_executable(bundled)
        return str(bundled)

    if not _rclone_platform_supported():
        system_path = shutil.which("rclone")
        if system_path:
            return system_path
        raise RuntimeError(
            f"rclone is not available on {platform.system()} {platform.machine()}"
        )

    with _install_lock:
        if bundled.is_file():
            _mark_executable(bundled)
            return str(bundled)

        RCLONE_BIN_DIR.mkdir(parents=True, exist_ok=True)
        url = _rclone_download_url()
        zip_path = RCLONE_BIN_DIR / "rclone-download.zip"
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, open(zip_path, "wb") as out:
                shutil.copyfileobj(resp, out)
            _extract_rclone_from_zip(zip_path, bundled)
            _mark_executable(bundled)
        finally:
            if zip_path.exists():
                zip_path.unlink(missing_ok=True)

        if not bundled.is_file():
            raise RuntimeError("Failed to install bundled rclone")
        return str(bundled)


def rclone_available():
    if _bundled_rclone_path().is_file():
        return True
    if shutil.which("rclone"):
        return True
    return _rclone_platform_supported()


def rclone_bin():
    return ensure_rclone_binary()


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


def registry_key(save_path, filename):
    return f"{save_path.strip('/')}/{filename}"


def registered_keys():
    from .registry import load_registry

    keys = set()
    for entry in load_registry():
        sp = (entry.get("save_path") or "").strip("/")
        fn = entry.get("filename") or ""
        if sp and fn:
            keys.add(registry_key(sp, fn))
            keys.add(fn)
    return keys


def scan_push_candidates():
    registered = registered_keys()
    r2 = get_r2_config()
    folders = r2.get("push_folders") or DEFAULT_PUSH_FOLDERS
    models_dir = os.path.abspath(folder_paths.models_dir)
    results = []
    seen = set()

    for top in folders:
        folder = os.path.join(models_dir, top)
        if not os.path.isdir(folder):
            continue
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in files:
                if name.startswith(".") or name.endswith(".partial"):
                    continue
                full = os.path.join(root, name)
                if not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, models_dir).replace("\\", "/")
                save_path = str(Path(rel).parent).replace("\\", "/")
                if save_path == ".":
                    continue
                filename = Path(rel).name
                key = registry_key(save_path, filename)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                results.append(
                    {
                        "filename": filename,
                        "save_path": save_path,
                        "for_model": Path(filename).stem,
                        "size": size,
                        "registered": key in registered or filename in registered,
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
    """Return (downloaded, total, percent) or (None, None, None) if unparseable."""
    line = line.strip()
    if not line:
        return None, None, None

    # JSON log stats (preferred when --use-json-log is set)
    if line.startswith("{"):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            stats = payload.get("stats") or payload
            if isinstance(stats, dict) and (
                "bytes" in stats or "totalBytes" in stats or "transfers" in stats
            ):
                downloaded = int(stats.get("bytes") or 0)
                total = int(stats.get("totalBytes") or fallback_total or 0)
                percent = round(downloaded * 100 / total) if total else None
                return downloaded, total or None, percent

    percent = None
    downloaded = None
    total = fallback_total or None

    pct = PERCENT_RE.search(line)
    if pct:
        percent = round(float(pct.group(1)))

    sizes = BYTES_RE.search(line)
    if sizes:
        downloaded = parse_size_to_bytes(sizes.group(1))
        parsed_total = parse_size_to_bytes(sizes.group(2))
        if parsed_total:
            total = parsed_total
        if percent is None and downloaded is not None and total:
            percent = round(downloaded * 100 / total)

    if downloaded is None and percent is None:
        return None, None, None
    return downloaded, total, percent


def split_rclone_output(buffer: str):
    """rclone --progress uses CR updates; split on CR and LF."""
    parts = re.split(r"[\r\n]+", buffer)
    incomplete = parts.pop() if parts else ""
    lines = [p.strip() for p in parts if p.strip()]
    return lines, incomplete


def r2_model_object_key(save_path: str, filename: str) -> str:
    sp = save_path.replace("\\", "/").strip("/")
    return f"models/{sp}/{filename}"


def prefer_rclone_download(r2, url: str) -> bool:
    """Use bucket S3 API when creds exist; keep plain HTTP for obvious external hosts."""
    if not rclone_available():
        return False
    if not r2.get("access_key_id") or not r2.get("secret_access_key") or not r2.get("bucket"):
        return False
    if not (r2.get("endpoint") or r2.get("account_id")):
        return False
    if not url:
        return True

    host = urlparse(url).netloc.lower()
    if "huggingface.co" in host or host == "hf.co" or host.endswith(".hf.co"):
        return False
    if "civitai.com" in host:
        return False

    base = (r2.get("public_base_url") or "").rstrip("/")
    if base and url.startswith(f"{base}/"):
        return True
    if "r2.dev" in host or "cloudflarestorage.com" in host:
        return True
    return False


def build_rclone_s3_copyto_cmd(r2, src, dest, *, upload=False):
    rclone = rclone_bin()
    cmd = [
        rclone,
        "copyto",
        src,
        dest,
        "-v",
        "--s3-no-check-bucket",
        "--s3-chunk-size",
        str(r2.get("chunk_size") or "64M"),
        "--progress",
        "--stats",
        "1s",
        "--stats-one-line",
    ]
    if upload:
        cmd.extend(
            [
                "--s3-upload-concurrency",
                str(r2.get("upload_concurrency") or 8),
            ]
        )
    return cmd


async def run_rclone_with_progress(cmd, fallback_total, request, send_event):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    assert proc.stdout is not None
    buffer = ""
    last_downloaded = 0
    log_tail = []

    def remember(line: str):
        log_tail.append(line)
        if len(log_tail) > 40:
            del log_tail[:-40]

    try:
        while True:
            if await client_disconnected(request):
                raise asyncio.CancelledError("Transfer cancelled")

            chunk = await proc.stdout.read(4096)
            if not chunk:
                break

            buffer += chunk.decode("utf-8", errors="replace")
            lines, buffer = split_rclone_output(buffer)

            for text in lines:
                remember(text)
                downloaded, total, percent = parse_rclone_progress(text, fallback_total)
                if downloaded is None and percent is None:
                    continue
                if downloaded is None:
                    downloaded = last_downloaded
                else:
                    last_downloaded = downloaded
                total = total if total else fallback_total
                if percent is None and total:
                    percent = round(downloaded * 100 / total)
                await send_event(
                    {
                        "type": "progress",
                        "downloaded": downloaded,
                        "total": total,
                        "percent": percent,
                    }
                )

        if buffer.strip():
            remember(buffer.strip())
            downloaded, total, percent = parse_rclone_progress(buffer, fallback_total)
            if downloaded is not None or percent is not None:
                if downloaded is None:
                    downloaded = last_downloaded
                total = total if total else fallback_total
                if percent is None and total:
                    percent = round(downloaded * 100 / total)
                await send_event(
                    {
                        "type": "progress",
                        "downloaded": downloaded,
                        "total": total,
                        "percent": percent,
                    }
                )

        code = await proc.wait()
        if code != 0:
            interesting = [
                line
                for line in log_tail
                if any(
                    needle in line.lower()
                    for needle in ("error", "failed", "denied", "forbidden", "404", "403", "401")
                )
            ]
            detail = " | ".join(interesting[-5:] or log_tail[-5:] or ["(no rclone output)"])
            raise RuntimeError(f"rclone exited with code {code}: {detail}")

        return last_downloaded or fallback_total or 0
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.terminate()
            try:
                await proc.wait()
            except Exception:
                pass
        raise
