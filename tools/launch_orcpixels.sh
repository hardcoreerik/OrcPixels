#!/usr/bin/env bash
# OrcPixels launcher (macOS / Linux): starts the server if needed and opens the UI.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ORCPIXELS_PYTHON:-$ROOT/.venv/bin/python}"
PORT="${ORCPIXELS_PORT:-8765}"
URL="http://127.0.0.1:$PORT"
[ -x "$PY" ] || { echo "OrcPixels: no Python at $PY - run ./install.sh first" >&2; exit 1; }
up() { curl -fsS -o /dev/null --max-time 2 "$URL/" 2>/dev/null; }
if ! up; then
  (cd "$ROOT" && nohup "$PY" -m orcpixels serve --port "$PORT" >/dev/null 2>&1 &)
  for _ in $(seq 60); do up && break; sleep 0.5; done
fi
curl -fsS -X POST --max-time 20 "$URL/api/connect" >/dev/null 2>&1 || true
if command -v open >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then open "$URL"
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 &
else echo "open $URL"; fi
