#!/usr/bin/env bash
# Smoke-test rclone auto-download URLs and optional full install to a temp dir.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== URL checks =="
python3 <<'PY'
import urllib.request

urls = {
    "current": "https://downloads.rclone.org/rclone-current-linux-amd64.zip",
    "versioned": "https://downloads.rclone.org/v1.68.2/rclone-v1.68.2-linux-amd64.zip",
    "broken (must 404)": "https://downloads.rclone.org/rclone-v1.68.2-linux-amd64.zip",
}
for label, url in urls.items():
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            ok = resp.status == 200
    except Exception as exc:
        ok = False
        err = exc
    else:
        err = None
    if label.startswith("broken"):
        if not ok:
            print(f"OK  {label}: 404 as expected")
        else:
            raise SystemExit(f"FAIL {label}: expected 404")
    elif ok:
        print(f"OK  {label}: {url}")
    else:
        raise SystemExit(f"FAIL {label}: {url} -> {err}")
PY

echo "== bundled install smoke test =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python3 <<PY
import os, shutil, stat, subprocess, sys, urllib.request, zipfile
from pathlib import Path

ext = Path("$TMP")
bin_dir = ext / "bin"
bin_dir.mkdir(parents=True)
bundled = bin_dir / "rclone"
zip_path = bin_dir / "rclone-download.zip"
url = "https://downloads.rclone.org/rclone-current-linux-amd64.zip"
with urllib.request.urlopen(url, timeout=120) as resp, open(zip_path, "wb") as out:
    shutil.copyfileobj(resp, out)
with zipfile.ZipFile(zip_path) as archive:
    member = next(n for n in archive.namelist() if n.endswith("/rclone"))
    with archive.open(member) as src, open(bundled, "wb") as out:
        shutil.copyfileobj(src, out)
bundled.chmod(bundled.stat().st_mode | stat.S_IXUSR)
zip_path.unlink(missing_ok=True)
result = subprocess.run([str(bundled), "version"], capture_output=True, text=True, check=False)
if result.returncode != 0:
    print(result.stderr or result.stdout, file=sys.stderr)
    raise SystemExit(result.returncode)
print(result.stdout.splitlines()[0])
PY

echo "PASS"
