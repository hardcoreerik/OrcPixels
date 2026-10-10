"""Contact sheets of presets64/ grouped by family (used by tools/led64_sheet.py)."""
from __future__ import annotations

import json
from pathlib import Path

from orcpixels.render import gif_frames

ROOT = Path(__file__).resolve().parents[1]
GROUPS = [("orcsdr", "OrcSDR + splash", lambda m: not m.get("family")),
          ("orco", "Oregon Radio Co (30)", lambda m: m.get("family") == "orc_co"),
          ("personal1", "Tailored: amb / code / hw", lambda m: m.get("family") in ("amb", "code", "hw")),
          ("personal2", "Tailored: orc / proj / rf", lambda m: m.get("family") in ("orc", "proj", "rf"))]


def load(name):
    m = json.loads((ROOT / "presets64" / f"{name}.json").read_text(encoding="utf-8"))
    data = (ROOT / "presets64" / m["file"]).read_bytes()
    if m["kind"] == "gif":
        frames, _ = gif_frames(data)
    else:
        from PIL import Image
        import io
        frames = [Image.open(io.BytesIO(data)).convert("RGB")]
    return m, frames


def rows_for(pred, nframes):
    rows = []
    for p in sorted((ROOT / "presets64").glob("*.json")):
        m, frames = load(p.stem)
        if not pred(m):
            continue
        n = len(frames)
        idx = sorted({int(i * n / nframes) for i in range(min(n, nframes))})
        sub = f"{m.get('title') or ''}\n{m['kind']} {n}f {m['bytes'] / 1024:.1f} KB\nloop: {m.get('loop') or 'still'}"
        rows.append((m["name"], sub, [frames[i] for i in idx]))
    return rows


def write(out: Path, nframes, scale, sheet):
    paths = []
    for key, title, pred in GROUPS:
        rows = rows_for(pred, nframes)
        if not rows:
            continue
        for part in range(0, len(rows), 20):
            chunk = rows[part:part + 20]
            suffix = "" if len(rows) <= 20 else f"_{part // 20 + 1}"
            paths.append(sheet(chunk, out / f"led64x64_previews_presets_{key}{suffix}.png", scale,
                               f"64x64 presets: {title}" + (f" ({part // 20 + 1})" if suffix else "")))
    return paths
