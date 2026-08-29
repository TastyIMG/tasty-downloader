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
    scan_push_candidates,
)
from .registry import append_local_registry, registry_filenames
from .streaming import client_disconnected, prepare_ndjson

routes = PromptServer.instance.routes


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
    save_path = body.get("save_path")
    if not filename or not save_path:
        return web.json_response({"error": "filename and save_path required"}, status=400)
    if "/" in filename or "\\" in filename or filename.startswith("."):
        return web.json_response({"error": "Invalid filename"}, status=400)
    if ".." in save_path or save_path.startswith("/") or "\\" in save_path:
        return web.json_response({"error": "Invalid save_path"}, status=400)

    if filename in registry_filenames():
        return web.json_response({"error": "Already registered"}, status=400)

    r2 = get_r2_config()
    if not r2_is_configured(r2):
        return web.json_response(
            {"error": "R2 not configured. Open Settings and save credentials."},
            status=400,
        )

    src = os.path.join(folder_paths.models_dir, save_path, filename)
    if not os.path.isfile(src):
        return web.json_response({"error": f"File not found: {src}"}, status=404)

    file_size = os.path.getsize(src)
    public_base = r2["public_base_url"].rstrip("/")
    public_url = f"{public_base}/models/{save_path}/{filename}"

    response, send_event = await prepare_ndjson(request)
    proc = None

    try:
        await send_event(
            {"type": "progress", "downloaded": 0, "total": file_size, "percent": 0}
        )
        remote = await asyncio.to_thread(ensure_rclone_remote, r2)
        rclone = rclone_bin()
        dest = f"{remote}:{r2['bucket']}/models/{save_path}/{filename}"
        cmd = [
            rclone,
            "copyto",
            src,
            dest,
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
        while True:
            if await client_disconnected(request):
                proc.terminate()
                raise asyncio.CancelledError("Push cancelled")

            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            downloaded, total, percent = parse_rclone_progress(text, file_size)
            await send_event(
                {
                    "type": "progress",
                    "downloaded": downloaded if downloaded is not None else 0,
                    "total": total if total is not None else file_size,
                    "percent": percent,
                }
            )

        code = await proc.wait()
        if code != 0:
            raise RuntimeError(f"rclone exited with code {code}")

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
