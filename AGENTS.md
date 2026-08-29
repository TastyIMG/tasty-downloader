# AGENTS.md — Configure Tasty R2 Downloader via terminal

Copy this whole file into your AI coding agent. Ask it to configure this ComfyUI custom node on the current machine using the terminal and the user's existing Cloudflare CLI (`wrangler`) access. Do **not** ask the user to paste API keys into chat if they can be obtained from wrangler / env / existing config.

## Goal

Set up **ComfyUI Tasty R2 Downloader** so Download and Push work:

1. Find the extension folder
2. Install `rclone` if missing
3. Resolve Cloudflare account + R2 credentials via CLI / env (not manual Q&A)
4. Write gitignored `config.json`
5. Create the rclone remote and test `lsd`
6. Optionally sync `registry.local.json`
7. Tell the user to restart ComfyUI

Do **not** commit `config.json`, `registry.local.json`, or secrets. Do not echo secrets in full if avoidable.

## Locate the extension

```bash
ls /workspace/ComfyUI/custom_nodes/
# often: tasty-downloader
```

```bash
EXT="/workspace/ComfyUI/custom_nodes/tasty-downloader"   # adjust if needed
test -f "$EXT/__init__.py" && test -d "$EXT/python" && echo "found $EXT"
export EXT
```

Config files live in **`$EXT`** (not `$EXT/python`).

## Resolve credentials via Cloudflare CLI (preferred)

Prefer tooling already authenticated on the machine:

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

Or in files the user already has (read if present; do not commit):

- `$EXT/config.json` (prior setup)
- `~/.config/` wrangler / cloudflare related files
- project `.env` / Vast secrets

If access keys are still missing, create an R2 API token via Cloudflare dashboard/API **using wrangler-authenticated session where possible**, or use:

```bash
# Account ID from whoami; endpoint form:
# https://<ACCOUNT_ID>.r2.cloudflarestorage.com
```

Public base URL is the **r2.dev public bucket host** (HTTPS origin only), e.g. from an existing registry URL:

```bash
# Infer from registry if present
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

If inference fails, list public bucket / custom domain via wrangler docs for that bucket — do not invent a pub- host.

## Install rclone

```bash
command -v rclone || curl -fsSL https://rclone.org/install.sh | sudo bash
rclone version
```

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
  "r2": {
    "account_id": account,
    "access_key_id": os.environ["CF_R2_ACCESS_KEY_ID"],
    "secret_access_key": os.environ["CF_R2_SECRET_ACCESS_KEY"],
    "bucket": os.environ["CF_R2_BUCKET"],
    "endpoint": f"https://{account}.r2.cloudflarestorage.com",
    "public_base_url": os.environ["CF_R2_PUBLIC_BASE_URL"].rstrip("/"),
    "remote_name": "tasty-r2",
    "chunk_size": "64M",
    "upload_concurrency": 8,
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

App also falls back to `CF_R2_ACCESS_KEY_ID` / `CF_R2_SECRET_ACCESS_KEY` if config keys are empty.

## Configure rclone remote

```bash
EXT="${EXT:?set EXT}"

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

rclone config create "$RCLONE_REMOTE" s3 \
  provider Cloudflare \
  access_key_id "$CF_R2_ACCESS_KEY_ID" \
  secret_access_key "$CF_R2_SECRET_ACCESS_KEY" \
  endpoint "$CF_R2_ENDPOINT" \
  region auto \
  no_check_bucket true

rclone lsd "${RCLONE_REMOTE}:${CF_R2_BUCKET}"
```

If `lsd` fails, fix credentials/endpoint before declaring success.

## Optional: sync personal registry

```bash
# Set YOUR hosted registry URL — never commit personal URLs to the repo
export TASTY_R2_LOCAL_REGISTRY_URL="https://pub-xxxx.r2.dev/path/registry.local.json"
bash "$EXT/scripts/sync-local-registry.sh"
# or:
curl -fsSL "$TASTY_R2_LOCAL_REGISTRY_URL" -o "$EXT/registry.local.json"
python3 -m json.tool "$EXT/registry.local.json" >/dev/null
```

## Verify

```bash
test -f "$EXT/config.json"
test -d "$EXT/js"
test -f "$EXT/python/server.py"
rclone listremotes
rclone lsd "tasty-r2:${CF_R2_BUCKET:?set CF_R2_BUCKET}" | head
```

## Finish

Tell the user:

1. Restart ComfyUI
2. Open **Tasty Downloader** — Settings should show credentials as saved
3. **Push** uploads to `{bucket}/models/{save_path}/{filename}` and appends `registry.local.json`
4. **Download** uses registry URLs under `public_base_url`

## Do not

- Prompt the user to manually type keys when wrangler/env already has access
- Commit secrets
- Put `config.json` under `python/`
- Treat `public_base_url` as a storage path — it is the public HTTPS host only
