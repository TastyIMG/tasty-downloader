import asyncio
import json
import os
from pathlib import Path

import folder_paths
from aiohttp import web
from server import PromptServer

from .config_store import get_r2_config, r2_is_configured
from .rclone_ops import (
    build_rclone_s3_copyto_cmd,
    delete_r2_object,
    ensure_rclone_remote,
    rclone_available,
    registry_key,
    registered_keys,
    run_rclone_with_progress,
    scan_push_candidates,
)
from .registry import append_local_registry
from .streaming import prepare_ndjson

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

    try:
        await send_event(
            {"type": "progress", "downloaded": 0, "total": file_size, "percent": 0}
        )

        remote = await asyncio.to_thread(ensure_rclone_remote, r2)
        object_key = f"models/{save_path}/{filename}"
        dest = f"{remote}:{r2['bucket']}/{object_key}"
        await asyncio.to_thread(delete_r2_object, r2, remote, object_key)
        cmd = build_rclone_s3_copyto_cmd(
            r2, src, dest, upload=True, file_size=file_size
        )
        await run_rclone_with_progress(cmd, file_size, request, send_event)

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
                    "error": f"Uploaded to R2 but failed to append config.json models: {exc}",
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
        try:
            await response.write_eof()
        except Exception:
            pass
        return response
    except Exception as exc:
        try:
            await send_event({"type": "error", "error": str(exc)})
            await response.write_eof()
        except Exception:
            pass
        return response
