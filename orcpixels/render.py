"""PIL helpers to compose 64x16 frames (used by CLI, web UI, and phase-2 watcher)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import protocol as P

W, H = 64, 16


def _font(size: int = 16) -> ImageFont.FreeTypeFont:
    return P.load_font(size=size)


def text_width(text: str, font) -> int:
    x0, _, x1, _ = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=font)
    return x1 - x0


def compose_status(title: str, subtitle: str = "", icon: Image.Image | str | Path | None = None,
                   color=(255, 255, 255), sub_color=(120, 180, 255), width: int = W,
                   height: int = H) -> Image.Image:
    """Icon (16x16) on the left + one or two lines of text on the right.

    With a subtitle, both lines use an 8px-high pixel font (Pillow default bitmap
    font) so they fit in 16 rows; otherwise title uses 16px Unifont.
    This is the frame the phase-2 window watcher will send.
    """
    img = Image.new("RGB", (width, height))
    x = 0
    if icon is not None:
        ic = icon if isinstance(icon, Image.Image) else Image.open(icon)
        ic = ic.convert("RGBA")
        ic.thumbnail((height, height), Image.Resampling.LANCZOS)
        img.paste(ic, (0, (height - ic.height) // 2), ic)
        x = height + 1
    d = ImageDraw.Draw(img)
    if subtitle:
        small = ImageFont.load_default()  # 6x11-ish bitmap font; crisp at 1px
        d.text((x, -2), title, fill=color, font=small)
        d.text((x, 7), subtitle, fill=sub_color, font=small)
    else:
        d.text((x, 0), title, fill=color, font=_font(16))
    return img


def compose_status64(title: str, subtitle: str = "", color=(255, 255, 255),
                     sub_color=(120, 180, 255)) -> Image.Image:
    """64x64 text frame: title in 16 px unifont wrapped over up to 3 lines, subtitle in
    the 3x5 font under it (wrapped)."""
    from .livedata_screens import big_w
    from .tinyfont import draw_text
    from .tinyfont import text_width as tw
    img = Image.new("RGB", (64, 64))
    d = ImageDraw.Draw(img)
    words, lines, cur = title.split(), [], ""
    for w in words:
        c = (cur + " " + w).strip()
        if big_w(c) <= 64:
            cur = c
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lines = lines[:3]
    y = 2 if subtitle else max(0, (64 - 16 * len(lines)) // 2 - 2)
    for ln in lines:
        d.text(((64 - big_w(ln)) // 2, y), ln, fill=color, font=_font(16))
        y += 16
    if subtitle:
        sub, cur = [], ""
        for w in subtitle.upper().split():
            c = (cur + " " + w).strip()
            if tw(c) <= 64:
                cur = c
            else:
                if cur:
                    sub.append(cur)
                cur = w
        if cur:
            sub.append(cur)
        y = max(y + 2, 40)
        for ln in sub[: max(1, (64 - y) // 6)]:
            draw_text(img, (64 - tw(ln)) // 2, y, ln, sub_color)
            y += 6
    return img


def test_pattern(width: int = W, height: int = H) -> Image.Image:
    """16x16 drawn icon + 'ORC' text + rainbow bar: exercises colors and geometry.
    A square panel (64x64) gets rainbow bands + a grey ramp under the same header."""
    img = Image.new("RGB", (width, height))
    if height >= 32:
        top = test_pattern(width, 16)
        img.paste(top, (0, 0))
        d = ImageDraw.Draw(img)
        for x in range(width):
            r, g, b = _hsv(x / max(1, width - 1))
            d.line((x, 18, x, 29), fill=(r, g, b))
        for i, c in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))):
            d.rectangle((i * 16, 32, i * 16 + 15, 45), fill=c)
        for x in range(width):
            v = int(x / max(1, width - 1) * 255)
            d.line((x, 48, x, 55), fill=(v, v, v))
        for y in range(58, height):
            for x in range(width):
                if (x + y) % 2 == 0:
                    img.putpixel((x, y), (90, 90, 90))
        for px in [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]:
            d.point(px, fill=(255, 0, 0))
        return img
    d = ImageDraw.Draw(img)
    # Icon: green orc-ish face in the left 16x16
    d.rectangle((1, 1, 14, 14), fill=(0, 140, 0))
    d.rectangle((3, 4, 5, 6), fill=(255, 255, 0))
    d.rectangle((10, 4, 12, 6), fill=(255, 255, 0))
    d.rectangle((4, 10, 11, 11), fill=(0, 0, 0))
    d.point([(4, 9), (11, 9)], fill=(255, 255, 255))
    d.text((18, 0), "ORC", fill=(255, 60, 0), font=_font(16))
    for i in range(width - 44):
        hue = i / max(1, width - 45)
        r, g, b = _hsv(hue)
        d.line((44 + i, 12, 44 + i, 15), fill=(r, g, b))
    # frame corners to check orientation/cropping
    for px in [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]:
        d.point(px, fill=(255, 0, 0))
    return img


def _hsv(h: float) -> tuple[int, int, int]:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h, 1, 1)
    return int(r * 255), int(g * 255), int(b * 255)


def preview_native_text(text: str, color: str = "ffffff", bg_color: str | None = None,
                        width: int = W, height: int = H) -> Image.Image:
    """Approximate what the device shows for a STATIC native text command."""
    fg = tuple(bytes.fromhex(color.lstrip("#")))
    bg = tuple(bytes.fromhex(bg_color.lstrip("#"))) if bg_color else (0, 0, 0)
    img = Image.new("RGB", (width, height), bg)
    font = _font(16)
    x = 0
    for ch in text:
        _, w = P.render_glyph(ch, font)
        ImageDraw.Draw(img).text((x, 0), ch, fill=fg, font=font)
        x += w
        if x >= width:
            break
    return img


# ------------------------------------------------------------ branding helpers
def _lerp_stops(stops, t: float):
    if len(stops) == 1:
        return tuple(stops[0])
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    a, b = stops[i], stops[i + 1]
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def gradient_text(text: str, width: int, stops, height: int = H, font=None) -> Image.Image:
    """Render text (1-bit Unifont) and color it with a horizontal gradient."""
    font = font or _font(16)
    mask = Image.new("L", (width, height))
    ImageDraw.Draw(mask).text((0, 0), text, fill=255, font=font)
    mask = mask.point(lambda p: 255 if p > 128 else 0)
    grad = Image.new("RGB", (width, height))
    for x in range(width):
        c = _lerp_stops(stops, x / max(1, width - 1))
        ImageDraw.Draw(grad).line((x, 0, x, height - 1), fill=c)
    out = Image.new("RGB", (width, height))
    out.paste(grad, (0, 0), mask)
    return out


def spectrum_bars(levels, width: int, height: int, stops) -> Image.Image:
    """Mini spectrum: one 1px bar per level (0..1), gradient-colored, bright tip."""
    img = Image.new("RGB", (width, height))
    d = ImageDraw.Draw(img)
    for i, v in enumerate(levels[:width]):
        h = max(1, round(v * (height - 1)))
        c = _lerp_stops(stops, i / max(1, len(levels) - 1))
        dim = tuple(int(k * 0.45) for k in c)
        d.line((i, height - 1, i, height - h), fill=dim)
        d.point((i, height - h), fill=c)
    return img


def scrolling_gif(text: str, icon: Image.Image | None = None, color=(80, 255, 40),
                  step: int = 1, frame_ms: int = 60, width: int = W, height: int = H,
                  stops=None) -> tuple[bytes, list[Image.Image]]:
    """Animated GIF: fixed 16x16 icon on the left, text scrolling right->left in
    the remaining area. Returns (gif_bytes, frames)."""
    import io
    font = _font(16)
    x0 = height + 1 if icon is not None else 0
    area = width - x0
    tw = text_width(text, font)
    strip_w = tw + 2 * area
    strip = (gradient_text(text, tw + 1, stops) if stops else None)
    if strip is None:
        strip = Image.new("RGB", (tw + 1, height))
        ImageDraw.Draw(strip).text((0, 0), text, fill=color, font=font)
    band = Image.new("RGB", (strip_w, height))
    band.paste(strip, (area, 0))
    frames = []
    # start with the text's left edge 1px into the scroll area, so even a
    # single-frame render (or the first frame) shows the text; then wrap around.
    for off in list(range(area - 1, tw + area + 1, step)) + list(range(0, area - 1, step)):
        f = Image.new("RGB", (width, height))
        if icon is not None:
            f.paste(icon.convert("RGB"), (0, 0))
        f.paste(band.crop((off, 0, off + area, height)), (x0, 0))
        frames.append(f)
    return encode_gif(frames, frame_ms), frames


def encode_gif(frames: list[Image.Image], frame_ms: int | list[int] = 80) -> bytes:
    """Device-friendly GIF: one shared global palette, full-size frames
    (disposal=2, no transparency -> Pillow writes every frame at 64x16 instead of
    delta sub-rectangles), loop forever."""
    import io
    rgb = [f.convert("RGB") for f in frames]
    sheet = Image.new("RGB", (rgb[0].width, rgb[0].height * len(rgb)))
    for i, f in enumerate(rgb):
        sheet.paste(f, (0, i * f.height))
    pal = sheet.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pframes = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in rgb]
    buf = io.BytesIO()
    pframes[0].save(buf, format="GIF", save_all=True, append_images=pframes[1:],
                    duration=frame_ms, loop=0, disposal=2, optimize=False)
    return buf.getvalue()


def gif_frames(gif: bytes) -> tuple[list[Image.Image], list[int]]:
    import io
    from PIL import ImageSequence
    im = Image.open(io.BytesIO(gif))
    frames, durs = [], []
    for fr in ImageSequence.Iterator(im):
        frames.append(fr.convert("RGB"))
        durs.append(int(fr.info.get("duration", im.info.get("duration", 100))))
    return frames, durs
