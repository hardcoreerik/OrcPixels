"""Schematic illustrations for docs/DESIGN.md (labelled SCHEMATIC; not renderer output).
    python tools/design_schematics.py   -> docs/img/schematic_*.png"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "img"
BG, FG, MUT, GRN, BLU, AMB = (14, 16, 20), (230, 235, 240), (140, 146, 160), (40, 210, 90), (60, 150, 255), (255, 168, 20)


def font(n):
    for f in ("DejaVuSans.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(f, n)
        except OSError:
            pass
    return ImageFont.load_default()


def tag(d, W):
    d.text((W - 110, 6), "SCHEMATIC", fill=(200, 190, 110), font=font(13))


def families():
    from orcpixels import layout as L
    rows = [("1:1 square", [(16, 16), (32, 32), (64, 64), (128, 128)]), ("2:1 wide", [(32, 16), (64, 32), (128, 64)]),
            ("4:1 ultrawide", [(64, 16), (128, 32), (256, 64)]), ("8:1+ strip", [(128, 16), (256, 32), (320, 16)]),
            ("portrait", [(16, 32), (32, 64), (16, 64)])]
    s = 1.0
    W, H = 900, 620
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    tag(d, W)
    d.text((14, 8), "Aspect families (design guides, not hard buckets)", fill=FG, font=font(17))
    y = 44
    for name, sizes in rows:
        d.text((14, y + 4), name, fill=FG, font=font(15))
        x = 150
        rh = 0
        for w, h in sizes:
            sc = s if max(w, h) <= 128 else 128 / max(w, h) * 1.4
            ww, hh = int(w * sc), int(h * sc)
            d.rectangle([x, y, x + ww, y + hh], outline=GRN)
            d.text((x, y + hh + 3), f"{w}x{h} {L.tier(w, h)}", fill=MUT, font=font(11))
            x += ww + 30
            rh = max(rh, hh)
        y += rh + 30
    img.save(OUT / "schematic_families.png", optimize=True)


def regions():
    W, H = 900, 300
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    tag(d, W)
    d.text((14, 8), "Responsive regions: the ADS-B radar scene at four shapes", fill=FG, font=font(17))
    x = 14
    for (w, h), parts in (((64, 64), [("radar", (0, 0, 64, 64), GRN)]),
                          ((64, 32), [("radar", (0, 0, 32, 32), GRN), ("list", (34, 0, 64, 32), BLU)]),
                          ((96, 24), [("radar", (0, 0, 24, 24), GRN), ("header + rows", (26, 0, 96, 24), BLU)]),
                          ((256, 16), [("radar", (0, 0, 16, 16), GRN), ("header", (18, 0, 50, 16), AMB),
                                       ("marquee", (50, 0, 256, 16), BLU)])):
        sc = min(3.0, 200 / w)
        y0 = 50
        d.rectangle([x, y0, x + w * sc, y0 + h * sc], outline=MUT)
        for lab, (a, b, c, e), col in parts:
            d.rectangle([x + a * sc + 1, y0 + b * sc + 1, x + c * sc - 1, y0 + e * sc - 1], outline=col)
            d.text((x + a * sc + 4, y0 + b * sc + 3), lab, fill=col, font=font(11))
        d.text((x, y0 + h * sc + 6), f"{w}x{h}", fill=FG, font=font(13))
        x += int(w * sc) + 26
    img.save(OUT / "schematic_regions.png", optimize=True)


def topology():
    W, H = 900, 330
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    tag(d, W)
    d.text((14, 8), "Topology: viewing size vs physical mounting, wiring order", fill=FG, font=font(17))
    # viewing vs rotation
    d.rectangle([20, 60, 60, 220], outline=GRN)
    d.text((14, 228), "viewing 16x64\n(width 16, height 64)", fill=FG, font=font(12))
    d.text((90, 130), "rotation 90 ->", fill=AMB, font=font(13))
    d.rectangle([200, 120, 360, 160], outline=BLU)
    d.text((200, 168), "physical panel 64x16", fill=FG, font=font(12))
    # wiring
    for k, (lab, serp) in enumerate((("progressive", False), ("serpentine", True))):
        ox, oy, n = 430 + k * 230, 70, 6
        for r in range(4):
            pts = [(ox + c * 30, oy + r * 40) for c in range(n)]
            if serp and r % 2:
                pts = pts[::-1]
            for p in pts:
                d.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], outline=GRN)
            d.line(pts, fill=BLU)
            if r < 3:
                nxt = (ox + (0 if not serp or r % 2 else (n - 1) * 30), oy + (r + 1) * 40)
                d.line([pts[-1], nxt], fill=(60, 80, 110))
        d.text((ox, oy - 26), lab + " (start top-left)", fill=FG, font=font(12))
    img.save(OUT / "schematic_topology.png", optimize=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    families(); regions(); topology()
    print("ok")
