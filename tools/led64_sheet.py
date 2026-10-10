"""Contact sheets for the 64x64 matrix (nothing is sent to any sign).

  python tools/led64_sheet.py [--outdir /workspace/uploads] [--frames 4] [--scale 3]

Writes led64x64_previews_live.png (all Live Data screens + OrcSDR Live + AI buddy) and
led64x64_previews_presets_<family>.png (the curated presets64/ set), plus
led64x64_previews.png (an overview: one frame of everything). Live Data is rendered from
the trimmed real responses in tests/fixtures/livedata (no network) and the real FT8
capture; frames are sampled across each loop.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from orcpixels import livedata_screens as SC  # noqa: E402
from orcpixels.sources import livefeeds as LF  # noqa: E402
from orcpixels.studio import led_frame  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "livedata"


def font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def fixture_feeds():
    from tests.test_livedata import ROUTES

    def http(url, headers, timeout):
        for k, f in ROUTES.items():
            if k in url:
                return (FX / f).read_bytes()
        raise OSError("no fixture for " + url)
    LF.HTTP = http
    feeds = SC.make_feeds()

    async def go():
        await asyncio.gather(*(f.refresh() for k, f in feeds.items() if not k.startswith("ft8_")))
    asyncio.run(go())
    import ft8_sheet
    text = (FX / "ft8_serial.txt").read_text(encoding="utf-8")
    v = ft8_sheet.build(text, mycall="K7ABC", later=True)
    for k in feeds:
        if k.startswith("ft8_"):
            feeds[k] = ft8_sheet.feed_with(v)
    return feeds, v


def sample(n, frames):
    if n <= frames:
        return list(range(n))
    return sorted({int(i * n / frames) for i in range(frames)})


def sheet(rows, out, scale, title):
    """rows: [(label, sublabel, [frames 64x64])]"""
    fw = 64 * scale
    maxf = max(len(r[2]) for r in rows)
    lw, pad = 230, 10
    rh = fw + pad
    W = lw + maxf * (fw + pad) + pad
    Hh = 40 + len(rows) * rh + pad
    img = Image.new("RGB", (W, Hh), (14, 16, 20))
    d = ImageDraw.Draw(img)
    d.text((pad, 8), title, fill=(230, 235, 240), font=font(20))
    f1, f2 = font(16), font(12)
    for i, (lab, sub, frs) in enumerate(rows):
        y = 40 + i * rh
        d.text((pad, y + 6), lab, fill=(235, 238, 242), font=f1)
        for j, line in enumerate(sub.split("\n")[:6]):
            d.text((pad, y + 28 + 15 * j), line, fill=(150, 156, 170), font=f2)
        for j, f in enumerate(frs):
            img.paste(led_frame(f, scale), (lw + j * (fw + pad), y))
    img.save(out)
    return out


def when(key, f, now):
    """Fixture data is from a fixed date: show tides at the time the fixture covers."""
    if key == "tides" and f.value and f.value.get("events"):
        return f.value["events"][0]["t"] + 3600
    return now


def live_rows(frames):
    from orcpixels import live64
    from orcpixels.loopmetric import loop_metric_frames
    feeds, _ = fixture_feeds()
    now = time.time()
    rows = []
    for key in SC.SCREENS:
        f = feeds[key]
        t = when(key, f, now)
        n = live64.loop_len(key, f)
        allf = [SC.render(key, f, i, t, size=(64, 64)) for i in range(n)]
        m = loop_metric_frames(allf)
        rows.append((key, f"{SC.SCREENS[key].group}\nloop {n} frames: {'seamless' if m['ok'] else 'JUMP'}",
                     [allf[i] for i in sample(n, frames)]))
    return rows


def extra_rows():
    """OrcSDR Live (64x64 panadapter + waterfall) and the AI status / buddy frames."""
    import math
    from orcpixels import ai_status as AI
    from orcpixels.orcsdr_live import render_frame64
    from orcpixels.sources import ai_watcher as AW
    from orcpixels.sources import buddy as BD
    from orcpixels.sources.orcsdr import OrcSDRStatus
    rows = []
    st = OrcSDRStatus.from_json({"mode": "FM", "frequency_hz": 96_100_000, "signal_dbfs": -22.4,
                                 "receiving": True, "rtl_ready": True})
    hist, frs = [], []
    for k in range(24):
        b = [max(0.03, min(1.0, 0.12 + 0.8 * math.exp(-((i - 32) / 3.0) ** 2) * (0.85 + 0.15 * math.sin(k + i))
                           + 0.35 * math.exp(-((i - 12) / 1.5) ** 2) + 0.05 * math.sin(i * 1.7 + k)))
             for i in range(64)]
        hist.insert(0, b)
        frs.append(render_frame64(st, b, [min(1.0, v + 0.08) for v in b], hist[:24], waterfall=True))
    rows.append(("orcsdr_live", "OrcSDR Live (FM 96.1)\npanadapter + waterfall\n(frames of a live run)", [frs[i] for i in (3, 9, 15, 23)]))
    rows.append(("orcsdr_live off", "OrcSDR offline / waiting",
                 [render_frame64(None, None, error="x"), render_frame64(None, None)]))
    det = AW.Detection(app="claude", app_display="Claude", via="desktop", folder="C:/src/OrcPixels",
                       git=AW.GitInfo(repo="OrcPixels", branch="master", toplevel="C:/src/OrcPixels",
                                      main_toplevel="C:/src/OrcPixels", is_worktree=False))
    det2 = AW.Detection(app="codex", app_display="Codex", via="terminal", folder="C:/src/OrcSDR-Temp/ft8",
                        git=AW.GitInfo(repo="OrcSDR", branch="claude/ft8-ticker-feed", toplevel="C:/src/OrcSDR-Temp/ft8",
                                       main_toplevel="C:/src/OrcSDR", is_worktree=True))
    rows.append(("ai_status", "AI Auto status (64x64)\nClaude / Codex worktree / none",
                 [AI.render_status(det, (64, 64))[0][0], AI.render_status(det2, (64, 64))[0][0],
                  AI.render_status(None, (64, 64))[0][0]]))
    rows.append(("ai_buddy", "AI buddy (64x64 image)\nthinking / editing / running / done",
                 [AI.render_buddy64(BD.Activity(state=s_, text=t_)) for s_, t_ in
                  (("thinking", "planning the 64x64 layouts"), ("editing", "live64.py header()"),
                   ("running", "pytest -q tests/"), ("done", "437 passed"))]))
    return rows


def overview(out, scale=2):
    """One frame of everything in the curated 64x64 set on a single page."""
    import json as _json
    import led64_presets_sheet as PS
    tiles = []
    for _, title, pred in PS.GROUPS:
        for row in PS.rows_for(pred, 3):
            tiles.append((row[0], row[2][min(1, len(row[2]) - 1)]))
    for key, _, frs in live_rows(2):
        tiles.append((key, frs[-1]))
    for key, _, frs in extra_rows():
        tiles.append((key, frs[0]))
    cols = 12
    fw = 64 * scale
    cw, ch = fw + 8, fw + 22
    rows_n = (len(tiles) + cols - 1) // cols
    img = Image.new("RGB", (cols * cw + 8, 44 + rows_n * ch), (14, 16, 20))
    d = ImageDraw.Draw(img)
    d.text((8, 10), f"OrcPixels curated 64x64 set: {len(tiles)} layouts (presets, Live Data, FT8, OrcSDR Live, AI)",
           fill=(230, 235, 240), font=font(20))
    f2 = font(11)
    for i, (lab, fr) in enumerate(tiles):
        x, y = 8 + (i % cols) * cw, 44 + (i // cols) * ch
        img.paste(led_frame(fr, scale), (x, y))
        d.text((x, y + fw + 3), lab[:22], fill=(160, 166, 180), font=f2)
    img.save(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/workspace/uploads")
    ap.add_argument("--frames", type=int, default=4)
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--only", default="all", choices=("all", "live", "presets"))
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    if a.only in ("all", "live"):
        rows = live_rows(a.frames)
        half = (len(rows) + 1) // 2
        sheet(rows[:half], out / "led64x64_previews_live1.png", a.scale, "64x64 Live Data (1/2): weather, sky, radio, markets")
        sheet(rows[half:], out / "led64x64_previews_live2.png", a.scale, "64x64 Live Data (2/2): markets, news, FT8/FT4")
        sheet(extra_rows(), out / "led64x64_previews_orcsdr_ai.png", a.scale, "64x64 OrcSDR Live + AI status / buddy")
    if a.only in ("all", "presets"):
        import led64_presets_sheet
        led64_presets_sheet.write(out, a.frames, a.scale, sheet)
    if a.only == "all":
        overview(out / "led64x64_previews.png")


if __name__ == "__main__":
    main()
