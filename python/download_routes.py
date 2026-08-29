import asyncio
import json
import os

import aiohttp
import folder_paths
from aiohttp import web
from server import PromptServer

from .config_store import load_config
from .paths import CHUNK_SIZE, LOCAL_REGISTRY_PATH
from .registry import find_entry, get_registry_path, load_registry
from .streaming import client_disconnected, prepare_ndjson

routes = PromptServer.instance.routes


def write_chunk(path, data, mode="ab"):
    with open(path, mode) as out:
        out.write(data)


def registry_available():
    if get_registry_path().exists() or LOCAL_REGISTRY_PATH.exists():
        return True
    models = load_config().get("models") or []
    return isinstance(models, list) and len(models) > 0


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

    response, send_event = await prepare_ndjson(request)

    try:
        if os.path.exists(temp_file):
            os.remove(temp_file)

        await send_event({"type": "progress", "downloaded": 0, "total": 0, "percent": 0})

        timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    await send_event({"type": "error", "error": f"HTTP {resp.status}"})
                    await response.write_eof()
                    return response

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

                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    if await client_disconnected(request):
                        raise asyncio.CancelledError("Download cancelled")
                    if not chunk:
                        continue
                    await asyncio.to_thread(write_chunk, temp_file, chunk, "ab")
                    downloaded += len(chunk)
                    percent = round(downloaded * 100 / total) if total else None
                    await send_event(
                        {
                            "type": "progress",
                            "downloaded": downloaded,
                            "total": total,
                            "percent": percent,
                        }
                    )

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
