"""Image Converter + Image Creator backend for the web UI (Task B, 2026-09-30).

Both tools produce a *job*: a list of upright 64x16 RGB frames + frame durations, kept in
a small in-memory cache. A job can be previewed, loop-checked (orcpixels.loopmetric, the
same metric as tools/loopcheck.py), sent to the sign (SignClient.send_gif/send_image, so
rotate_180 is applied there), saved as a preset (presets/<name>.gif|png + .json), or
stored in a device slot (prepare_* + store_*, recorded in config/device_slots.json).
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import importlib.util
import io
import json
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageSequence

from . import presets as PR
from . import protocol as P
from .loopmetric import loop_metric_frames
from .render import encode_gif, gif_frames

W, H = 64, 16          # default (64x16 strip); the current display's size is _wh()


def _wh() -> tuple[int, int]:
    """Target size = the display this request is for (displays.CURRENT), 2026-10-08."""
    from . import displays as D
    return D.size()
ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 8 * 1024 * 1024
MAX_SRC_FRAMES = 400
MAX_FRAMES = 64
FAMILIES = ("proj", "rf", "orc", "code", "hw", "amb", "orc_co", "user")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,47}$")
BIG_GIF = 15 * 1024          # same budget as tests/test_personal_presets.py (BLE ~1.1 KB/s)

DEFAULTS = {"mode": "fit", "pos_x": 0.5, "pos_y": 0.5, "zoom": 1.0, "bg": "#000000",
            "brightness": 1.0, "contrast": 1.0, "saturation": 1.0,
            "colors": 0, "dither": "none", "fps": 10.0, "max_frames": 32,
            "loop": "none", "xfade": 4,
            # animated-GIF import (2026-10-09): trim on the source timeline (ms; end 0 = to the
            # end), playback speed, layout strategy at the display size, fit to sign limits
            "trim_start": 0, "trim_end": 0, "speed": 1.0, "strategy": "", "fit_limits": True}
STRATEGIES = ("", "adaptive", "contain", "crop")      # "" = use `mode` as is


# ------------------------------------------------------------------ helpers
def hex_rgb(s: str | None, default=(0, 0, 0)) -> tuple[int, int, int]:
    s = (s or "").lstrip("#")
    if len(s) != 6:
        return default
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def png_bytes(img: Image.Image, scale: int = 1) -> bytes:
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def data_url(img: Image.Image) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes(img)).decode()


def from_data_url(s: str) -> Image.Image:
    if "," in s:
        s = s.split(",", 1)[1]
    img = Image.open(io.BytesIO(base64.b64decode(s)))
    img.load()
    return img


def flatten(img: Image.Image, bg=(0, 0, 0)) -> Image.Image:
    rgba = img.convert("RGBA")
    base = Image.new("RGBA", rgba.size, bg + (255,))
    return Image.alpha_composite(base, rgba).convert("RGB")


def source_info(data: bytes) -> dict:
    """What an uploaded file is: format, size, frame count, total duration, GIF loop count
    (0 = forever, None = play once)."""
    im = Image.open(io.BytesIO(data))
    n = getattr(im, "n_frames", 1)
    total = 0
    for i in range(min(n, MAX_SRC_FRAMES)):
        im.seek(i)
        total += int(im.info.get("duration", 100) or 100)
    return {"format": im.format, "width": im.width, "height": im.height, "frames": n,
            "duration_ms": total if n > 1 else 0, "loop": im.info.get("loop") if n > 1 else None}


def decode_frames(data: bytes) -> tuple[list[Image.Image], list[int]]:
    """Any PIL image (PNG/JPG/GIF/animated PNG/WebP) -> RGBA frames + durations (ms).
    Every frame is the fully composited picture (Pillow applies GIF disposal / partial
    frames), so frame i is what a browser shows at that moment."""
    im = Image.open(io.BytesIO(data))
    frames, durs = [], []
    for fr in ImageSequence.Iterator(im):
        frames.append(fr.convert("RGBA"))
        durs.append(int(fr.info.get("duration", im.info.get("duration", 100)) or 100))
        if len(frames) >= MAX_SRC_FRAMES:
            break
    if not frames:
        raise ValueError("no frames")
    return frames, [max(20, d) for d in durs]


# ------------------------------------------------------------------ converter steps
def trim(frames: list, durs: list[int], start_ms: int = 0, end_ms: int = 0) -> tuple[list, list[int]]:
    """Keep the source frames shown between start_ms and end_ms (0 = the end); the first
    and last kept frames are shortened to the cut."""
    if len(frames) < 2 or (start_ms <= 0 and end_ms <= 0):
        return frames, durs
    total = sum(durs)
    a = max(0, min(int(start_ms), total - 20))
    b = total if end_ms <= 0 else max(a + 20, min(int(end_ms), total))
    out, od, t = [], [], 0
    for f, d in zip(frames, durs):
        s0, s1 = t, t + d
        t = s1
        lo, hi = max(s0, a), min(s1, b)
        if hi - lo >= 1:
            out.append(f)
            od.append(max(20, hi - lo))
    return (out, od) if out else (frames[:1], [durs[0]])


def speed_up(durs: list[int], speed: float) -> list[int]:
    sp = max(0.1, min(float(speed or 1.0), 10.0))
    return [max(20, round(d / sp)) for d in durs]


def strategy_mode(strategy: str, src_size: tuple[int, int], mode: str) -> str:
    """adaptive: cover the panel when the picture's shape is close to the panel's (little is
    lost), letterbox when it isn't; contain = fit; crop = fill."""
    if strategy == "contain":
        return "fit"
    if strategy == "crop":
        return "fill"
    if strategy == "adaptive":
        W, H = _wh()
        a, b = src_size[0] / max(1, src_size[1]), W / H
        return "fill" if max(a, b) / min(a, b) <= 1.34 else "fit"
    return mode


def legacy_limits() -> dict:
    """Upload limits for the selected display: iPixel BLE signs get the 15 KB budget and the
    64-frame cap; network/USB drivers only the frame cap."""
    from . import displays as D
    try:
        ip = D.is_ipixel(D.current())
    except Exception:  # noqa: BLE001
        ip = True
    return {"max_frames": MAX_FRAMES, "max_bytes": BIG_GIF if ip else 0, "ble": ip}


def fit_to_limits(frames: list[Image.Image], ms: int, colors: int, dither: str, lim: dict):
    """Shrink until the encoded GIF fits: fewer colours first (64, 32, 16, 8), then drop
    every other frame (the frame time doubles, so speed and loop are kept). Returns
    frames, frame_ms, notes."""
    notes = []
    if len(frames) < 2 or not lim.get("max_bytes"):
        return frames, ms, notes
    def size(fr, m):
        return len(encode_gif(fr, m))
    if size(frames, ms) <= lim["max_bytes"]:
        return frames, ms, notes
    base = frames
    for c in (64, 32, 16, 8):
        if colors and colors <= c:
            continue
        cand = reduce_colors(base, c, dither)
        if size(cand, ms) <= lim["max_bytes"]:
            notes.append(f"reduced to {c} colours for the sign's upload budget")
            return cand, ms, notes
    cand, m, c = reduce_colors(base, 16, dither), ms, 16
    notes.append("reduced to 16 colours")
    while len(cand) >= 4 and size(cand, m) > lim["max_bytes"]:
        cand, m = cand[::2], m * 2
    notes.append(f"{len(cand)} frames at {m} ms (every {len(base) // max(1, len(cand))}th frame) for the sign's upload budget")
    return cand, m, notes



def fit_frame(img: Image.Image, mode: str = "fit", pos_x: float = 0.5, pos_y: float = 0.5,
              zoom: float = 1.0, bg=(0, 0, 0)) -> Image.Image:
    """fit = letterbox (whole image visible), fill = cover the panel (cropped at pos),
    crop = native pixels 1:1 (x zoom) at pos, stretch = distort to 64x16 (x zoom)."""
    W, H = _wh()
    img = img.convert("RGBA")
    sw, sh = img.size
    zoom = max(0.1, min(float(zoom), 20.0))
    if mode == "stretch":
        nw, nh = W * zoom, H * zoom
    else:
        if mode == "fill":
            scale = max(W / sw, H / sh)
        elif mode == "crop":
            scale = 1.0
        else:
            scale = min(W / sw, H / sh)
        nw, nh = sw * scale * zoom, sh * scale * zoom
    nw, nh = max(1, round(nw)), max(1, round(nh))
    up = nw >= sw and nh >= sh
    res = img.resize((nw, nh), Image.Resampling.NEAREST if up else Image.Resampling.LANCZOS)
    px, py = min(max(float(pos_x), 0.0), 1.0), min(max(float(pos_y), 0.0), 1.0)
    ox, oy = round((W - nw) * px), round((H - nh) * py)
    canvas = Image.new("RGBA", (W, H), tuple(bg) + (255,))
    canvas.alpha_composite(res, (max(ox, 0), max(oy, 0)),
                           (max(-ox, 0), max(-oy, 0)))
    return canvas.convert("RGB")


def adjust(img: Image.Image, brightness=1.0, contrast=1.0, saturation=1.0) -> Image.Image:
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(float(brightness))
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(float(contrast))
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(float(saturation))
    return img


def reduce_colors(frames: list[Image.Image], colors: int = 0, dither: str = "none") -> list[Image.Image]:
    """Shared palette over all frames (like encode_gif) with optional Floyd-Steinberg."""
    W, H = _wh()
    colors = int(colors or 0)
    if colors <= 0 and dither == "none":
        return frames
    colors = max(2, min(colors or 256, 256))
    sheet = Image.new("RGB", (W, H * len(frames)))
    for i, f in enumerate(frames):
        sheet.paste(f, (0, i * H))
    pal = sheet.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    d = Image.Dither.FLOYDSTEINBERG if dither in ("fs", "floyd", "floyd-steinberg") else Image.Dither.NONE
    return [f.quantize(palette=pal, dither=d).convert("RGB") for f in frames]


def resample(frames: list, durs: list[int], fps: float, max_frames: int) -> tuple[list, int]:
    """Pick frames at `fps` over the source's total duration (capped at max_frames; the
    frame time then stretches so the animation keeps its real speed)."""
    if len(frames) == 1:
        return frames, 1000
    total = sum(durs)
    fps = max(1.0, min(float(fps), 50.0))
    n = max(2, min(int(max_frames), MAX_FRAMES, len(frames), round(total / 1000.0 * fps)))
    ends, acc = [], 0
    for d in durs:
        acc += d
        ends.append(acc)
    out, j = [], 0
    for k in range(n):
        t = k * total / n
        while j < len(ends) - 1 and ends[j] <= t:
            j += 1
        out.append(frames[j])
    return out, max(20, round(total / n))


def make_loop(frames: list[Image.Image], mode: str = "none", k: int = 4) -> list[Image.Image]:
    """pingpong: f0..fN-1, fN-2..f1.  crossfade: drop the first k frames and blend the
    last k into them, so the end fades into the (new) start:
    out = f[k:n-k] + [blend(f[n-k+i], f[i], (i+1)/(k+1)) for i in range(k)]."""
    n = len(frames)
    if mode == "pingpong" and n >= 3:
        return frames + frames[-2:0:-1]
    if mode == "crossfade" and n >= 4:
        k = max(1, min(int(k), (n - 1) // 2))
        return frames[k:n - k] + [Image.blend(frames[n - k + i], frames[i], (i + 1) / (k + 1))
                                  for i in range(k)]
    return frames


# ------------------------------------------------------------------ jobs
@dataclass
class Job:
    frames: list[Image.Image]
    frame_ms: int = 100
    source: str = "studio"
    params: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created: float = field(default_factory=time.time)
    _enc: bytes | None = None
    notes: list = field(default_factory=list)
    src_info: dict | None = None
    src_bytes: bytes | None = None

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    def encoded(self) -> bytes:
        W, H = _wh()
        if self._enc is None:
            self._enc = (encode_gif(self.frames, self.frame_ms) if self.animated
                         else P.image_to_png(self.frames[0], W, H))
        return self._enc

    def info(self, with_frames: bool = True) -> dict:
        enc = self.encoded()
        m = loop_metric_frames(self.frames) if self.animated else None
        warn = []
        if self.animated and len(enc) > BIG_GIF:
            warn.append(f"GIF is {len(enc) / 1024:.1f} KB (> 15 KB): slow over BLE (~1.1 KB/s)")
        if m and not m["ok"]:
            warn.append("loop seam is visible (loopcheck JUMP): try ping-pong or crossfade")
        out = {"id": self.id, "source": self.source, "kind": "gif" if self.animated else "png",
               "frames": len(self.frames), "frame_ms": self.frame_ms,
               "fps": round(1000 / self.frame_ms, 2), "bytes": len(enc),
               "upload_s": round(len(enc) / 1100 + 0.6, 1), "loop": m, "warnings": warn,
               "params": self.params, "notes": self.notes, "source_info": self.src_info,
               "size": list(self.frames[0].size)}
        if with_frames:
            out["frame_urls"] = [data_url(f) for f in self.frames]
        return out


class JobCache:
    def __init__(self, maxlen: int = 16):
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self.sources: OrderedDict[str, bytes] = OrderedDict()
        self.maxlen = maxlen

    def put(self, job: Job) -> Job:
        self.jobs[job.id] = job
        while len(self.jobs) > self.maxlen:
            self.jobs.popitem(last=False)
        return job

    def get(self, jid: str) -> Job:
        if jid not in self.jobs:
            raise KeyError(f"unknown or expired job {jid!r}")
        self.jobs.move_to_end(jid)
        return self.jobs[jid]

    def put_source(self, data: bytes) -> str:
        sid = hashlib.sha1(data).hexdigest()[:12]
        self.sources[sid] = data
        self.sources.move_to_end(sid)
        while len(self.sources) > 8:
            self.sources.popitem(last=False)
        return sid


def norm_params(params: dict | None) -> dict:
    p = dict(DEFAULTS)
    for k, v in (params or {}).items():
        if k in DEFAULTS and v is not None:
            p[k] = type(DEFAULTS[k])(v) if not isinstance(DEFAULTS[k], str) else str(v)
    if p["mode"] not in ("fit", "fill", "crop", "stretch"):
        raise ValueError(f"mode must be fit|fill|crop|stretch, not {p['mode']!r}")
    if p["loop"] not in ("none", "pingpong", "crossfade"):
        raise ValueError(f"loop must be none|pingpong|crossfade, not {p['loop']!r}")
    if p["dither"] not in ("none", "fs"):
        raise ValueError("dither must be none|fs")
    p["max_frames"] = max(1, min(int(p["max_frames"]), MAX_FRAMES))
    if p["strategy"] not in STRATEGIES:
        raise ValueError("strategy must be adaptive|contain|crop")
    p["speed"] = max(0.1, min(float(p["speed"]), 10.0))
    p["trim_start"], p["trim_end"] = max(0, int(p["trim_start"])), max(0, int(p["trim_end"]))
    if p["trim_end"] and p["trim_end"] <= p["trim_start"]:
        raise ValueError("trim end must be after trim start")
    return p


def convert(data: bytes, params: dict | None = None) -> Job:
    """Uploaded image/GIF bytes + converter params -> Job."""
    if len(data) > MAX_UPLOAD:
        raise ValueError("file too large (max 8 MB)")
    p = norm_params(params)
    info = source_info(data)
    src, durs = decode_frames(data)
    src, durs = trim(src, durs, p["trim_start"], p["trim_end"])
    durs = speed_up(durs, p["speed"])
    src, ms = resample(src, durs, p["fps"], p["max_frames"])
    bg = hex_rgb(p["bg"])
    mode = strategy_mode(p["strategy"], (info["width"], info["height"]), p["mode"])
    frames = [adjust(fit_frame(f, mode, p["pos_x"], p["pos_y"], p["zoom"], bg),
                     p["brightness"], p["contrast"], p["saturation"]) for f in src]
    frames = reduce_colors(frames, p["colors"], p["dither"])
    frames = make_loop(frames, p["loop"], p["xfade"])
    notes = []
    if p["fit_limits"]:
        frames, ms, notes = fit_to_limits(frames, ms, p["colors"], p["dither"], legacy_limits())
    job = Job(frames=frames, frame_ms=ms, source="converter", params={**p, "applied_mode": mode})
    job.notes, job.src_info = notes, info
    job.src_bytes = data if info["frames"] > 1 else None
    return job


def from_frames(urls: list[str], frame_ms: int = 100, loop: str = "none", xfade: int = 4) -> Job:
    """Image Creator frames (PNG data URLs, 64x16; transparent = black)."""
    W, H = _wh()
    if not urls:
        raise ValueError("no frames")
    if len(urls) > MAX_FRAMES:
        raise ValueError(f"at most {MAX_FRAMES} frames")
    frames = []
    for u in urls:
        img = from_data_url(u)
        img = flatten(img) if img.size == (W, H) else fit_frame(img, "fit")
        frames.append(img)
    frames = make_loop(frames, loop, xfade)
    return Job(frames=frames, frame_ms=max(20, min(int(frame_ms), 5000)), source="creator",
               params={"loop": loop, "xfade": xfade})


# ------------------------------------------------------------------ fonts
_pixfont = None


def _pix():
    global _pixfont
    if _pixfont is None:
        spec = importlib.util.spec_from_file_location("orcpixels_pixfont", ROOT / "tools" / "pixfont.py")
        _pixfont = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_pixfont)
    return _pixfont


FONTS = {"tiny": "3x5 tiny (tinyfont)", "pix35": "3x5 pixel (tools/pixfont)",
         "unifont": "Unifont 16 px (device-like)", "unifont8": "Unifont 8 px"}


def render_text(text: str, font: str = "tiny", color: str = "#ffffff", x: int = 0, y: int = 0) -> tuple[Image.Image, int]:
    """Text on a transparent 64x16 layer (hard pixels, no anti-aliasing) -> (RGBA, width)."""
    W, H = _wh()
    rgb = hex_rgb(color, (255, 255, 255))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text = text[:80]
    if font == "tiny":
        from . import tinyfont as TF
        end = TF.draw_text(layer, x, y, text, rgb + (255,))
        return layer, end - x
    if font == "pix35":
        pf = _pix()
        end = pf.text35(layer, x, y, text, rgb + (255,))
        return layer, end - x
    if font in ("unifont", "unifont8"):
        size = 16 if font == "unifont" else 8
        f = P.load_font(size=size)
        mask = Image.new("1", (W, H), 0)
        d = ImageDraw.Draw(mask)
        d.fontmode = "1"
        d.text((x, y), text, font=f, fill=1)
        layer.paste(Image.new("RGBA", (W, H), rgb + (255,)), (0, 0), mask)
        x0, _, x1, _ = d.textbbox((0, 0), text, font=f)
        return layer, x1 - x0
    raise ValueError(f"unknown font {font!r}; have {sorted(FONTS)}")


# ------------------------------------------------------------------ import
def list_assets() -> list[dict]:
    out = []
    for p in sorted((ROOT / "assets").rglob("*")):
        if p.suffix.lower() not in (".png", ".gif") or "_preview" in p.stem:
            continue
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001
            continue
        if w <= 128 and h <= 64:
            out.append({"path": p.relative_to(ROOT / "assets").as_posix(), "w": w, "h": h})
    return out


def import_frames(preset: str | None = None, asset: str | None = None) -> dict:
    W, H = _wh()
    if preset:
        meta = PR.get(preset)
        data = PR.file_path(meta).read_bytes()
    elif asset:
        base = (ROOT / "assets").resolve()
        p = (base / asset).resolve()
        if base not in p.parents or not p.is_file():
            raise KeyError(f"unknown asset {asset!r}")
        data = p.read_bytes()
    else:
        raise ValueError("need preset or asset")
    src, durs = decode_frames(data)
    src, durs = src[:MAX_FRAMES], durs[:MAX_FRAMES]
    frames = []
    for f in src:
        if f.size == (W, H):
            frames.append(f)
        else:            # small icons land at the left, keep transparency for layering
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            g = f.copy()
            g.thumbnail((W, H), Image.Resampling.NEAREST)
            layer.alpha_composite(g, (0, (H - g.height) // 2))
            frames.append(layer)
    return {"frames": [data_url(f) for f in frames], "durations": durs,
            "frame_ms": round(sum(durs) / len(durs)) if durs else 100}


# ------------------------------------------------------------------ LED-look thumbnails
_DOT_CACHE: dict = {}
_LED_CACHE: OrderedDict = OrderedDict()


def _dot_mask(scale: int, size: tuple[int, int] = (W, H)) -> Image.Image:
    """Full-panel mask with one round dot per LED (white dot on black)."""
    W, H = size
    if (scale, size) not in _DOT_CACHE:
        tile = Image.new("L", (scale, scale), 0)
        r = max(1.0, scale * 0.42)
        c = (scale - 1) / 2
        ImageDraw.Draw(tile).ellipse((c - r + 0.5, c - r + 0.5, c + r - 0.5, c + r - 0.5), fill=255)
        mask = Image.new("L", (W * scale, H * scale), 0)
        for y in range(H):
            for x in range(W):
                mask.paste(tile, (x * scale, y * scale))
        _DOT_CACHE[(scale, size)] = mask
    return _DOT_CACHE[(scale, size)]


def led_frame(img: Image.Image, scale: int = 4) -> Image.Image:
    """64x16 frame -> scale-x upscaled 'LED dot' look (unlit LEDs faintly visible)."""
    img = flatten(img) if img.mode != "RGB" else img
    if img.size not in ((64, 16), (64, 64)):
        img = fit_frame(img, "fit")
    W, H = img.size
    big = img.resize((W * scale, H * scale), Image.Resampling.NEAREST)
    lit = Image.new("RGB", big.size, (0, 0, 0))
    lit.paste(big, (0, 0), _dot_mask(scale, (W, H)))
    off = Image.new("RGB", big.size, (0, 0, 0))
    off.paste((22, 24, 28), (0, 0), _dot_mask(scale, (W, H)))
    return Image.composite(lit, off, big.convert("L").point(lambda v: 255 if v > 6 else 0))


def led_thumb(path: Path, scale: int = 4) -> tuple[bytes, str]:
    """Preset file -> LED-look PNG (still) or GIF (animated), cached by mtime."""
    scale = max(1, min(int(scale), 8))
    key = (str(path), path.stat().st_mtime, scale)
    if key in _LED_CACHE:
        _LED_CACHE.move_to_end(key)
        return _LED_CACHE[key]
    frames, durs = decode_frames(path.read_bytes())
    if len(frames) > 1:
        out = (encode_gif([led_frame(f, scale) for f in frames], durs), "image/gif")
    else:
        out = (png_bytes(led_frame(frames[0], scale)), "image/png")
    _LED_CACHE[key] = out
    while len(_LED_CACHE) > 300:
        _LED_CACHE.popitem(last=False)
    return out


# ------------------------------------------------------------------ save / store
def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def save_preset(job: Job, name: str, family: str = "user", title: str = "", description: str = "",
                overwrite: bool = False, force: bool = False) -> dict:
    name = (name or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("preset name: lowercase letters, digits, _ (max 48)")
    if name.startswith("p_") or re.search(r"_v\d+$", name):
        raise ValueError("names starting with p_ or ending in _v<N> are reserved")
    if family not in FAMILIES:
        raise ValueError(f"family must be one of {FAMILIES}")
    size = tuple(job.frames[0].size)
    sdir = PR.save_dir_for(size)          # the display size's set (presets64 for a 64x64 sign)
    meta_p = sdir / f"{name}.json"
    if meta_p.exists() and not overwrite:
        raise FileExistsError(f"preset {name!r} exists (tick overwrite)")
    m = loop_metric_frames(job.frames) if job.animated else None
    if m and not m["ok"] and not force:
        raise RuntimeError("animation does not loop seamlessly (loopcheck JUMP); "
                           "fix the loop or save anyway with force")
    enc = job.encoded()
    ext = "gif" if job.animated else "png"
    (sdir / f"{name}.{ext}").write_bytes(enc)
    prev = f"{name}_preview_10x.png"
    (sdir / prev).write_bytes(png_bytes(job.frames[0], 10))
    meta = {"name": name, "kind": "gif" if job.animated else "image", "file": f"{name}.{ext}",
            "preview": prev, "family": family, "title": title or name,
            "description": description or f"made in the web {job.source}",
            "frames": len(job.frames), "frame_ms": job.frame_ms, "bytes": len(enc), "size": list(size),
            "loop": (("seamless" if m["ok"] else "JUMP (saved with force)") if m else None),
            "live_data": False, "generator": f"web studio ({job.source})", "created": _now()}
    if job.params:
        meta["params"] = job.params
    if job.src_bytes:                     # keep the original GIF in the user's Library
        try:
            from . import paths
            lib = paths.library() / "imports"
            lib.mkdir(parents=True, exist_ok=True)
            (lib / f"{name}.gif").write_bytes(job.src_bytes)
            meta["source_file"] = f"imports/{name}.gif"
        except OSError:
            pass
    meta_p.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


async def store_slot(sign, job: Job, slot: int, preset: str | None = None) -> dict:
    """Write the job into device slot 1..100 and record it in device_slots.json. If the
    slot belongs to the playlist the device is running, re-send the play order (0x8008)."""
    from . import playlist as PL
    from .activity import labelled
    P._check_slots([slot])
    if job.animated:
        dev, upright, first = sign.prepare_gif(job.encoded())
    else:
        dev, first = sign.prepare_png(job.frames[0])
        upright = None
    t0 = time.monotonic()
    await sign._ensure_orientation(0)
    with labelled(f"slot {slot} <- {preset or job.source}"):
        ack = await (sign.store_gif if job.animated else sign.store_png)(dev, slot)
    dt = time.monotonic() - t0
    rec = PL.slot_record()
    rec["slots"][str(slot)] = {"preset": preset or f"studio:{job.id}", "kind": "gif" if job.animated else "png",
                               "bytes": len(dev), "sha1": hashlib.sha1(dev).hexdigest(),
                               "rotate_180": sign.rotate_180, "playlist": None, "source": job.source,
                               "written": _now(), "ack": ack["raw"], "send_s": round(dt, 2)}
    PL._save_record(rec)
    out = {"ok": True, "slot": slot, "bytes": len(dev), "ack": ack["raw"], "send_s": round(dt, 2),
           "replayed": None}
    act = rec.get("active") or {}
    if slot in (act.get("slots") or []):
        r = await PL.play(sign)
        out["replayed"] = r["playlist"]
    sign.last_frame, sign.last_gif = first, upright
    return out


# ------------------------------------------------------------------ web routes
# request models live at module level: with `from __future__ import annotations` FastAPI
# can't resolve models defined inside install() (see web.SettingsReq)
from typing import Optional  # noqa: E402

from fastapi import File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from pydantic import BaseModel  # noqa: E402


class FramesReq(BaseModel):
    frames: list[str]
    frame_ms: int = 100
    loop: str = "none"
    xfade: int = 4

class TextReq(BaseModel):
    text: str
    font: str = "tiny"
    color: str = "#ffffff"
    x: int = 0
    y: int = 0

class SaveReq(BaseModel):
    name: str
    family: str = "user"
    title: str = ""
    description: str = ""
    overwrite: bool = False
    force: bool = False

class StoreReq(BaseModel):
    slot: int
    preset: Optional[str] = None


def install(app, sign, auto=None):
    import asyncio

    from .activity import labelled

    cache = JobCache()
    app.state.studio = cache

    def job_or_404(jid: str) -> Job:
        try:
            return cache.get(jid)
        except KeyError as e:
            raise HTTPException(404, str(e))

    async def stop_live():
        for attr in ("orcsdr_live", "livedata_live"):
            live = getattr(app.state, attr, None)
            if live is not None and live.enabled:
                await live.stop()
                live.stopped_reason = "image studio took over the sign"
        if auto is not None and auto.enabled:
            await auto.stop()

    @app.post("/api/studio/convert")
    async def studio_convert(file: Optional[UploadFile] = File(None), params: str = Form("{}"),
                             source_id: str = Form("")):
        if file is not None:
            data = await file.read()
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "file too large (max 8 MB)")
            sid = cache.put_source(data)
        elif source_id and source_id in cache.sources:
            sid, data = source_id, cache.sources[source_id]
        else:
            raise HTTPException(400, "upload a file (or pass a known source_id)")
        try:
            p = json.loads(params or "{}")
            job = await asyncio.to_thread(convert, data, p)
        except (ValueError, OSError, json.JSONDecodeError) as e:
            raise HTTPException(400, f"{type(e).__name__}: {e}")
        cache.put(job)
        return {**await asyncio.to_thread(job.info), "source_id": sid}

    @app.post("/api/studio/frames")
    async def studio_frames(r: FramesReq):
        try:
            job = await asyncio.to_thread(from_frames, r.frames, r.frame_ms, r.loop, r.xfade)
        except (ValueError, OSError) as e:
            raise HTTPException(400, f"{type(e).__name__}: {e}")
        cache.put(job)
        return await asyncio.to_thread(job.info)

    @app.get("/api/studio/jobs/{jid}")
    async def studio_job(jid: str, frames: bool = False):
        return job_or_404(jid).info(with_frames=frames)

    @app.get("/api/studio/jobs/{jid}/preview")
    async def studio_preview(jid: str):
        job = job_or_404(jid)
        return Response(job.encoded(), media_type="image/gif" if job.animated else "image/png",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/studio/jobs/{jid}/send")
    async def studio_send(jid: str):
        job = job_or_404(jid)
        try:
            with labelled(f"{job.source} image ({len(job.frames)} fr)"):
                if job.animated:
                    await sign.send_gif(job.encoded(), job.frames[0])
                else:
                    await sign.send_image(job.frames[0])
            sign.set_showing(job.source, f"{job.source} job {job.id}")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"{type(e).__name__}: {e}")
        return {"ok": True, "bytes": len(job.encoded()), "frames": len(job.frames)}

    @app.post("/api/studio/jobs/{jid}/save")
    async def studio_save(jid: str, r: SaveReq):
        job = job_or_404(jid)
        try:
            return {"ok": True, "preset": save_preset(job, r.name, r.family, r.title, r.description,
                                                      r.overwrite, r.force)}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except (FileExistsError, RuntimeError) as e:
            raise HTTPException(409, str(e))

    @app.post("/api/studio/jobs/{jid}/store")
    async def studio_store(jid: str, r: StoreReq):
        job = job_or_404(jid)
        if not 1 <= r.slot <= P.PROGRAM_SLOT_MAX:
            raise HTTPException(400, f"slot must be 1..{P.PROGRAM_SLOT_MAX}")
        await stop_live()
        try:
            return await store_slot(sign, job, r.slot, r.preset)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"{type(e).__name__}: {e}")

    @app.get("/api/presets/{name}/led")
    async def preset_led(name: str, scale: int = 4):
        """LED-dot thumbnail (256x64 at scale 4), animated for GIF presets."""
        try:
            path = PR.file_path(PR.get(name))
        except KeyError as e:
            raise HTTPException(404, str(e))
        data, mt = await asyncio.to_thread(led_thumb, path, scale)
        return Response(data, media_type=mt, headers={"Cache-Control": "max-age=60"})

    @app.get("/api/studio/fonts")
    async def studio_fonts():
        return FONTS

    @app.post("/api/studio/text")
    async def studio_text(r: TextReq):
        try:
            layer, width = render_text(r.text, r.font, r.color, r.x, r.y)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"png": data_url(layer), "width": width}

    @app.get("/api/studio/assets")
    async def studio_assets():
        return list_assets()

    @app.get("/api/studio/import")
    async def studio_import(preset: Optional[str] = None, asset: Optional[str] = None):
        try:
            return await asyncio.to_thread(import_frames, preset, asset)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
