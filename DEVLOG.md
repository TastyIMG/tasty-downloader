# DEVLOG — mistakes & lessons

Everything that broke or shipped wrong in this project. **Append new rows; do not trim old ones.** Next agent: read this before touching Settings, sync, or progress streaming.

---

## Download tab

| Issue | What went wrong | Fix / rule |
|---|---|---|
| Download never started | Used broken `api.apiURL()` instead of `api.fetchApi` (same as list endpoint) | All Comfy API calls: `api.fetchApi("/tasty-r2/...")` |
| `Unexpected non-whitespace character after JSON at position N (line 2 column 1)` on download | OK responses treated as single JSON via `resp.json()`; body is **NDJSON** (many lines) | Success path: stream line-by-line only; `resp.json()` only for `!resp.ok` errors |
| Closing panel cancelled downloads | `close()` aborted every `AbortController` | Only **Cancel** aborts; close clears DOM refs; reopen reattaches live progress |
| No cancel on download | Shipped streaming download without abort | `AbortController` + Cancel button; server drops write on disconnect |
| Server disconnect detection | Assumed `request.is_disconnected()` exists on Comfy’s aiohttp | Use helper in `streaming.py` that works with Comfy’s stack |
| ~1 MB/s R2 downloads | Download used aiohttp via public URL; opened file every 1 MiB chunk; progress `drain()` every chunk | R2 models: `rclone copyto` from `{bucket}/models/...` (mirror Push). External URLs (HF, etc.): HTTP with 4 MiB chunks + throttled progress |
| Manual rclone install | Docs/UI told users to `curl install.sh \| sudo bash` | Auto-download official rclone zip to `$EXT/bin/rclone` on first use; gitignore `bin/` |
| Blocking **“Preparing rclone”** / warm-ready HTTP fallback | `config create` + cache + dual paths on every click | **Inline `:s3,...:bucket/key` URIs** from `config.json` — spawn rclone immediately; bootstrap binary once at ComfyUI start |
| Download stuck at **0%** after deploy | Relied on parsing rclone stdout (buffered / wrong format); rclone also wrote to `*.partial.partial` then renamed at end so dest size stayed 0 | Poll growing local file size every 0.25s; `--inplace` + `--multi-thread-streams 0` so bytes land on the path we poll; HEAD total in parallel (non-blocking) |
| rclone `Custom endpoint \`https\` was not a valid URI` | Inline `:s3,endpoint=https://…:bucket/key` — unquoted (and even quoted) URL `:` breaks on Vast's rclone path parsing | **Never put credentials/endpoint in the connection string.** Use `:s3:bucket/key` + separate `--s3-endpoint` / `--s3-access-key-id` argv flags (same as AGENTS.md `config create`). Download: HTTP public-URL fallback if rclone still fails |

---

## Registry / model list

| Issue | What went wrong | Fix / rule |
|---|---|---|
| Fake **Flux 1 Dev** in UI | Bundled `registry.json` had placeholder entries with `pub-xxxx` URLs shown as real models | `models: []` + `_example` array; server filters `pub-xxxx` / `example_*` |
| Deleted example format | Cleared `registry.json` entirely; user still needed format reference | Never delete examples — use `_example` (JSON has no comments) |
| “Cached” fake models on Vast | User thought browser cached; Vast box had **old `registry.json`** merged with `registry.local.json` at runtime | UI = merge of both files; stale git on remote box ≠ cache |
| Klein under `unet` on Vast | Old hosted `registry.local.json` had wrong `save_path` | Re-upload fixed registry after Push; filter placeholders |
| Download 404 with only `config.json` models | List endpoint required `registry.json` on disk | Also allow models from `config.json` `models` array |

---

## Push tab

| Issue | What went wrong | Fix / rule |
|---|---|---|
| Nested loras not listed | Only scanned top level of each push folder | `os.walk` nested dirs; `save_path` can be e.g. `loras/subdir` |
| Push stuck at **0% · 0 B / 6.46 GB** | rclone `--progress` uses `\r`; `readline()` on stdout never fired until upload finished | Read chunks + split on `\r`/`\n`; parse progress like download NDJSON |
| Push progress not built like download | Implemented push without reusing download streaming patterns despite prior download work | **Always mirror download progress path for push** |
| `rclone exited with code 1` only | `--use-json-log` + weak stderr capture hid real error | Match manual working flags: `-v --progress --stats 1s`; capture log tail on failure |
| No error feedback during long push | UI looked idle while multi-GB upload ran | Stream progress events; surface rclone stderr in UI on failure |
| Push **sizes differ** on large models (Flux ~19GB) | rclone multipart to R2 finished with wrong remote size; stale partial on bucket; concurrency 8 | `deletefile` before push; cap concurrency (≤2 for ≥15GB, ≤4 for ≥5GB); 128M chunks; default concurrency 4 |

---

## Settings / config sync

| Issue | What went wrong | Fix / rule |
|---|---|---|
| Forgot **config URL** | Only had registry sync; wiped config = dead box even with registry | One hosted **`config.json`** (credentials + `models`); `r2.config_url` |
| Two hosted files | Shipped Config URL + Registry URL | User wanted **one file** — personal models in `config.json` |
| Config URL did nothing | Field only saved the URL string | **Load** must fetch URL or accept pasted JSON and populate form + write local file |
| Paste full JSON in URL field | Auto-load tried to treat JSON as URL → parse errors | Separate **Or paste config.json** textarea; URL field = URL only |
| **405 Method Not Allowed** on Load | New route `POST /tasty-r2/settings/pull`; Comfy registers routes **only at startup** | Use existing `POST /tasty-r2/settings` with `load: true` or `config: {...}` — **no extra settings routes** |
| Hidden fields still visible | `.tasty-r2-field { display: flex }` overrode HTML `hidden` | `.tasty-r2-field[hidden] { display: none !important; }` |
| `config.example.json` stale | Didn’t reflect combined `models` + `config_url` shape | Keep example in sync with `config.json` schema |
| Hardcoded **"fields populated"** on Load | UI always showed success even when `models_count` was 0 or server ignored `load: true` (old Python) | Status from real response: `loaded`, `models_loaded`, `models_count`, `configured`; error if source had models but save has 0 |
| **`remote-config-check.json` committed** | Agent saved fetched config (full R2 keys) to repo root during debug; file was committed and pushed | Never write secrets to workspace; curl to `/tmp` only; gitignore `remote-config-check.json`; **rotate R2 keys** if pushed |

---

## Docs / sync / public repo

| Issue | What went wrong | Fix / rule |
|---|---|---|
| Personal R2 URLs in public readme | Hardcoded `pub-e03ebcba…` and paths in tracked files | Never commit personal URLs; use `$TASTY_R2_CONFIG_URL` placeholders |
| curl **error 23** on Vast | One-liner used relative `-o ComfyUI/custom_nodes/...` without `/workspace` | Use absolute paths on Vast: `/workspace/ComfyUI/custom_nodes/tasty-downloader/...` |
| Wrong clone folder name in docs | `ComfyUI-Tasty-R2-Downloader` vs actual `tasty-downloader` | Match real folder name on each machine |
| Push doesn’t upload registry/config to R2 | By design — local append only | After Push/Settings, user must **re-upload** hosted `config.json` or next sync overwrites |

---

## Deploy / environments

| Issue | What went wrong | Fix / rule |
|---|---|---|
| Maintainer agent confused **AGENTS.md audience** | Wrote AGENTS.md like internal dev docs (Cursor repo, local Comfy paths) | **AGENTS.md is for end users** who installed the node — paste into their AI to configure on **their ComfyUI box** (usually Vast). Maintainer notes belong in DEVLOG only. |
| Agent assumed **local dev = user's ComfyUI** | Debugged `~/comfy/…` on maintainer laptop; user's Vast unchanged | User's runtime is their Vast instance at `/workspace/ComfyUI/custom_nodes/tasty-downloader`. |
| Agent blamed **host `/usr/bin/rclone`** | Fresh Vast has no rclone; node uses **`$EXT/bin/rclone`** auto-download | Bundled binary is the product path (see AGENTS.md). |
| **`ModuleNotFoundError: sqlalchemy`** | Ran system `python3 main.py` instead of Comfy **venv** (local dev only) | Local: `cd ~/comfy/ComfyUI && source venv/bin/activate && python main.py --enable-manager` |
| Dev repo ≠ running extension | Edits in `~/Documents/tasty_downloader`; Vast or local Comfy still on old copy | After changes: deploy to **target runtime**, **restart Comfy**, hard-refresh browser |
| `cp` permission denied | Agent copied to Comfy path, failed silently, didn’t tell user | Escalate immediately if deploy copy fails |
| JS/CSS stale in browser | Comfy serves extension JS; browser caches | Hard refresh `Ctrl+Shift+R`; extension `init()` should refresh injected styles |

---

## Process (agent)

| Issue | What went wrong | Fix / rule |
|---|---|---|
| No mistake log | Fixed forward without recording failures | **Update this file** when something breaks |
| Shipped Load without E2E test | Never hit running ComfyUI with Load/Save | Checklist below before “done” |
| Guessing under time pressure | User had to say “ask me” / “stop guessing” | One clarifying question beats five wrong fixes |

---

## Checklist before calling a feature “done”

1. Code deployed to the **runtime** ComfyUI extension path (usually **Vast** `/workspace/ComfyUI/custom_nodes/tasty-downloader`) — not just the local git repo.
2. **Restart ComfyUI** on that runtime (any Python route change).
3. **Hard-refresh** browser (`Ctrl+Shift+R`).
4. **Settings → Load** (URL and pasted JSON) → fields populate, `models_count` correct.
5. **Save & Test** → rclone OK (`$EXT/bin/rclone` exists after first test).
6. **Download** tab lists models; one download shows live progress + cancel works.
7. **Push** tab finds nested files; push shows live progress; failure shows rclone message not just exit code.

---

## Paths (do not confuse these)

| What | Path | Notes |
|---|---|---|
| **Production (Vast / cloud)** | `/workspace/ComfyUI/custom_nodes/tasty-downloader` | **Main instance** — Download/Push must work here |
| Git repo (source) | `~/Documents/tasty_downloader` | Local only; not where Comfy runs unless user says so |
| Local Comfy smoke-test | `~/comfy/ComfyUI/custom_nodes/tasty-downloader` | Optional dev; **not** Vast |
| Gitignored secrets | `$EXT/config.json` on the **runtime** box | credentials + personal `models` |
| Start Comfy (local dev example) | `source ~/comfy/ComfyUI/venv/bin/activate && python main.py --enable-manager` | Vast templates vary — use their launcher |

---

## Still open / known gaps

- Manual re-upload of hosted `config.json` after Push/Settings (no dynamic R2 read/write yet).
- Easy one-click Comfy launcher + open browser tab (user asked; not in repo).
- ComfyUI Desktop app avoids venv/terminal pain — optional for local dev.
