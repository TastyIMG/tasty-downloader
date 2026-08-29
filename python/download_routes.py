import asyncio
import json
import os
import time

import aiohttp
import folder_paths
from aiohttp import web
from server import PromptServer

from .config_store import get_r2_config, load_config
from .paths import LOCAL_REGISTRY_PATH
from .rclone_ops import (
    build_rclone_s3_copyto_cmd,
    ensure_rclone_remote,
    prefer_rclone_download,
    r2_model_object_key,
    run_rclone_with_progress,
)
from .registry import find_entry, get_registry_path, load_registry
from .streaming import client_disconnected, prepare_ndjson

routes = PromptServer.instance.routes

HTTP_READ_CHUNK = 4 * 1024 * 1024
PROGRESS_INTERVAL_SEC = 0.25


def registry_available():
    if get_registry_path().exists() or LOCAL_REGISTRY_PATH.exists():
        return True
    models = load_config().get("models") or []
    return isinstance(models, list) and len(models) > 0


async def download_via_http(url, temp_file, request, send_event):
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    last_progress_at = 0.0

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status}")

            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            await send_event(
                {
                    "type": "progress",
                    "downloaded": 0,
                    "total": total,
                    "percent": 0 if total else None,
                }
            )

            with open(temp_file, "wb") as out:
                async for chunk in resp.content.iter_chunked(HTTP_READ_CHUNK):
                    if await client_disconnected(request):
                        raise asyncio.CancelledError("Download cancelled")
                    if not chunk:
                        continue
                    await asyncio.to_thread(out.write, chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_progress_at >= PROGRESS_INTERVAL_SEC or (
                        total and downloaded >= total
                    ):
                        percent = round(downloaded * 100 / total) if total else None
                        await send_event(
                            {
                                "type": "progress",
                                "downloaded": downloaded,
                                "total": total,
                                "percent": percent,
                            }
                        )
                        last_progress_at = now


@routes.get("/tasty-r2/list")
async def list_models(_request):
    if not registry_available():
        return web.json_response(
            {"error": "No models in config.json or registry.json"},
            status=404,
        )

    result = []
    for entry in load_registry():
        filename = entry.get("filename")
        save_path = entry.get("save_path")
        url = entry.get("url")
        if not filename or not save_path or not url:
            continue
        result.append(
            {
                "filename": filename,
                "save_path": save_path,
                "url": url,
                "for_model": entry.get("for_model", ""),
                "exists": os.path.isfile(
                    os.path.join(folder_paths.models_dir, save_path, filename)
                ),
            }
        )
    return web.json_response(result)


@routes.post("/tasty-r2/download")
async def download_model(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    filename = body.get("filename")
    if not filename:
        return web.json_response({"error": "filename required"}, status=400)

    if not registry_available():
        return web.json_response(
            {"error": "No models in config.json or registry.json"},
            status=404,
        )

    entry = find_entry(filename)
    if not entry:
        return web.json_response({"error": f"Not in registry: {filename}"}, status=404)

    save_path = entry.get("save_path")
    url = entry.get("url")
    if not save_path or not url:
        return web.json_response({"error": "Registry entry missing save_path or url"}, status=400)

    dest_dir = os.path.join(folder_paths.models_dir, save_path)
    os.makedirs(dest_dir, exist_ok=True)
    dest_file = os.path.join(dest_dir, filename)
    temp_file = dest_file + ".partial"

    r2 = get_r2_config()
    use_rclone = prefer_rclone_download(r2, url)

    response, send_event = await prepare_ndjson(request)

    try:
        if os.path.exists(temp_file):
            os.remove(temp_file)

        await send_event({"type": "progress", "downloaded": 0, "total": 0, "percent": 0})

        if use_rclone:
            remote = await asyncio.to_thread(ensure_rclone_remote, r2)
            src = f"{remote}:{r2['bucket']}/{r2_model_object_key(save_path, filename)}"
            cmd = build_rclone_s3_copyto_cmd(r2, src, temp_file, upload=False)
            await run_rclone_with_progress(cmd, 0, request, send_event)
        else:
            await download_via_http(url, temp_file, request, send_event)

        await asyncio.to_thread(os.replace, temp_file, dest_file)
    except asyncio.CancelledError:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        try:
            await response.write_eof()
        except Exception:
            pass
        return response
    except Exception as exc:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        try:
            await send_event({"type": "error", "error": str(exc)})
            await response.write_eof()
        except Exception:
            pass
        return response

    if hasattr(folder_paths, "filename_list_cache"):
        folder_paths.filename_list_cache.clear()

    await send_event({"type": "done", "path": dest_file})
    await response.write_eof()
    return response
