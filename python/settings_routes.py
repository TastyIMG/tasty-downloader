import asyncio
import json

import aiohttp
from aiohttp import web
from server import PromptServer

from .config_store import get_r2_config, load_config, r2_is_configured, save_config
from .paths import DEFAULT_PUSH_FOLDERS, DEFAULT_R2, SECRET_PLACEHOLDER
from .rclone_ops import ensure_rclone_remote, rclone_available, rclone_bin, run_cmd

routes = PromptServer.instance.routes


def settings_payload():
    cfg = load_config()
    r2 = get_r2_config()
    return {
        "registry_path": cfg.get("registry_path") or "",
        "account_id": r2.get("account_id") or "",
        "access_key_configured": bool(r2.get("access_key_id")),
        "secret_configured": bool(r2.get("secret_access_key")),
        "bucket": r2.get("bucket") or "",
        "endpoint": r2.get("endpoint") or "",
        "public_base_url": r2.get("public_base_url") or "",
        "config_url": r2.get("config_url") or "",
        "remote_name": r2.get("remote_name") or "tasty-r2",
        "chunk_size": r2.get("chunk_size") or "64M",
        "upload_concurrency": r2.get("upload_concurrency") or 8,
        "push_folders": r2.get("push_folders") or list(DEFAULT_PUSH_FOLDERS),
        "models_count": len(cfg.get("models") or [])
        if isinstance(cfg.get("models"), list)
        else 0,
        "rclone_available": rclone_available(),
        "configured": r2_is_configured(r2),
    }


async def fetch_remote_config(url):
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(
                    f"Config URL returned HTTP {resp.status}: {text[:200]}"
                )
            try:
                data = await resp.json(content_type=None)
            except Exception as exc:
                raise RuntimeError(f"Config URL did not return JSON: {exc}") from exc
    return normalize_remote_config(data)


def normalize_remote_config(remote):
    if not isinstance(remote, dict):
        raise RuntimeError("Config must be a JSON object")
    if "r2" in remote and not isinstance(remote.get("r2"), dict):
        raise RuntimeError("Config JSON r2 must be an object")
    return remote


def apply_pulled_config(remote, config_url=""):
    """Write remote config locally; keep Config URL when pulling from a hosted file."""
    remote = normalize_remote_config(remote)
    cfg = {
        "registry_path": (remote.get("registry_path") or "").strip(),
        "models": remote.get("models") if isinstance(remote.get("models"), list) else [],
        "r2": {**DEFAULT_R2, **(remote.get("r2") or {})},
    }
    if config_url:
        cfg["r2"]["config_url"] = config_url
    pub = (cfg["r2"].get("public_base_url") or "").rstrip("/")
    if pub:
        cfg["r2"]["public_base_url"] = pub
    if not cfg["r2"].get("endpoint") and cfg["r2"].get("account_id"):
        cfg["r2"]["endpoint"] = (
            f"https://{cfg['r2']['account_id']}.r2.cloudflarestorage.com"
        )
    save_config(cfg)
    return cfg


@routes.get("/tasty-r2/settings")
async def get_settings(_request):
    return web.json_response(settings_payload())


@routes.post("/tasty-r2/settings/pull")
async def pull_settings(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    pasted = body.get("config")
    if isinstance(pasted, dict):
        try:
            apply_pulled_config(pasted, (body.get("config_url") or "").strip())
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        payload = settings_payload()
        payload["ok"] = True
        payload["pulled"] = True
        return web.json_response(payload)

    url = (body.get("config_url") or "").strip()
    if not url:
        url = (get_r2_config().get("config_url") or "").strip()
    if not url:
        return web.json_response(
            {"error": "Paste a config URL or paste config.json contents"},
            status=400,
        )
    if url.startswith("{"):
        return web.json_response(
            {"error": "That looks like JSON — click Load after pasting, not Save"},
            status=400,
        )
    if not url.startswith(("http://", "https://")):
        return web.json_response({"error": "Config URL must be http(s)"}, status=400)

    try:
        remote = await fetch_remote_config(url)
        apply_pulled_config(remote, url)
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    payload = settings_payload()
    payload["ok"] = True
    payload["pulled"] = True
    return web.json_response(payload)


@routes.post("/tasty-r2/settings")
async def save_settings(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    config_url = (body.get("config_url") or "").strip()
    want_pull = bool(body.get("pull"))
    keys_blank = not (body.get("access_key_id") or "").strip() and not (
        body.get("secret_access_key") or ""
    ).strip()
    local_has_keys = bool(get_r2_config().get("access_key_id"))
    if config_url and (want_pull or (keys_blank and not local_has_keys)):
        try:
            remote = await fetch_remote_config(config_url)
            apply_pulled_config(remote, config_url)
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        body = {**body, "config_url": config_url}

    cfg = load_config()
    r2 = {**cfg.get("r2", {})}

    if "registry_path" in body:
        cfg["registry_path"] = (body.get("registry_path") or "").strip()

    for key in (
        "account_id",
        "bucket",
        "endpoint",
        "public_base_url",
        "config_url",
        "remote_name",
        "chunk_size",
    ):
        if key in body:
            val = (body.get(key) or "").strip()
            if key in ("endpoint", "chunk_size") and not val:
                continue
            r2[key] = val

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

    pub = (r2.get("public_base_url") or "").rstrip("/")
    if pub:
        r2["public_base_url"] = pub

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
            payload = settings_payload()
            payload.update({"ok": False, "saved": True, "error": str(exc)})
            return web.json_response(payload, status=400)

    payload = settings_payload()
    payload.update({"ok": True, "saved": True, "test": test_result})
    return web.json_response(payload)
