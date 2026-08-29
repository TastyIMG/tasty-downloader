#!/usr/bin/env bash
set -euo pipefail

# Pull one hosted config.json (credentials + settings + personal models).
# Prefer Settings → Config URL, or TASTY_R2_CONFIG_URL.
# Do not hardcode personal URLs in the repo.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSION_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="$EXTENSION_DIR/config.json"

CONFIG_URL="${TASTY_R2_CONFIG_URL:-}"

if [[ -z "$CONFIG_URL" && -f "$CONFIG_PATH" ]]; then
  CONFIG_URL="$(python3 - <<PY
import json
try:
    cfg = json.load(open("$CONFIG_PATH", encoding="utf-8"))
except Exception:
    raise SystemExit(0)
print(((cfg.get("r2") or {}).get("config_url") or "").strip())
PY
)"
fi

if [[ -z "${CONFIG_URL:-}" ]]; then
  echo "Set TASTY_R2_CONFIG_URL, or save Config URL in Settings first." >&2
  echo "Example:" >&2
  echo "  export TASTY_R2_CONFIG_URL=\"https://pub-xxxx.r2.dev/path/config.json\"" >&2
  exit 1
fi

tmp="$(mktemp)"
echo "Fetching:"
echo "  $CONFIG_URL"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$CONFIG_URL" -o "$tmp"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$tmp" "$CONFIG_URL"
else
  echo "Error: curl or wget is required." >&2
  rm -f "$tmp"
  exit 1
fi

python3 -m json.tool "$tmp" >/dev/null
mv "$tmp" "$CONFIG_PATH"
echo "Updated: $CONFIG_PATH"
echo "Restart ComfyUI (or reload the extension) to pick up changes."
