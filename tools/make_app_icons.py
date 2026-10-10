"""Generate 16x16 app icons for the AI status frame (assets/icons/apps/*_16.png)
plus 20x previews. Brand-ish, simplified for a 64x16 LED panel.

  python tools/make_app_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets" / "icons" / "apps"


def codex() -> Image.Image:
    """Codex desktop: blue->violet rounded tile with a white '>_' prompt."""
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    px = img.load()
    for y in range(16):
        for x in range(16):
            if (x in (0, 15) and y in (0, 15)) or (x in (0, 15) and y in (1, 14)) or (y in (0, 15) and x in (1, 14)):
                continue
            t = (x + y) / 30
            px[x, y] = (int(60 + 90 * t), int(90 - 40 * t), int(255 - 30 * t), 255)
    d = ImageDraw.Draw(img)
    w = (255, 255, 255, 255)
    d.line([(3, 4), (6, 7), (3, 10)], fill=w, width=1)
    d.line([(4, 4), (7, 7), (4, 10)], fill=w, width=1)
    d.line([(8, 11), (12, 11)], fill=w, width=1)
    d.line([(8, 12), (12, 12)], fill=w, width=1)
    return img


CLAUDE = [
    "................",
    ".......##.......",
    "..#....##....#..",
    "...#...##...#...",
    "....#..##..#....",
    ".....#.##.#.....",
    "......####......",
    ".##############.",
    ".##############.",
    "......####......",
    ".....#.##.#.....",
    "....#..##..#....",
    "...#...##...#...",
    "..#....##....#..",
    ".......##.......",
    "................",
]

GROK = [
    "................",
    "............##..",
    "....#####..##...",
    "..##.....###....",
    "..#.....###.....",
    ".#.....###...#..",
    ".#....###....#..",
    ".#...###.....#..",
    ".#..###......#..",
    ".#.###.......#..",
    "..###.......#...",
    ".###.......#....",
    "###...#####.....",
    "##..............",
    "................",
    "................",
]


def _bitmap(rows, color) -> Image.Image:
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == "#":
                img.putpixel((x, y), color)
    return img


def claude() -> Image.Image:
    """Claude: terracotta 8-ray asterisk/starburst."""
    return _bitmap(CLAUDE, (217, 119, 87, 255))


def grok() -> Image.Image:
    """Grok: white broken ring with a bold diagonal slash."""
    return _bitmap(GROK, (235, 235, 235, 255))


def cursor() -> Image.Image:
    """Cursor: grey isometric cube with a bright top face."""
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(8, 1), (14, 4), (8, 7), (2, 4)], fill=(235, 235, 235, 255))
    d.polygon([(2, 4), (8, 7), (8, 15), (2, 12)], fill=(120, 120, 120, 255))
    d.polygon([(8, 7), (14, 4), (14, 12), (8, 15)], fill=(70, 70, 70, 255))
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in [("codex", codex), ("claude", claude), ("grok", grok), ("cursor", cursor)]:
        img = fn()
        img.save(OUT / f"{name}_16.png")
        prev = Image.new("RGB", (16, 16))
        prev.paste(img, (0, 0), img)
        prev.resize((320, 320), Image.Resampling.NEAREST).save(OUT / f"{name}_16_preview_20x.png")
        print("wrote", OUT / f"{name}_16.png")


if __name__ == "__main__":
    main()
