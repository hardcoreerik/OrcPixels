#!/usr/bin/env bash
# OrcPixels installer for macOS / Linux. Safe to run again (idempotent).
#   ./install.sh [--shortcut] [--python /path/to/python3]
# * checks for Python 3.11+, creates .venv here and installs requirements.txt
# * copies example configs that are missing (never overwrites your files)
# * --shortcut: a launcher entry (Linux: ~/.local/share/applications/orcpixels.desktop and
#   ~/Desktop if present; macOS: ~/Desktop/OrcPixels.command). No autostart is ever added.
# The AI status / buddy feature needs Windows; here it reports itself unavailable.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
SHORTCUT=0
PY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --shortcut) SHORTCUT=1 ;;
    --python) PY="${2:?}"; shift ;;
    --with-ai) echo "[orcpixels] note: the AI feature's foreground detection is Windows-only; skipping extras" ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
say() { echo "[orcpixels] $*"; }

ok_py() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; }
if [ -z "$PY" ]; then
  for c in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && ok_py "$c"; then PY="$(command -v "$c")"; break; fi
  done
fi
if [ -z "$PY" ] || ! ok_py "$PY"; then
  echo "Python 3.11 or newer was not found. Install it (python.org, Homebrew 'brew install python', or your distro's python3 + python3-venv) or pass --python." >&2
  exit 1
fi
say "using $("$PY" -c 'import sys; print("Python %d.%d.%d" % sys.version_info[:3])') ($PY)"

VPY="$ROOT/.venv/bin/python"
if [ -x "$VPY" ] && ! "$VPY" -c 'import sys' 2>/dev/null; then say ".venv is broken: recreating"; rm -rf "$ROOT/.venv"; fi
if [ ! -x "$VPY" ]; then
  say "creating .venv"
  "$PY" -m venv "$ROOT/.venv" || { echo "python -m venv failed (Debian/Ubuntu: sudo apt install python3-venv)" >&2; exit 1; }
else
  say ".venv exists"
fi
"$VPY" -m pip install -q --disable-pip-version-check --upgrade pip >/dev/null
say "installing requirements.txt"
"$VPY" -m pip install -q --disable-pip-version-check -r "$ROOT/requirements.txt"

copy_if_missing() {
  if [ -f "$ROOT/$1" ] && [ ! -e "$ROOT/$2" ]; then cp "$ROOT/$1" "$ROOT/$2"; say "created $2 from $1"
  elif [ -e "$ROOT/$2" ]; then say "kept existing $2"; fi
}
copy_if_missing config/settings.example.json config/settings.json
copy_if_missing config/apps.local.example.json config/apps.local.json
copy_if_missing .env.example .env
# config/displays.json is not copied: the example has placeholder addresses; the web UI
# writes it when you add a sign.

chmod +x "$ROOT/tools/launch_orcpixels.sh" 2>/dev/null || true
if [ "$SHORTCUT" = 1 ]; then
  if [ "$(uname)" = "Darwin" ]; then
    f="$HOME/Desktop/OrcPixels.command"
    printf '#!/bin/bash\nexec "%s/tools/launch_orcpixels.sh"\n' "$ROOT" > "$f"; chmod +x "$f"
    say "shortcut: $f (no autostart added)"
  else
    d="$HOME/.local/share/applications"; mkdir -p "$d"
    f="$d/orcpixels.desktop"
    cat > "$f" <<DESK
[Desktop Entry]
Type=Application
Name=OrcPixels
Comment=OrcPixels LED sign studio
Exec="$ROOT/tools/launch_orcpixels.sh"
Path=$ROOT
Terminal=false
Categories=Utility;
DESK
    chmod +x "$f"
    if [ -d "$HOME/Desktop" ]; then cp "$f" "$HOME/Desktop/orcpixels.desktop"; chmod +x "$HOME/Desktop/orcpixels.desktop"; fi
    say "shortcut: $f (no autostart added)"
  fi
fi

cat <<TXT

OrcPixels is installed. First run:
  1. Start it:   .venv/bin/python -m orcpixels serve      (or tools/launch_orcpixels.sh)
  2. Open        http://127.0.0.1:8765
  3. Power on your iPixel sign, then Scan / add display in the status panel and pick it.
     (Linux needs BlueZ; your user may need to be in the 'bluetooth' group.)
  4. Optional: Live tab > feed settings: home latitude/longitude, time zone, stations.
  Tests: .venv/bin/python -m pytest -q
TXT
