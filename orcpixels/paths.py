"""Where OrcPixels keeps things.

APP   read-only program files (package, presets, assets, profiles, shipped config defaults).
      In a source checkout this is the repo; in the installed build it is the install
      folder (Program Files\\OrcPixels\\_internal).
DATA  per-user writable data: config/ (settings, displays, playlists, local overrides),
      user presets, out/. Source checkout: the repo itself (unchanged dev behaviour).
      Installed build: %APPDATA%\\OrcPixels. ORCPIXELS_HOME overrides both.
LIBRARY  imported media; setting library_dir, default Documents\\OrcPixels Library
      (installed) or <repo>/library (checkout).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
FROZEN = bool(getattr(sys, "frozen", False))


def _data() -> Path:
    env = os.environ.get("ORCPIXELS_HOME")
    if env:
        return Path(env).expanduser()
    if FROZEN:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "OrcPixels"
    return APP


DATA = _data()
CONFIG = DATA / "config"
OUT = DATA / "out"
SEPARATE = DATA.resolve() != APP.resolve()      # user data lives outside the program folder


def config_file(name: str) -> Path:
    """User copy if present, else the shipped default (APP/config/name)."""
    p = CONFIG / name
    return p if p.exists() or not SEPARATE else APP / "config" / name


def user_presets(sub: str) -> Path | None:
    """Writable preset folder when program files are read-only (installed build)."""
    return DATA / sub if SEPARATE else None


def documents() -> Path:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:    # CSIDL_PERSONAL
                return Path(buf.value)
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / "Documents"


def library() -> Path:
    try:
        from . import settings as S
        v = S.load().get("library_dir")
    except Exception:  # noqa: BLE001
        v = None
    if v:
        return Path(str(v)).expanduser()
    return documents() / "OrcPixels Library" if SEPARATE else APP / "library"
