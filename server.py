import json
import os
from pathlib import Path

import aiohttp
import folder_paths
from aiohttp import web
from server import PromptServer

routes = PromptServer.instance.routes
EXTENSION_DIR = Path(__file__).parent
DEFAULT_REGISTRY_PATH = EXTENSION_DIR / "registry.json"
LOCAL_REGISTRY_PATH = EXTENSION_DIR / "registry.local.json"
CONFIG_PATH = EXTENSION_DIR / "config.json"


def _get_registry_path():
    env_path = os.environ.get("TASTY_R2_REGISTRY_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        custom_path = (cfg.get("registry_path") or "").strip()
        if custom_path:
            return Path(custom_path).expanduser()

    return DEFAULT_REGISTRY_PATH


def _parse_registry(data):
    if isinstance(data, dict) and "models" in data:
        return data["models"]
    if isinstance(data, list):
        return data
    return []


def _read_registry_file(path):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return _parse_registry(json.load(f))


def _load_registry():
    entries = _read_registry_file(_get_registry_path())
    local_entries = _read_registry_file(LOCAL_REGISTRY_PATH)
    if not local_entries:
        return entries

    by_filename = {entry.get("filename"): entry for entry in entries if entry.get("filename")}
    for entry in local_entries:
        filename = entry.get("filename")
        if filename:
            by_filename[filename] = entry
    return list(by_filename.values())


def _find_entry(filename):
    for entry in _load_registry():
        if entry.get("filename") == filename:
            return entry
    return None


@routes.get("/tasty-r2/list")
async def list_models(_request):
    registry_path = _get_registry_path()
    if not registry_path.exists() and not LOCAL_REGISTRY_PATH.exists():
        return web.json_response(
            {"error": f"Registry not found: {registry_path}"},
            status=404,
        )

    result = []
    for entry in _load_registry():
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

    registry_path = _get_registry_path()
    if not registry_path.exists() and not LOCAL_REGISTRY_PATH.exists():
        return web.json_response(
            {"error": f"Registry not found: {registry_path}"},
            status=404,
        )

    entry = _find_entry(filename)
    if not entry:
        return web.json_response({"error": f"Not in registry: {filename}"}, status=404)

    save_path = entry.get("save_path")
    url = entry.get("url")
    if not save_path or not url:
        return web.json_response({"error": "Registry entry missing save_path or url"}, status=400)

    dest_dir = os.path.join(folder_paths.models_dir, save_path)
    os.makedirs(dest_dir, exist_ok=True)
    dest_file = os.path.join(dest_dir, filename)

    response = web.StreamResponse(
        status=200,
        headers={"Content-Type": "application/x-ndjson", "Cache-Control": "no-cache"},
    )
    await response.prepare(request)

    async def send_event(payload):
        await response.write((json.dumps(payload) + "\n").encode("utf-8"))

    try:
        timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    await send_event({"type": "error", "error": f"HTTP {resp.status}"})
                    await response.write_eof()
                    return response

                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                with open(dest_file, "wb") as out:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        out.write(chunk)
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
    except Exception as exc:
        if os.path.exists(dest_file):
            os.remove(dest_file)
        await send_event({"type": "error", "error": str(exc)})
        await response.write_eof()
        return response

    if hasattr(folder_paths, "filename_list_cache"):
        folder_paths.filename_list_cache.clear()

    await send_event({"type": "done", "path": dest_file})
    await response.write_eof()
    return response
