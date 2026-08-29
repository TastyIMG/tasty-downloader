# AGENTS.md — get R2 secrets via terminal

**User:** paste this into your AI after installing Tasty Downloader.

**AI:** pull secrets with **wrangler + curl/python in your terminal** (usually the user’s laptop). You typically **cannot** SSH into Vast — after you have the values, give the user Settings fields or a one-liner **they** run on the ComfyUI host. Don’t walk the dashboard. Don’t install rclone. Don’t commit `config.json`.

## 1. Account + bucket (wrangler)

```bash
command -v wrangler || npm i -g wrangler
wrangler whoami          # → Account ID
wrangler r2 bucket list  # → pick their models bucket (ask if multiple)
```

```bash
export CF_ACCOUNT_ID="…"   # from whoami
export CF_R2_BUCKET="…"    # from bucket list
```

Endpoint is always:

```bash
export CF_R2_ENDPOINT="https://${CF_ACCOUNT_ID}.r2.cloudflarestorage.com"
```

## 2. S3 keys — reuse or create via API

### Prefer existing

```bash
env | grep -E '^(CF_R2_ACCESS_KEY_ID|CF_R2_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)=' | sed 's/=.*/=***/'
```

If `CF_R2_ACCESS_KEY_ID` + `CF_R2_SECRET_ACCESS_KEY` (or AWS_*) are set, use those. Also check a local gitignored `config.json` if this repo already has one on the machine you’re on — **read it, don’t print secrets**.

### Otherwise create R2 S3 credentials via Cloudflare API

Need a Cloudflare API token that can create tokens (`CLOUDFLARE_API_TOKEN`), or wrangler login session. Create user token with R2 Object Read+Write; S3 Access Key ID = token `id`, Secret = SHA-256 of token `value`.

```bash
# Token that can create user tokens — from env, or after: wrangler login
: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN}"
: "${CF_ACCOUNT_ID:?}"
: "${CF_R2_BUCKET:?}"

python3 - <<'PY'
import hashlib, json, os, urllib.request

token = os.environ["CLOUDFLARE_API_TOKEN"]
account = os.environ["CF_ACCOUNT_ID"]
bucket = os.environ["CF_R2_BUCKET"]

def api(method, url, body=None):
    req = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)

# Resolve R2 bucket-item permission group IDs (don’t hardcode forever)
groups = api("GET", "https://api.cloudflare.com/client/v4/user/tokens/permission_groups")["result"]
want = {
    g["name"]: g["id"]
    for g in groups
    if g.get("name") in (
        "Workers R2 Storage Bucket Item Read",
        "Workers R2 Storage Bucket Item Write",
    )
}
if len(want) < 2:
    raise SystemExit(f"missing R2 permission groups, got: {want}")

body = {
    "name": f"tasty-downloader-{bucket}",
    "policies": [
        {
            "effect": "allow",
            "resources": {
                f"com.cloudflare.edge.r2.bucket.{account}_default_{bucket}": "*",
            },
            "permission_groups": [{"id": i} for i in want.values()],
        }
    ],
}
created = api("POST", "https://api.cloudflare.com/client/v4/user/tokens", body)["result"]
access_key_id = created["id"]
secret_access_key = hashlib.sha256(created["value"].encode()).hexdigest()
print("export CF_R2_ACCESS_KEY_ID=" + access_key_id)
print("export CF_R2_SECRET_ACCESS_KEY=" + secret_access_key)
print("# secret shown once — copy into Settings; do not commit")
PY
```

```bash
eval "$(…paste the export lines…)"   # or set from script output
```

## 3. Public base URL

Need the HTTPS origin only (`https://pub-….r2.dev` or custom domain) — no path.

```bash
# If wrangler/API already knows public domains for the bucket, use that.
# Else infer from an existing public object URL the user has, or:
curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/r2/buckets/${CF_R2_BUCKET}/domains/managed" \
  | python3 -m json.tool
```

```bash
export CF_R2_PUBLIC_BASE_URL="https://pub-….r2.dev"   # from API / user
```

## 4. Put secrets on the ComfyUI box

You usually **don’t** have that shell. Either:

**A.** Tell them: **Tasty Downloader → Settings** → paste  
`account_id`, `access_key_id`, `secret_access_key`, `bucket`, `public_base_url` → **Save & Test**

**B.** Give them this to run **on the ComfyUI host** (Vast path; change if needed):

```bash
EXT="/workspace/ComfyUI/custom_nodes/tasty-downloader"
# exports from steps 1–3 must be set in THAT shell
python3 - <<'PY'
import json, os
ext, account = os.environ["EXT"], os.environ["CF_ACCOUNT_ID"]
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
  },
}
path = f"{ext}/config.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("wrote", path)
PY
```

Then: restart ComfyUI, hard-refresh.

Optional hosted pull (if they already have a Config URL):

```bash
curl -fsSL "$TASTY_R2_CONFIG_URL" \
  -o /workspace/ComfyUI/custom_nodes/tasty-downloader/config.json
```

## Field map

| `config.json` | Env |
|---|---|
| `r2.account_id` | `CF_ACCOUNT_ID` |
| `r2.access_key_id` | `CF_R2_ACCESS_KEY_ID` |
| `r2.secret_access_key` | `CF_R2_SECRET_ACCESS_KEY` |
| `r2.bucket` | `CF_R2_BUCKET` |
| `r2.public_base_url` | `CF_R2_PUBLIC_BASE_URL` |
| `r2.endpoint` | derived from account_id |
| `r2.config_url` | `TASTY_R2_CONFIG_URL` (optional) |

File: `custom_nodes/tasty-downloader/config.json` (gitignored, not under `python/`).
