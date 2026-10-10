"""Contact sheets for the Live tab categories (milestone 2), from the recorded fixtures.

  categories_overview.png       every category, every screen at 64x16 (first frame)
  categories_<id>.png           one per category: 64x16 + 64x64, 3 frames each
  categories_adaptive.png       Sports + Entertainment at 16x16 / 64x32 / 256x16 / 96x24

    python tools/categories_sheet.py [outdir]
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("ORCPIXELS_NO_UPDATE_CHECK", "1")

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from orcpixels import categories as CAT  # noqa: E402
from orcpixels import livedata_screens as SC  # noqa: E402
from orcpixels import place as PLACE  # noqa: E402
from orcpixels.loopmetric import loop_metric_frames  # noqa: E402
from orcpixels.sources import livefeeds as LF  # noqa: E402
from orcpixels.studio import led_frame  # noqa: E402

T_FX = 1791689400.0          # 2026-10-10 20:30 PT (the fixture capture day)
BG, FG, MUTE = (14, 16, 20), (225, 230, 238), (130, 138, 150)


def font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


def feeds():
    import led64_sheet
    with mock.patch("time.time", return_value=T_FX):
        fs, _ = led64_sheet.fixture_feeds()
    return fs


def frames(key, f, size, n_show):
    n = SC.loop_len(key, f, size)
    allf = [SC.render(key, f, i, T_FX, size=size) for i in range(n)]
    ok = n == 1 or loop_metric_frames(allf)["ok"]
    pick = [allf[int(i * n / n_show)] for i in range(min(n_show, n))]
    return pick, n, ok


def led(img, scale):
    return led_frame(img, scale).convert("RGB")


def sheet_rows(rows, title, path, scale_for):
    """rows: [(label, sub, [images])] -> one PNG."""
    f1, f2, f3 = font(26), font(15), font(13)
    pad, lab = 14, 270
    tiles = [[led(im, scale_for(im)) for im in ims] for _l, _s, ims in rows]
    rw = [sum(t.width + 8 for t in ts) for ts in tiles]
    W = lab + max(rw + [400]) + pad * 2
    H = 60 + sum(max(t.height for t in ts) + 12 for ts in tiles) + pad
    out = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(out)
    d.text((pad, 14), title, font=f1, fill=FG)
    y = 60
    for (label, sub, _ims), ts in zip(rows, tiles):
        h = max(t.height for t in ts)
        d.text((pad, y + 2), label, font=f2, fill=FG)
        d.multiline_text((pad, y + 22), sub, font=f3, fill=MUTE, spacing=2)
        x = lab
        for t in ts:
            out.paste(t, (x, y))
            x += t.width + 8
        y += h + 12
    out.save(path)
    return path


def main(outdir: Path):
    from tests.conftest import SAMPLE, SAMPLE_SETTINGS
    outdir.mkdir(parents=True, exist_ok=True)
    orig = LF._setting
    with mock.patch.object(PLACE, "OVERRIDE", dict(SAMPLE)), mock.patch.dict(os.environ, {"ORCPIXELS_TZ": "us-pacific"}), \
            mock.patch.object(LF, "_setting", lambda k: orig(k) or SAMPLE_SETTINGS.get(k)):
        fs = feeds()
        made = []
        groups = CAT.grouped(SC.SCREENS)
        # overview
        rows = []
        for c, keys in groups:
            for i in range(0, len(keys), 6):
                part = keys[i:i + 6]
                rows.append((c.title if i == 0 else "", f"{len(keys)} screens" if i == 0 else "",
                             [SC.render(k, fs[k], 0, T_FX) for k in part]))
        made.append(sheet_rows(rows, f"Live tab categories: {len(SC.SCREENS)} screens in {len(groups)} categories (64x16)",
                               outdir / "categories_overview.png", lambda im: 4))
        for c, keys in groups:
            rows = []
            for k in keys:
                a, n1, ok1 = frames(k, fs[k], (64, 16), 3)
                b, n2, ok2 = frames(k, fs[k], (64, 64), 3)
                rows.append((fs[k].title[:30], f"{k}\n64x16 {n1}f {'seamless' if ok1 else 'JUMP'}\n64x64 {n2}f {'seamless' if ok2 else 'JUMP'}", a + b))
            made.append(sheet_rows(rows, f"{c.title}: {c.blurb}", outdir / f"categories_{c.id}.png",
                                   lambda im: 4 if im.height <= 16 else 3))
        rows = []
        for k, s in SC.SCREENS.items():
            if not getattr(s, "adaptive", False):
                continue
            ims, subs = [], []
            for size in ((16, 16), (64, 32), (256, 16), (96, 24)):
                fr, n, ok = frames(k, fs[k], size, 1)
                ims += fr
                subs.append(f"{size[0]}x{size[1]} {n}f {'ok' if ok else 'JUMP'}")
            rows.append((fs[k].title[:30], k + "\n" + "  ".join(subs[:2]) + "\n" + "  ".join(subs[2:]), ims))
        made.append(sheet_rows(rows, "Sports + Entertainment: adaptive sizes 16x16, 64x32, 256x16, 96x24",
                               outdir / "categories_adaptive.png", lambda im: 3))
    for p in made:
        print(p)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "img" / "categories")
