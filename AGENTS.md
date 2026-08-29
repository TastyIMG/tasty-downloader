# AGENTS.md — Set up Tasty R2 Downloader on your ComfyUI box

**For you (the person who installed this custom node):** copy this entire file into your AI assistant (ChatGPT, Cursor, Claude, etc.) and ask it to configure **Tasty R2 Downloader on the same machine where ComfyUI is running**.

**For the AI reading this:** you are helping a **user** who installed this node. Work in the terminal **on their ComfyUI instance** — usually a **Vast.ai** (or similar) GPU box, not a random folder on their laptop unless they say ComfyUI runs locally.

Do **not** ask the user to paste API keys into chat if you can get them from `wrangler`, env vars, or an existing `config.json` on that box.

## Where ComfyUI runs

This custom node lives next to ComfyUI. Configure it **on that box**.

| Typical host | Extension path |
|---|---|
| Vast.ai / cloud GPU template | `/workspace/ComfyUI/custom_nodes/tasty-downloader` |
| Other cloud images | `…/ComfyUI/custom_nodes/tasty-downloader` (find it) |
| Local ComfyUI (only if user says so) | wherever they cloned `tasty-downloader` under `custom_nodes/` |

**Important:**

- Run all commands on the **ComfyUI host** (SSH into Vast, or the machine where `main.py` runs).
- Do **not** configure paths on the user’s laptop if their ComfyUI is on Vast.
- This node **bundles rclone** — it auto-downloads to `$EXT/bin/rclone` on first Download, Push, or Settings **Save & Test**. Do **not** tell them to `apt install rclone` or use `curl install.sh | sudo bash`.
- Fresh Vast images often have **no** rclone on PATH. Use `$EXT/bin/rclone`, not `/usr/bin/rclone`.

## Goal

Set up **ComfyUI Tasty R2 Downloader** so Download and Push work:

1. Find the extension folder (`$EXT`)
2. Resolve Cloudflare account + R2 credentials via CLI / env (not manual Q&A)
3. Write gitignored `config.json` (credentials + personal `models`)
4. Create the rclone remote and test `lsd` (bundled rclone downloads to `$EXT/bin/` on first use)
5. Sync hosted `config.json` via one-liner if the user has a Config URL (see below)
6. Tell the user to **restart ComfyUI**
7. Warn that Push only appends locally — they must re-upload `config.json` after Push until dynamic R2 sync exists

Do **not** commit `config.json` or secrets. Do not echo secrets in full if avoidable.

## Locate the extension

```bash
ls /workspace/ComfyUI/custom_nodes/
# folder name is usually: tasty-downloader
```

```bash
EXT="/workspace/ComfyUI/custom_nodes/tasty-downloader"   # adjust if needed
test -f "$EXT/__init__.py" && test -d "$EXT/python" && echo "found $EXT"
export EXT
```

If `/workspace/…` does not exist, search under the user’s ComfyUI install:

```bash
find ~ /workspace -path '*/custom_nodes/tasty-downloader/__init__.py' 2>/dev/null | head -3
```

Config files live in **`$EXT`** (not `$EXT/python`).

## Resolve credentials via Cloudflare CLI (preferred)

Use tooling already on the ComfyUI box:

```bash
command -v wrangler || npm i -g wrangler
wrangler whoami
```

From `wrangler whoami` / account listing, take the **Account ID**.

Discover R2 buckets:

```bash
wrangler r2 bucket list
```

Pick the R2 bucket from the list (do not assume a bucket name).

### S3 API keys for rclone

R2 S3 access keys may already exist as env vars:

```bash
env | grep -E '^(CF_R2_|R2_|CLOUDFLARE_|AWS_)' | sed 's/=.*/=***/'
```

Or in files already on the box (read if present; do not commit):

- `$EXT/config.json` (prior setup)
- `~/.config/` wrangler / cloudflare related files
- Vast instance env / secrets the user configured

If access keys are still missing, create an R2 API token via Cloudflare dashboard/API **using wrangler-authenticated session where possible**. Endpoint form:

```text
https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

Public base URL is the **r2.dev public bucket host** (HTTPS origin only), e.g. from an existing registry URL:

```bash
python3 - <<'PY'
import json, os, re
from pathlib import Path
ext = Path(os.environ["EXT"])
for name in ("registry.local.json", "registry.json"):
    p = ext / name
    if not p.exists():
        continue
    data = json.loads(p.read_text())
    models = data.get("models", data) if isinstance(data, dict) else data
    if not isinstance(models, list):
        continue
    for e in models:
        url = (e or {}).get("url") or ""
        m = re.match(r"(https://pub-[a-f0-9]+\.r2\.dev)", url)
        if m:
            print(m.group(1))
            raise SystemExit
print("")
PY
```

If inference fails, list public bucket / custom domain for that bucket — do not invent a `pub-` host.

## rclone (bundled — do not install system-wide)

The extension downloads official rclone to **`$EXT/bin/rclone`** on first Download, Push, or Settings **Save & Test**.

Optional verify (triggers download if needed):

```bash
python3 - <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ["EXT"], "python"))
from rclone_ops import ensure_rclone_binary
print(ensure_rclone_binary())
PY
```

Use the printed path for smoke tests — usually `$EXT/bin/rclone`.

## Write config.json

Create `$EXT/config.json` (gitignored):

```bash
export CF_ACCOUNT_ID="..."          # from wrangler whoami
export CF_R2_ACCESS_KEY_ID="..."    # from env / existing secret store
export CF_R2_SECRET_ACCESS_KEY="..."
export CF_R2_BUCKET="..."            # from wrangler r2 bucket list
export CF_R2_PUBLIC_BASE_URL="..."  # https://pub-….r2.dev

python3 - <<'PY'
import json, os
ext = os.environ["EXT"]
account = os.environ["CF_ACCOUNT_ID"]
cfg = {
  "registry_path": "",
  "models": [],
  "r2": {
    "account_id": account,
    "access_key_id": os.environ["CF_R2_ACCESS_KEY_ID"],
    "secret_access_key": os.environ["CF_R2_SECRET_ACCESS_KEY"],
    "bucket": os.environ["CF_R2_BUCKET"],
    "endpoint": f"https://{account}.r2.cloudflarestorage.com",
    "public_base_url": os.environ["CF_R2_PUBLIC_BASE_URL"].rstrip("/"),
    "config_url": os.environ.get("TASTY_R2_CONFIG_URL", ""),
    "remote_name": "tasty-r2",
    "chunk_size": "64M",
    "upload_concurrency": 4,
    "push_folders": [
      "diffusion_models", "unet", "loras", "vae", "clip", "clip_vision",
      "controlnet", "upscale_models", "checkpoints", "embeddings", "hypernetworks"
    ],
  },
}
path = os.path.join(ext, "config.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("wrote", path)
PY
```

The app also falls back to `CF_R2_ACCESS_KEY_ID` / `CF_R2_SECRET_ACCESS_KEY` if config keys are empty.

## Configure rclone remote

Run on the **ComfyUI box** (same `$EXT`):

```bash
EXT="${EXT:?set EXT}"

RCLONE="$(python3 - <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ["EXT"], "python"))
from rclone_ops import ensure_rclone_binary
print(ensure_rclone_binary())
PY
)"

eval "$(python3 - <<'PY'
import json, os, shlex
cfg = json.load(open(os.path.join(os.environ["EXT"], "config.json")))
r2 = cfg["r2"]
endpoint = r2.get("endpoint") or f"https://{r2['account_id']}.r2.cloudflarestorage.com"
pairs = {
    "RCLONE_REMOTE": r2.get("remote_name") or "tasty-r2",
    "CF_R2_ACCESS_KEY_ID": r2["access_key_id"],
    "CF_R2_SECRET_ACCESS_KEY": r2["secret_access_key"],
    "CF_R2_BUCKET": r2["bucket"],
    "CF_R2_ENDPOINT": endpoint,
}
for k, v in pairs.items():
    print(f"export {k}={shlex.quote(str(v))}")
PY
)"

"$RCLONE" config create "$RCLONE_REMOTE" s3 \
  provider Cloudflare \
  access_key_id "$CF_R2_ACCESS_KEY_ID" \
  secret_access_key "$CF_R2_SECRET_ACCESS_KEY" \
  endpoint "$CF_R2_ENDPOINT" \
  region auto \
  no_check_bucket true

"$RCLONE" lsd "${RCLONE_REMOTE}:${CF_R2_BUCKET}"
```

If `lsd` fails, fix credentials/endpoint before declaring success.

## Sync config (one file)

`config.json` holds credentials, settings, and personal `models`. Host that single file on R2; save **Config URL** in Settings.

```bash
export TASTY_R2_CONFIG_URL="https://pub-xxxx.r2.dev/path/config.json"
bash "$EXT/scripts/sync-local-registry.sh"
```

Or:

```bash
curl -fsSL "$TASTY_R2_CONFIG_URL" -o "$EXT/config.json"
python3 -m json.tool "$EXT/config.json" >/dev/null
```

On Vast, use absolute paths (`/workspace/ComfyUI/custom_nodes/tasty-downloader/config.json`), not relative paths from `$HOME`.

**Sync problem:** Push/Settings stay local until the user re-uploads `config.json` to the Config URL. Overwriting with a stale hosted copy wipes newer local rows.

**Future:** dynamic read/write of config from the user’s R2. Not implemented yet — document only.

## Verify

```bash
EXT="${EXT:?set EXT}"

RCLONE="$(python3 - <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ["EXT"], "python"))
from rclone_ops import ensure_rclone_binary
print(ensure_rclone_binary())
PY
)"

test -f "$EXT/config.json"
test -d "$EXT/js"
test -f "$EXT/python/server.py"
test -x "$RCLONE"
"$RCLONE" listremotes
"$RCLONE" lsd "tasty-r2:${CF_R2_BUCKET:?set CF_R2_BUCKET}" | head
```

## Finish

Tell the user:

1. **Restart ComfyUI** on that box
2. Hard-refresh the browser (`Ctrl+Shift+R`)
3. Open **Tasty Downloader** — Settings should show credentials as saved
4. **Download** pulls models from R2 (via bundled rclone when configured)
5. **Push** uploads to `{bucket}/models/{save_path}/{filename}` and appends into **local** `config.json` `models`
6. If they use a hosted config, re-upload `config.json` after Push/Settings, or the next sync one-liner will overwrite newer entries
7. Dynamic config read/write from R2 is planned but not implemented yet

## Do not

- Configure on the wrong machine (laptop terminal when ComfyUI is on Vast)
- Ask the user to paste keys when `wrangler` / env / existing `config.json` already has them
- Install system rclone — use `$EXT/bin/rclone`
- Put `config.json` under `python/`
- Treat `public_base_url` as a storage path — it is the public HTTPS host only
- Commit or publish `config.json` (contains secrets)
