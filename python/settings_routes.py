import asyncio
import json

from aiohttp import web
from server import PromptServer

from .config_store import get_r2_config, load_config, r2_is_configured, save_config
from .paths import DEFAULT_PUSH_FOLDERS, DEFAULT_R2, SECRET_PLACEHOLDER
from .rclone_ops import ensure_rclone_remote, rclone_available, rclone_bin, run_cmd

routes = PromptServer.instance.routes


@routes.get("/tasty-r2/settings")
async def get_settings(_request):
    cfg = load_config()
    r2 = get_r2_config()
    return web.json_response(
        {
            "registry_path": cfg.get("registry_path") or "",
            "account_id": r2.get("account_id") or "",
            "access_key_configured": bool(r2.get("access_key_id")),
            "secret_configured": bool(r2.get("secret_access_key")),
            "bucket": r2.get("bucket") or "",
            "endpoint": r2.get("endpoint") or "",
            "public_base_url": r2.get("public_base_url") or "",
            "registry_url": r2.get("registry_url") or "",
            "remote_name": r2.get("remote_name") or "tasty-r2",
            "chunk_size": r2.get("chunk_size") or "64M",
            "upload_concurrency": r2.get("upload_concurrency") or 8,
            "push_folders": r2.get("push_folders") or list(DEFAULT_PUSH_FOLDERS),
            "rclone_available": rclone_available(),
            "configured": r2_is_configured(r2),
        }
    )


@routes.post("/tasty-r2/settings")
async def save_settings(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    cfg = load_config()
    r2 = {**cfg.get("r2", {})}

    if "registry_path" in body:
        cfg["registry_path"] = (body.get("registry_path") or "").strip()

    for key in (
        "account_id",
        "bucket",
        "endpoint",
        "public_base_url",
        "registry_url",
        "remote_name",
        "chunk_size",
    ):
        if key in body:
            r2[key] = (body.get(key) or "").strip()

    if "upload_concurrency" in body:
        try:
            r2["upload_concurrency"] = int(body.get("upload_concurrency") or 8)
        except (TypeError, ValueError):
            r2["upload_concurrency"] = 8

    if "push_folders" in body:
        folders = body.get("push_folders")
        if isinstance(folders, str):
            folders = [part.strip() for part in folders.split(",") if part.strip()]
        if isinstance(folders, list) and folders:
            r2["push_folders"] = folders

    access_key = (body.get("access_key_id") or "").strip()
    secret_key = (body.get("secret_access_key") or "").strip()
    if access_key and access_key != SECRET_PLACEHOLDER:
        r2["access_key_id"] = access_key
    if secret_key and secret_key != SECRET_PLACEHOLDER:
        r2["secret_access_key"] = secret_key

    if not r2.get("endpoint") and r2.get("account_id"):
        r2["endpoint"] = f"https://{r2['account_id']}.r2.cloudflarestorage.com"

    cfg["r2"] = {**DEFAULT_R2, **r2}
    save_config(cfg)

    test = bool(body.get("test"))
    test_result = None
    if test or r2_is_configured(cfg["r2"]):
        try:
            remote = await asyncio.to_thread(ensure_rclone_remote, cfg["r2"])
            if test:
                rclone = rclone_bin()
                bucket = cfg["r2"]["bucket"]
                result = await asyncio.to_thread(
                    run_cmd,
                    [rclone, "lsd", f"{remote}:{bucket}"],
                    60,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        (result.stderr or result.stdout or "rclone lsd failed").strip()
                    )
                test_result = "ok"
        except Exception as exc:
            return web.json_response(
                {"ok": False, "saved": True, "error": str(exc)},
                status=400,
            )

    return web.json_response({"ok": True, "saved": True, "test": test_result})
