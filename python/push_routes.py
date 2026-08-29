import asyncio
import json
import os
from pathlib import Path

import folder_paths
from aiohttp import web
from server import PromptServer

from .config_store import get_r2_config, r2_is_configured
from .rclone_ops import (
    ensure_rclone_remote,
    parse_rclone_progress,
    rclone_available,
    rclone_bin,
    registry_key,
    registered_keys,
    scan_push_candidates,
    split_rclone_output,
)
from .registry import append_local_registry
from .streaming import client_disconnected, prepare_ndjson

routes = PromptServer.instance.routes


def _safe_save_path(save_path: str) -> bool:
    if not save_path or save_path.startswith("/") or "\\" in save_path:
        return False
    parts = Path(save_path).parts
    return bool(parts) and ".." not in parts


@routes.get("/tasty-r2/push-list")
async def push_list(_request):
    r2 = get_r2_config()
    return web.json_response(
        {
            "configured": r2_is_configured(r2),
            "rclone_available": rclone_available(),
            "items": scan_push_candidates(),
        }
    )


@routes.post("/tasty-r2/push")
async def push_model(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    filename = body.get("filename")
    save_path = (body.get("save_path") or "").replace("\\", "/").strip("/")
    if not filename or not save_path:
        return web.json_response({"error": "filename and save_path required"}, status=400)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        return web.json_response({"error": "Invalid filename"}, status=400)
    if not _safe_save_path(save_path):
        return web.json_response({"error": "Invalid save_path"}, status=400)

    key = registry_key(save_path, filename)
    registered = registered_keys()
    if key in registered:
        return web.json_response({"error": "Already registered"}, status=400)

    r2 = get_r2_config()
    if not r2_is_configured(r2):
        return web.json_response(
            {"error": "R2 not configured. Open Settings and save credentials."},
            status=400,
        )

    src = os.path.join(folder_paths.models_dir, *save_path.split("/"), filename)
    if not os.path.isfile(src):
        return web.json_response({"error": f"File not found: {src}"}, status=404)

    file_size = os.path.getsize(src)
    public_base = r2["public_base_url"].rstrip("/")
    public_url = f"{public_base}/models/{save_path}/{filename}"

    response, send_event = await prepare_ndjson(request)
    proc = None

    try:
        # Same idea as download: emit real progress events as bytes move.
        await send_event(
            {"type": "progress", "downloaded": 0, "total": file_size, "percent": 0}
        )

        remote = await asyncio.to_thread(ensure_rclone_remote, r2)
        await send_event(
            {"type": "progress", "downloaded": 0, "total": file_size, "percent": 0}
        )

        rclone = rclone_bin()
        dest = f"{remote}:{r2['bucket']}/models/{save_path}/{filename}"
        # Match the working manual flags; avoid --use-json-log (hides/breaks useful errors).
        cmd = [
            rclone,
            "copyto",
            src,
            dest,
            "-v",
            "--s3-no-check-bucket",
            "--s3-chunk-size",
            str(r2.get("chunk_size") or "64M"),
            "--s3-upload-concurrency",
            str(r2.get("upload_concurrency") or 8),
            "--progress",
            "--stats",
            "1s",
            "--stats-one-line",
        ]

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

        while True:
            if await client_disconnected(request):
                proc.terminate()
                raise asyncio.CancelledError("Push cancelled")

            chunk = await proc.stdout.read(4096)
            if not chunk:
                break

            buffer += chunk.decode("utf-8", errors="replace")
            lines, buffer = split_rclone_output(buffer)

            for text in lines:
                remember(text)
                downloaded, total, percent = parse_rclone_progress(text, file_size)
                # Never clobber UI with empty parses (was stuck at 0 B).
                if downloaded is None and percent is None:
                    continue
                if downloaded is None:
                    downloaded = last_downloaded
                else:
                    last_downloaded = downloaded
                total = total if total else file_size
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
            downloaded, total, percent = parse_rclone_progress(buffer, file_size)
            if downloaded is not None or percent is not None:
                if downloaded is None:
                    downloaded = last_downloaded
                total = total if total else file_size
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
            # Prefer error-looking lines; fall back to last output.
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

        entry = {
            "for_model": Path(filename).stem,
            "filename": filename,
            "save_path": save_path,
            "url": public_url,
        }
        try:
            await asyncio.to_thread(append_local_registry, entry)
        except Exception as exc:
            await send_event(
                {
                    "type": "error",
                    "error": f"Uploaded to R2 but failed to append registry.local.json: {exc}",
                }
            )
            await response.write_eof()
            return response

        await send_event(
            {
                "type": "progress",
                "downloaded": file_size,
                "total": file_size,
                "percent": 100,
            }
        )
        await send_event({"type": "done", "path": dest, "url": public_url, "entry": entry})
        await response.write_eof()
        return response
    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await proc.wait()
            except Exception:
                pass
        try:
            await response.write_eof()
        except Exception:
            pass
        return response
    except Exception as exc:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await proc.wait()
            except Exception:
                pass
        try:
            await send_event({"type": "error", "error": str(exc)})
            await response.write_eof()
        except Exception:
            pass
        return response
