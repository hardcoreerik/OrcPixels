"""Rendering any preset at any size + the compatibility rating (docs/DESIGN.md).

    render_preset(name, w, h, strategy) -> (frames, durations_ms, rating)
    rating_for(name, w, h)               -> native | adaptive | scaled | fallback

Adaptive scenes (scenes.py) lay themselves out; fixed presets render their golden 64x16 /
64x64 asset at native size and are Scaled or Fallback (contain / crop) elsewhere.
"""
from __future__ import annotations

from . import layout as L
from . import scenes as SCN

MASTERS = ((64, 16), (64, 64))


def native_sizes(name: str) -> list[tuple[int, int]]:
    from . import presets as PR
    out = []
    if (PR.DIR / f"{name}.json").exists() or any((d / f"{name}.json").exists() for d in PR._dirs((64, 16))[1:]):
        out.append((64, 16))
    if (PR.DIR64 / f"{name}.json").exists() or any((d / f"{name}.json").exists() for d in PR._dirs((64, 64))[1:]):
        out.append((64, 64))
    return out


def rating_for(name: str, w: int, h: int) -> str:
    if name in SCN.SCENES:
        return "adaptive"
    return L.rate(native_sizes(name) or [(64, 16)], w, h)


def master_frames(name: str, size: tuple[int, int]):
    from . import presets as PR
    from .render import gif_frames
    from PIL import Image
    meta = PR.get(name, size)
    path = PR.file_path(meta)
    if meta.get("kind") == "gif":
        fr, durs = gif_frames(path.read_bytes())
        return [f.convert("RGB") for f in fr], list(durs)
    return [Image.open(path).convert("RGB")], [1000]


def render_preset(name: str, w: int, h: int, strategy: str = "adaptive"):
    if strategy not in L.STRATEGIES:
        raise ValueError(f"strategy must be one of {L.STRATEGIES}")
    if name in SCN.SCENES:
        if strategy == "adaptive":
            fr, durs = SCN.frames(name, w, h)
            return fr, durs, "adaptive"
        m = L.best_master(MASTERS, w, h)
        fr, durs = SCN.frames(name, *m)
        out, _ = L.place(fr, [m], w, h, strategy)
        return out, durs, "fallback" if (w, h) != m else "adaptive"
    ns = native_sizes(name)
    if not ns:
        raise KeyError(f"unknown preset {name!r}")
    m = (w, h) if (w, h) in ns else L.best_master(ns, w, h)
    fr, durs = master_frames(name, m)
    if fr[0].size != m:                      # 64x64 request fell back to the 64x16 file
        m = fr[0].size
    out, rating = L.place(fr, [m], w, h, strategy)
    return out, durs, rating


def names() -> list[dict]:
    from . import presets as PR
    out = [{"name": n, "title": s.title, "adaptive": True, "notes": s.family_notes} for n, s in SCN.SCENES.items()]
    out += [{"name": p["name"], "title": p.get("title") or p["name"], "adaptive": False} for p in PR.list_presets((64, 16))]
    return out
