import json
import os

from .paths import CONFIG_PATH, DEFAULT_PUSH_FOLDERS, DEFAULT_R2


def load_config():
    cfg = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    r2 = {**DEFAULT_R2, **(cfg.get("r2") or {})}
    if not r2.get("push_folders"):
        r2["push_folders"] = list(DEFAULT_PUSH_FOLDERS)
    cfg["r2"] = r2
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def get_r2_config():
    r2 = load_config()["r2"]
    if not r2.get("access_key_id"):
        r2["access_key_id"] = os.environ.get("CF_R2_ACCESS_KEY_ID", "").strip()
    if not r2.get("secret_access_key"):
        r2["secret_access_key"] = os.environ.get("CF_R2_SECRET_ACCESS_KEY", "").strip()
    if not r2.get("endpoint") and r2.get("account_id"):
        r2["endpoint"] = f"https://{r2['account_id']}.r2.cloudflarestorage.com"
    return r2


def r2_is_configured(r2=None):
    r2 = r2 or get_r2_config()
    return bool(
        r2.get("access_key_id")
        and r2.get("secret_access_key")
        and r2.get("bucket")
        and (r2.get("endpoint") or r2.get("account_id"))
        and r2.get("public_base_url")
    )
