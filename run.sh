#!/usr/bin/env bash
# TubeGrab — bootstrap the Python venv + React UI on first run, then start the server.
#   ./run.sh          build UI if needed, serve everything on :8765
#   ./run.sh --dev    Flask on :8765 + Vite hot-reload UI on :5173
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Creating virtual environment…"
    python3 -m venv .venv
fi
. .venv/bin/activate
pip install -q -r requirements.txt

command -v bun >/dev/null || { echo "bun is required: https://bun.sh" >&2; exit 1; }
( cd web && bun install --silent )

if [ "${1:-}" = "--dev" ]; then
    python app.py &
    trap 'kill $! 2>/dev/null' EXIT
    echo "Dev UI → http://127.0.0.1:5173  (API on :8765)"
    cd web && bun run dev
    exit
fi

# Rebuild the UI when any source file is newer than the last build.
if [ ! -f web/dist/index.html ] || [ -n "$(find web/src web/index.html web/vite.config.ts -newer web/dist/index.html -print -quit)" ]; then
    echo "Building UI…"
    ( cd web && bun run build >/dev/null )
fi

echo "Starting TubeGrab → http://127.0.0.1:${TUBEGRAB_PORT:-8765}"
exec python app.py "$@"
