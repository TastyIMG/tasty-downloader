import asyncio
import json
import os
import platform
import re
import select
import shutil
import stat
import subprocess
import threading
import time
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


def _rclone_conn_value(value: str) -> str:
    text = str(value or "")
    if not any(ch in text for ch in (",", "\\", '"')):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def rclone_s3_uri(r2, object_key: str) -> str:
    """Inline S3 path — no `rclone config create`, no warm/ready state."""
    endpoint = r2.get("endpoint") or f"https://{r2['account_id']}.r2.cloudflarestorage.com"
    bucket = r2["bucket"]
    key = object_key.replace("\\", "/").lstrip("/")
    opts = (
        "provider=Cloudflare,"
        f"access_key_id={_rclone_conn_value(r2['access_key_id'])},"
        f"secret_access_key={_rclone_conn_value(r2['secret_access_key'])},"
        f"endpoint={_rclone_conn_value(endpoint)},"
        "region=auto,"
        "no_check_bucket=true"
    )
    return f":s3,{opts}:{bucket}/{key}"


def rclone_s3_bucket_uri(r2) -> str:
    endpoint = r2.get("endpoint") or f"https://{r2['account_id']}.r2.cloudflarestorage.com"
    bucket = r2["bucket"]
    opts = (
        "provider=Cloudflare,"
        f"access_key_id={_rclone_conn_value(r2['access_key_id'])},"
        f"secret_access_key={_rclone_conn_value(r2['secret_access_key'])},"
        f"endpoint={_rclone_conn_value(endpoint)},"
        "region=auto,"
        "no_check_bucket=true"
    )
    return f":s3,{opts}:{bucket}"


def bootstrap_rclone_binary_async():
    """Install bundled rclone at ComfyUI startup so Download/Push never wait on first click."""

    def _run():
        try:
            ensure_rclone_binary()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="tasty-r2-bootstrap").start()
PROGRESS_EMIT_INTERVAL_SEC = 0.25

PERCENT_RE = re.compile(r",\s*(\d+(?:\.\d+)?)%")
BYTES_RE = re.compile(
    r"(?:Transferred:\s+)?"
    r"(?:[0-9/:\s+\-T.]+\sINFO\s+:\s+)?"
    r"([0-9.]+\s*[KMGT]?i?B)\s*/\s*([0-9.]+\s*[KMGT]?i?B)",
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


def upload_transfer_options(file_size: int, r2) -> tuple[str, int]:
    """Conservative multipart settings for large R2 uploads (avoids size-mismatch failures)."""
    chunk = str(r2.get("chunk_size") or "64M")
    concurrency = int(r2.get("upload_concurrency") or 4)
    if file_size >= 15 * 1024**3:
        return "128M", min(concurrency, 2)
    if file_size >= 5 * 1024**3:
        return chunk, min(concurrency, 4)
    return chunk, concurrency


def delete_r2_object(r2, object_key):
    """Remove a stale/partial object before re-upload (best-effort; ignore missing)."""
    rclone = rclone_bin()
    target = rclone_s3_uri(r2, object_key)
    run_cmd([rclone, "deletefile", target, "--s3-no-check-bucket"], timeout=300)


def rclone_object_size(r2, object_key):
    """Return remote object size in bytes, or 0 if unknown."""
    rclone = rclone_bin()
    target = rclone_s3_uri(r2, object_key)
    result = run_cmd(
        [rclone, "lsjson", target, "--files-only", "--s3-no-check-bucket"],
        timeout=120,
    )
    if result.returncode != 0:
        return 0
    try:
        entries = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return 0
    if isinstance(entries, list) and entries:
        return int(entries[0].get("Size") or 0)
    return 0


def line_buffered_cmd(cmd):
    """Line-buffer rclone stdout on Linux so --stats lines flush during pipe I/O."""
    if platform.system().lower() == "linux" and shutil.which("stdbuf"):
        return ["stdbuf", "-oL", *cmd]
    return cmd


def _read_pty_chunk(master_fd, proc):
    if proc.returncode is not None:
        ready, _, _ = select.select([master_fd], [], [], 0)
        if not ready:
            return None
    ready, _, _ = select.select([master_fd], [], [], 1.0)
    if not ready:
        return b""
    try:
        data = os.read(master_fd, 4096)
    except OSError:
        return None
    if not data:
        return None
    return data


def format_rclone_failure(log_tail):
    messages = []
    for line in log_tail:
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                level = str(payload.get("level") or "").lower()
                msg = (payload.get("msg") or "").strip()
                if msg and level in ("error", "notice") and "failed" in msg.lower():
                    messages.append(msg)
                    continue
                if msg and level == "error":
                    messages.append(msg)
                    continue
        lower = line.lower()
        if any(
            needle in lower
            for needle in ("error", "failed", "denied", "forbidden", "404", "403", "401", "corrupted")
        ):
            messages.append(line.strip())
    if messages:
        return " | ".join(messages[-3:])
    tail = [line.strip() for line in log_tail if line.strip()]
    return " | ".join(tail[-3:] or ["(no rclone output)"])


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


def _rclone_download_urls():
    suffix = _platform_archive_suffix()
    if not suffix:
        raise RuntimeError(
            f"Unsupported platform for bundled rclone: {platform.system()} {platform.machine()}"
        )
    return [
        f"https://downloads.rclone.org/rclone-current-{suffix}.zip",
        f"https://downloads.rclone.org/{RCLONE_VERSION}/rclone-{RCLONE_VERSION}-{suffix}.zip",
    ]


def _download_bundled_rclone(bundled, zip_path):
    last_error = None
    for url in _rclone_download_urls():
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, open(zip_path, "wb") as out:
                shutil.copyfileobj(resp, out)
            _extract_rclone_from_zip(zip_path, bundled)
            _mark_executable(bundled)
            return
        except Exception as exc:
            last_error = exc
            if zip_path.exists():
                zip_path.unlink(missing_ok=True)
    raise RuntimeError(f"Failed to download rclone: {last_error}")


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

    system_path = shutil.which("rclone")
    if system_path:
        return system_path

    if not _rclone_platform_supported():
        raise RuntimeError(
            f"rclone is not available on {platform.system()} {platform.machine()}"
        )

    with _install_lock:
        if bundled.is_file():
            _mark_executable(bundled)
            return str(bundled)

        system_path = shutil.which("rclone")
        if system_path:
            return system_path

        RCLONE_BIN_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = RCLONE_BIN_DIR / "rclone-download.zip"
        try:
            _download_bundled_rclone(bundled, zip_path)
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


def rclone_binary_ready():
    bundled = _bundled_rclone_path()
    return bundled.is_file() or bool(shutil.which("rclone"))


def rclone_bin_or_install():
    """Return rclone path; install bundled copy only if missing (startup usually did this already)."""
    bundled = _bundled_rclone_path()
    if bundled.is_file():
        if os.access(bundled, os.X_OK):
            return str(bundled)
        _mark_executable(bundled)
        return str(bundled)
    system_path = shutil.which("rclone")
    if system_path:
        return system_path
    return ensure_rclone_binary()


def rclone_bin():
    return rclone_bin_or_install()


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
            msg = payload.get("msg") or ""
            if msg and (" / " in msg or "%" in msg):
                for part in re.split(r"[\r\n]+", msg):
                    part = part.strip()
                    if " / " not in part:
                        continue
                    nested = parse_rclone_progress(part, fallback_total)
                    if nested[0] is not None or nested[2] is not None:
                        return nested

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


def build_rclone_s3_copyto_cmd(r2, src, dest, *, upload=False, file_size=0):
    rclone = rclone_bin()
    chunk_size = str(r2.get("chunk_size") or "64M")
    upload_concurrency = int(r2.get("upload_concurrency") or 4)
    if upload and file_size > 0:
        chunk_size, upload_concurrency = upload_transfer_options(file_size, r2)

    cmd = [
        rclone,
        "copyto",
        src,
        dest,
        "-v",
        "--use-json-log",
        "--s3-no-check-bucket",
        "--s3-chunk-size",
        chunk_size,
        "--stats",
        "1s",
        "--stats-one-line",
        "--timeout",
        "0",
    ]
    if upload:
        cmd.extend(
            [
                "--s3-upload-concurrency",
                str(upload_concurrency),
                "--retries",
                "3",
                "--low-level-retries",
                "10",
            ]
        )
    return cmd


async def run_rclone_with_progress(cmd, fallback_total, request, send_event):
    cmd = line_buffered_cmd(list(cmd))
    use_pty = platform.system().lower() != "windows" and hasattr(os, "openpty")

    master_fd = None
    proc = None
    buffer = ""
    last_downloaded = 0
    last_emit_at = 0.0
    log_tail = []

    async def emit_progress(downloaded, total, percent, *, force=False):
        nonlocal last_emit_at
        now = time.monotonic()
        if not force and (now - last_emit_at) < PROGRESS_EMIT_INTERVAL_SEC:
            return
        last_emit_at = now
        await send_event(
            {
                "type": "progress",
                "downloaded": downloaded,
                "total": total,
                "percent": percent,
            }
        )

    async def handle_lines(lines):
        nonlocal last_downloaded
        for text in lines:
            log_tail.append(text)
            if len(log_tail) > 40:
                del log_tail[:-40]
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
            elif percent is None and downloaded:
                percent = None
            await emit_progress(downloaded, total or 0, percent, force=downloaded > 0)

    async def drain_buffer(*, final=False):
        nonlocal buffer
        lines, buffer = split_rclone_output(buffer)
        if lines:
            await handle_lines(lines)
        if final and buffer.strip():
            await handle_lines([buffer.strip()])
            buffer = ""

    try:
        await emit_progress(0, fallback_total or 0, 0 if fallback_total else None, force=True)

        if use_pty:
            master_fd, slave_fd = os.openpty()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
            )
            os.close(slave_fd)
            loop = asyncio.get_running_loop()
            while True:
                if await client_disconnected(request):
                    raise asyncio.CancelledError("Transfer cancelled")
                chunk = await loop.run_in_executor(None, _read_pty_chunk, master_fd, proc)
                if chunk is None:
                    break
                if chunk:
                    buffer += chunk.decode("utf-8", errors="replace")
                    await drain_buffer()
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            while True:
                if await client_disconnected(request):
                    raise asyncio.CancelledError("Transfer cancelled")
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                await drain_buffer()

        await drain_buffer(final=True)

        code = await proc.wait()
        if code != 0:
            detail = format_rclone_failure(log_tail)
            hint = ""
            if "sizes differ" in detail.lower():
                hint = (
                    " A partial/corrupt copy may remain on R2 — retry Push "
                    "(the node deletes the old object first). For 15GB+ files, "
                    "use upload_concurrency 2 in Settings if it fails again."
                )
            raise RuntimeError(f"rclone exited with code {code}: {detail}{hint}")

        if fallback_total and last_downloaded < fallback_total:
            await emit_progress(fallback_total, fallback_total, 100, force=True)

        return last_downloaded or fallback_total or 0
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await proc.wait()
            except Exception:
                pass
        raise
    finally:
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
