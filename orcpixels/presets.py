"""Named, frozen display presets: presets/<name>.json + the exact rendered file.

A preset stores the upright (as-viewed) frame; rotation (settings.rotate_180)
is applied at send time by SignClient, so presets never need re-rendering.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "presets"            # 64x16 (the original set)
DIR64 = ROOT / "presets64"        # 64x64 curated set: same names/families, own layouts
SIZE_DIRS = {(64, 64): "DIR64"}


def dir_for(size: tuple[int, int] | None = None) -> Path:
    """Preset folder for a panel size (default: the display this request/task is for)."""
    if size is None:
        from . import displays as D
        size = D.size()
    attr = SIZE_DIRS.get(tuple(size))
    return globals()[attr] if attr else DIR


def list_presets(size: tuple[int, int] | None = None) -> list[dict]:
    out = []
    d = dir_for(size)
    for p in sorted(d.glob("*.json")):
        meta = json.loads(p.read_text(encoding="utf-8"))
        meta.setdefault("name", p.stem)
        out.append(meta)
    return out


def get(name: str, size: tuple[int, int] | None = None) -> dict:
    """Size-specific preset; a name missing from the 64x64 set falls back to the 64x16
    file (the sign letterboxes it)."""
    d = dir_for(size)
    p = d / f"{name}.json"
    if not p.exists() and d != DIR and (DIR / f"{name}.json").exists():
        d, p = DIR, DIR / f"{name}.json"
    if not p.exists():
        raise KeyError(f"unknown preset {name!r}; have: {[m['name'] for m in list_presets(size)]}")
    meta = json.loads(p.read_text(encoding="utf-8"))
    meta.setdefault("name", name)
    meta["_dir"] = str(d)
    return meta


def file_path(meta: dict) -> Path:
    return Path(meta["_dir"]) / meta["file"] if meta.get("_dir") else dir_for() / meta["file"]


def preview_image(name: str) -> Image.Image:
    return Image.open(file_path(get(name))).convert("RGB")


async def play(sign, name: str) -> dict:
    from .activity import labelled
    meta = get(name)
    path = file_path(meta)
    with labelled(f"preset {meta['name']}"):
        if meta.get("kind", "image") == "gif":
            await sign.send_gif(path.read_bytes())
        else:
            await sign.send_image(Image.open(path))
    sign.resume_hint = {"kind": "preset", "name": meta["name"]}   # re-shown after a reconnect
    if hasattr(sign, "set_showing"):
        sign.set_showing("preset", meta["name"], title=meta.get("title"))
    return meta
