# ComfyUI Tasty R2 Downloader

Download models from your public R2 storage into the correct ComfyUI folders. Push unregistered local models back up with rclone.

**Typical install:** [Vast.ai](https://vast.ai) (or similar cloud GPU) ComfyUI at `/workspace/ComfyUI/custom_nodes/tasty-downloader`.

**First-time setup with AI:** after you clone this node on your ComfyUI box, copy [AGENTS.md](AGENTS.md) into your AI assistant and ask it to configure R2 + `config.json` in the terminal **on that same machine** (SSH into Vast, not your laptop).

## Install

On your ComfyUI machine (Vast / cloud GPU):

```bash
cd /workspace/ComfyUI/custom_nodes
git clone <this-repo> tasty-downloader
# restart ComfyUI — rclone auto-downloads to bin/ on first Download/Push/Settings test
```

Use absolute paths on Vast (`/workspace/...`), not relative paths from `$HOME`.

## Usage

Open from:

- **Tasty → Tasty Downloader**
- **Extensions → Tasty R2 Downloader → Tasty Downloader**
- **Tasty Downloader** action bar button

Tabs:

| Tab | What it does |
|---|---|
| **Download** | R2 models: **rclone S3** starts on click (credentials inline — no config/warm step). External URLs: HTTP. |
| **Push** | **rclone S3** upload (same inline credentials) |
| **Settings** | R2 credentials + push folders (saved to gitignored `config.json`) |

## Registry

Default: `registry.json` in this folder (examples live under `_example`; only `models` is loaded).

Optional override via `config.json` or env:

```json
{
  "registry_path": "/workspace/config/registry.json"
}
```

```bash
export TASTY_R2_REGISTRY_PATH="/workspace/config/registry.json"
```

| Field | Description |
|---|---|
| `for_model` | Label shown in UI |
| `filename` | Exact ComfyUI filename |
| `save_path` | Folder under `ComfyUI/models/` |
| `url` | Public R2 URL |

`config.json` (gitignored) holds R2 credentials **and** your personal `models` list. Successful **Push** appends there. Bundled `registry.json` is still merged for shared/example catalogs.

### Syncing (one file)

Host your `config.json` on R2, save **Config URL** in Settings, then on a new box:

```bash
export TASTY_R2_CONFIG_URL="https://pub-xxxx.r2.dev/path/config.json"
cd /workspace/ComfyUI/custom_nodes/tasty-downloader
bash scripts/sync-local-registry.sh
```

Or:

```bash
curl -fsSL "$TASTY_R2_CONFIG_URL" -o /workspace/ComfyUI/custom_nodes/tasty-downloader/config.json
```

After Settings changes or Push, **re-upload** that same `config.json` to the Config URL.

Restart ComfyUI after replacing the file.

**Known gap:** sync is still a manual overwrite. Longer-term the node should read/write this file dynamically from the user’s R2. Not implemented yet.

## Settings / Push

Configure in the **Settings** tab (writes `config.json` on the box):

- Cloudflare account id, access key, secret
- Bucket name
- Public base URL (e.g. `https://pub-….r2.dev`)
- Config URL (hosted `config.json` — credentials + personal models)
- Push folders list
- Chunk size (default `64M`)

Env fallbacks if config keys are empty: `CF_R2_ACCESS_KEY_ID`, `CF_R2_SECRET_ACCESS_KEY`.

Push uploads with rclone multipart (`--s3-chunk-size 64M`) to `{bucket}/models/{save_path}/{filename}`, then appends the model into `config.json` locally.

See [config.example.json](config.example.json) for the full shape.
