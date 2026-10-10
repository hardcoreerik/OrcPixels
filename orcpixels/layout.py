"""Resolution-independent layout (docs/DESIGN.md).

Three independent properties of a display:
  1. aspect ratio      -> composition   (Ctx.family: square / wide / ultrawide / strip / portrait*)
  2. pixel dimensions  -> level of detail (Ctx.tier: tiny / small / medium / large / xl)
  3. hardware topology -> how the finished image maps to LEDs (drivers.base.Layout, profiles)

A display's width x height is its *viewing* size (how people look at it); the profile's
`rotation` is how the panel is physically mounted and is applied by the driver afterwards.

Normal rendering is adaptive: a scene (scenes.py) gets a Ctx, asks it for regions
(split / stack / inset) and lays itself out for the space it has, like responsive web
design. Families are design guides and fallbacks, not hard buckets: a 96x32 (3:1) panel
is laid out from its real regions, not forced to 2:1 or 4:1.

Existing hand-made 64x16 and 64x64 assets are golden references and are never
re-rendered; at other sizes they are shown Scaled (integer upscale of the same aspect)
or Fallback (contain = letterbox, crop = fill and crop).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image

STRATEGIES = ("adaptive", "contain", "crop")
RATINGS = ("native", "adaptive", "scaled", "fallback")
FAMILY_LABEL = {"square": "Square 1:1", "wide": "Wide 2:1", "ultrawide": "Ultrawide 4:1", "strip": "Strip 8:1+",
                "portrait": "Portrait", "tall": "Tall portrait 1:4+"}


def classify(w: int, h: int) -> str:
    """Aspect family of a viewing size (design guide; layouts use the real regions)."""
    r = w / h
    if r >= 1:
        if r < 1.5:
            return "square"
        if r < 3.0:
            return "wide"
        if r < 6.0:
            return "ultrawide"
        return "strip"
    if r > 1 / 1.5:
        return "square"
    return "portrait" if r > 1 / 3.0 else "tall"


def tier(w: int, h: int) -> str:
    """Detail tier from pixel dimensions: short side mostly sets text rows, area sets
    icon complexity / history length."""
    s, area = min(w, h), w * h
    if s < 12 or area <= 256:
        return "tiny"
    if s < 24:
        return "small"
    if s < 48:
        return "medium"
    if s < 96:
        return "large"
    return "xl"


TIER_INDEX = {"tiny": 0, "small": 1, "medium": 2, "large": 3, "xl": 4}


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def aspect(self) -> float:
        return self.w / max(1, self.h)

    def inset(self, d: int) -> "Rect":
        return Rect(self.x + d, self.y + d, max(1, self.w - 2 * d), max(1, self.h - 2 * d))

    def square(self, align: str = "center") -> "Rect":
        s = min(self.w, self.h)
        x = self.x if align == "start" else self.x + (self.w - s) // 2
        y = self.y + (self.h - s) // 2
        return Rect(x, y, s, s)

    def split_x(self, at: int, gap: int = 1) -> tuple["Rect", "Rect"]:
        at = max(0, min(self.w, at))
        return Rect(self.x, self.y, at, self.h), Rect(self.x + at + gap, self.y, max(0, self.w - at - gap), self.h)

    def split_y(self, at: int, gap: int = 1) -> tuple["Rect", "Rect"]:
        at = max(0, min(self.h, at))
        return Rect(self.x, self.y, self.w, at), Rect(self.x, self.y + at + gap, self.w, max(0, self.h - at - gap))

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w - 1, self.y + self.h - 1)


@dataclass
class Ctx:
    """Shared layout context handed to every adaptive generator."""
    w: int
    h: int
    now: float = 1_760_000_000.0           # fixed sample time for previews / tests
    meta: dict = field(default_factory=dict)

    @property
    def aspect(self) -> float:
        return self.w / self.h

    @property
    def family(self) -> str:
        return classify(self.w, self.h)

    @property
    def tier(self) -> str:
        return tier(self.w, self.h)

    @property
    def t(self) -> int:
        return TIER_INDEX[self.tier]

    @property
    def orientation(self) -> str:
        return "landscape" if self.w > self.h else "portrait" if self.h > self.w else "square"

    @property
    def rect(self) -> Rect:
        return Rect(0, 0, self.w, self.h)

    def text_scale(self, rows_wanted: int = 1, box: Rect | None = None) -> int:
        """Largest integer scale of the 3x5 font giving `rows_wanted` rows in box."""
        bh = (box or self.rect).h
        return max(1, min(4, (bh + 1) // (6 * rows_wanted)))

    def describe(self) -> dict:
        return {"width": self.w, "height": self.h, "ratio": round(self.aspect, 2),
                "ratio_label": ratio_label(self.w, self.h), "family": self.family,
                "family_label": FAMILY_LABEL[self.family], "tier": self.tier, "orientation": self.orientation}


def ratio_label(w: int, h: int) -> str:
    return f"{w / h:.2f}:1" if w >= h else f"1:{h / w:.2f}"


# ------------------------------------------------------------------ text (3x5 font, scaled)
def text_w(s: str, scale: int = 1) -> int:
    from .tinyfont import text_width
    return text_width(s) * scale + (scale - 1) * 0


def text(img: Image.Image, x: int, y: int, s: str, col, scale: int = 1) -> int:
    from .tinyfont import draw_text, text_width
    if scale == 1:
        return draw_text(img, x, y, s, col)
    tw = text_width(s) + 1
    m = Image.new("RGB", (max(1, tw), 5))
    draw_text(m, 0, 0, s, (255, 255, 255))
    m = m.resize((m.width * scale, m.height * scale), Image.Resampling.NEAREST).convert("L")
    img.paste(Image.new("RGB", m.size, col), (x, y), m)
    return x + text_width(s) * scale + scale


def fit_text(s: str, px: int, scale: int = 1) -> str:
    s = s.upper()
    while s and text_w(s, scale) > px:
        s = s[:-1]
    return s


# ------------------------------------------------------------------ fallbacks for fixed assets
def contain(img: Image.Image, w: int, h: int) -> Image.Image:
    from .fit import fit
    return fit(img, w, h)


def crop(img: Image.Image, w: int, h: int) -> Image.Image:
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    if s >= 1:
        s = math.ceil(s)
        res = img.resize((img.width * s, img.height * s), Image.Resampling.NEAREST)
    else:
        res = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.Resampling.BOX)
    x, y = (res.width - w) // 2, (res.height - h) // 2
    return res.crop((x, y, x + w, y + h))


def scaled(img: Image.Image, w: int, h: int) -> Image.Image | None:
    """Integer upscale of the same aspect (no structural change), else None."""
    if w % img.width or h % img.height or w // img.width != h // img.height:
        return None
    s = w // img.width
    return img.convert("RGB").resize((w, h), Image.Resampling.NEAREST)


# ------------------------------------------------------------------ rating
def rate(native_sizes, w: int, h: int, adaptive: bool = False) -> str:
    """Native: designed+tested at exactly this size. Adaptive: generated with a layout for
    this display. Scaled: integer upscale of a native size, same aspect. Fallback: letterbox/crop."""
    ns = [tuple(s) for s in native_sizes]
    if (w, h) in ns:
        return "native"
    if adaptive:
        return "adaptive"
    if any(w % a == 0 and h % b == 0 and w // a == h // b for a, b in ns):
        return "scaled"
    return "fallback"


def best_master(native_sizes, w: int, h: int) -> tuple[int, int]:
    """Native size whose aspect is closest to w x h (log-ratio distance)."""
    return min((tuple(s) for s in native_sizes), key=lambda s: abs(math.log((s[0] / s[1]) / (w / h))))


def place(frames: list[Image.Image], native_sizes, w: int, h: int, strategy: str = "adaptive"):
    """Fixed-asset frames (rendered at their best master) -> (frames at w x h, rating used)."""
    src = frames[0].size
    if src == (w, h):
        return [f.convert("RGB") for f in frames], "native"
    if strategy in ("adaptive", "contain"):
        sc = [scaled(f, w, h) for f in frames]
        if sc[0] is not None:
            return sc, "scaled"
    fn = crop if strategy == "crop" else contain
    return [fn(f, w, h) for f in frames], "fallback"


# ------------------------------------------------------------------ preview look
def led_preview(img: Image.Image, scale: int = 6) -> Image.Image:
    """Any-size frame -> LED-dot preview (lit dots, faint unlit dots)."""
    img = img.convert("RGB")
    W, H = img.size
    big = img.resize((W * scale, H * scale), Image.Resampling.NEAREST)
    m = Image.new("L", (scale, scale))
    r = max(1, scale // 2 - (1 if scale >= 4 else 0))
    from PIL import ImageDraw
    ImageDraw.Draw(m).ellipse([scale / 2 - r - 0.5, scale / 2 - r - 0.5, scale / 2 + r - 0.5, scale / 2 + r - 0.5],
                              fill=255)
    tile = Image.new("L", big.size)
    for y in range(H):
        for x in range(W):
            tile.paste(m, (x * scale, y * scale))
    off = Image.new("RGB", big.size, (0, 0, 0))
    off.paste((24, 26, 30), (0, 0), tile)
    lit = Image.new("RGB", big.size, (0, 0, 0))
    lit.paste(big, (0, 0), tile)
    return Image.composite(lit, off, big.convert("L").point(lambda v: 255 if v > 6 else 0))


def reduce_frames(frames, durs, max_n: int):
    """Keep every k-th frame (k = smallest divisor-free step so the count fits) and stretch
    the durations: the loop keeps its length and stays seamless (k divides n when possible)."""
    n = len(frames)
    if n <= max_n:
        return list(frames), list(durs)
    k = next((d for d in range(2, n + 1) if n % d == 0 and n // d <= max_n), -(-n // max_n))
    idx = list(range(0, n, k))
    return [frames[i] for i in idx], [sum(durs[i:i + k]) for i in idx]
