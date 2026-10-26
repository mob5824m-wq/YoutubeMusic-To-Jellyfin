#!/usr/bin/env bash
#
# Cross-platform launcher for the ytm2j web UI.
# Works on Linux and macOS. Creates a local virtualenv and installs
# dependencies on first run, then starts the server.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

pick_python() {
  if [ -x "$HERE/.venv/bin/python" ]; then
    echo "$HERE/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    echo "python3"
  elif command -v python >/dev/null 2>&1; then
    echo "python"
  else
    echo ""
  fi
}

PY="$(pick_python)"
if [ -z "$PY" ]; then
  echo "Python 3.9+ is required but was not found on PATH." >&2
  exit 1
fi

if ! "$PY" -c "import yt_dlp, fastapi, uvicorn" >/dev/null 2>&1; then
  echo "First run: installing dependencies into $HERE/.venv ..."
  if ! "$PY" -m venv "$HERE/.venv" >/dev/null 2>&1; then
    echo "Creating a virtualenv failed. Install 'python3-venv' / 'python3.x-venv' and retry." >&2
    exit 1
  fi
  PY="$HERE/.venv/bin/python"
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r requirements.txt
fi

exec "$PY" -m ytm2j serve "$@"
