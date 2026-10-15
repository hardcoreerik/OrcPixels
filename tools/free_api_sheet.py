"""Contact sheet of the free-API Live sources (2026-10-14), grouped by category, 64x16 and
64x64 side by side, rendered from the recorded fixtures (no network, nothing sent to a sign).

  python tools/free_api_sheet.py [--out /workspace/uploads/free_api_live_previews.png] [--gif]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image, ImageDraw  # noqa: E402

from orcpixels import categories as CAT  # noqa: E402
from orcpixels import livedata_screens as SC  # noqa: E402
from orcpixels import place as PLACE  # noqa: E402
from orcpixels.sources import livefeeds as LF  # noqa: E402
from orcpixels.studio import led_frame  # noqa: E402

MODULES = ("world_weather", "space_more", "markets_more", "ham_spots", "motorsport", "games", "local_more",
           "status_board", "live_catalog")
T_NOW = 1792046700.0          # 2026-10-14 23:45 PT, when the fixtures were captured


def new_keys():
    return [k for k, s in SC.SCREENS.items() if type(s).__module__.split(".")[-1] in MODULES]


def feeds_for(keys):
    from tests.conftest import SAMPLE
    from tests.test_livedata import FX, ROUTES

    def http(url, headers, timeout):
        for k, f in ROUTES.items():
            if k in url:
                return (FX / f).read_bytes()
        raise OSError("no fixture for " + url)
    PLACE.OVERRIDE.update(SAMPLE)
    os.environ["ORCPIXELS_TZ"] = "us-pacific"
    LF.HTTP = http
    fs = {k: SC.SCREENS[k].feed() for k in keys}

    async def go():
        await asyncio.gather(*(f.refresh(force=True) for f in fs.values()))
    with mock.patch("time.time", return_value=T_NOW):
        asyncio.run(go())
    return fs


def frames(k, f, size, n_s):
    n = SC.loop_len(k, f, size)
    idx = sorted({int(i * n / n_s) for i in range(min(n, n_s))})
    with mock.patch("time.time", return_value=T_NOW):
        return [SC.render(k, f, i, T_NOW, size=size) for i in idx], n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/workspace/uploads/free_api_live_previews.png")
    ap.add_argument("--gif", action="store_true")
    ap.add_argument("--scale", type=int, default=3)
    a = ap.parse_args()
    keys = new_keys()
    fs = feeds_for(keys)
    by = {}
    for k in keys:
        by.setdefault(CAT.of(k, SC.SCREENS[k]), []).append(k)
    sc = a.scale
    lw, pad = 250, 8
    row_h = 64 * sc + pad
    strip_w, sq = 64 * sc, 64 * sc
    cols = 3
    W = lw + cols * (strip_w + pad) + sq + pad * 2 + 10
    rows = []
    for c in CAT.ordered():
        if c.id in by:
            rows.append(("cat", c))
            rows += [("scr", k) for k in by[c.id]]
    H = 50 + sum(34 if t == "cat" else row_h for t, _ in rows)
    img = Image.new("RGB", (W, H), (14, 16, 20))
    d = ImageDraw.Draw(img)
    from led64_sheet import font
    d.text((pad, 10), f"OrcPixels free-API Live sources: {len(keys)} screens (64x16 frames across the loop | 64x64)",
           fill=(230, 235, 240), font=font(22))
    y = 50
    for t, v in rows:
        if t == "cat":
            d.text((pad, y + 6), v.title.upper(), fill=v.color, font=font(20))
            d.line((pad, y + 30, W - pad, y + 30), fill=tuple(int(x * .4) for x in v.color))
            y += 34
            continue
        k, f = v, fs[v]
        d.text((pad, y + 4), SC.SCREENS[k].short if isinstance(SC.SCREENS[k].short, str) else k, fill=(235, 238, 242), font=font(16))
        sub = f"{k}\n{f.source[:34]}\nevery {int(f.poll_s // 60)} min" + (f"\nERR {f.error[:30]}" if f.error else "")
        for j, ln in enumerate(sub.split("\n")):
            d.text((pad, y + 26 + 15 * j), ln, fill=(150, 156, 170), font=font(12))
        fr16, n16 = frames(k, f, (64, 16), cols * 3)
        for j, fr in enumerate(fr16[:cols * 3]):
            cx, cy = j % cols, j // cols
            img.paste(led_frame(fr, sc), (lw + cx * (strip_w + pad), y + cy * (16 * sc + 4)))
        fr64, n64 = frames(k, f, (64, 64), 1)
        img.paste(led_frame(fr64[0], sc), (lw + cols * (strip_w + pad) + pad, y))
        y += row_h
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    img.save(a.out)
    print("wrote", a.out, img.size)
    if a.gif:
        gif = str(Path(a.out).with_suffix(".gif"))
        sel = keys
        tiles = []
        cw, ch = 64 * 2 + 4, 16 * 2 + 4 + 64 * 2 + 4
        ncol = 8
        nrow = (len(sel) + ncol - 1) // ncol
        out = []
        for i in range(16):
            fimg = Image.new("RGB", (ncol * (cw + 4), nrow * (ch + 4)), (14, 16, 20))
            for j, k in enumerate(sel):
                f = fs[k]
                with mock.patch("time.time", return_value=T_NOW):
                    a16 = SC.render(k, f, i, T_NOW, size=(64, 16))
                    a64 = SC.render(k, f, i, T_NOW, size=(64, 64))
                x, yy = (j % ncol) * (cw + 4), (j // ncol) * (ch + 4)
                fimg.paste(led_frame(a16, 2), (x, yy))
                fimg.paste(led_frame(a64, 2), (x, yy + 16 * 2 + 4))
            out.append(fimg.quantize(colors=255))
        out[0].save(gif, save_all=True, append_images=out[1:], duration=700, loop=0)
        print("wrote", gif)
        _ = tiles
    _ = time


if __name__ == "__main__":
    main()
