#!/usr/bin/env bash
# macOS double-click launcher for the ytm2j web UI.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
if [ -x "$HERE/.venv/bin/python" ]; then PY="$HERE/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then PY="python3"
else PY="python"; fi

if ! "$PY" -c "import yt_dlp, fastapi, uvicorn" >/dev/null 2>&1; then
  echo "First run: installing dependencies into $HERE/.venv ..."
  "$PY" -m venv "$HERE/.venv"
  PY="$HERE/.venv/bin/python"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r requirements.txt
fi
exec "$PY" -m ytm2j serve "$@"
