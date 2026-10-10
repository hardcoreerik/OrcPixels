"""Seamless-loop metric for animated presets.

For a GIF with frames f0..f(N-1):
  adjacent deltas d_i = mean |f_i - f_(i+1)| over RGB (0..255), i = 0..N-2
  seam            s   = mean |f_(N-1) - f_0|
  ratio           r   = s / median(d)
Plus a motion-compensated version (mc): each 16x16 tile is compared at the best
shift dx in -2..2, dy in -1..1, so a scrolling waterfall / text scroll has ~0
adjacent mc-delta and a broken scroll shows up at the seam even when the raw
delta looks "normal" (noise vs noise).
A loop is 'seamless' when the seam is no bigger than a normal step in BOTH:
  s <= 1.25 * p90(d) + 0.5   and   s_mc <= 1.25 * p90(d_mc) + 0.5
CLI:  python tools/loopcheck.py [preset ...]   (default: all non-backup GIF presets)
"""
from __future__ import annotations

import statistics

from PIL import Image, ImageChops

from .render import gif_frames


def _delta(a: Image.Image, b: Image.Image) -> float:
    d = ImageChops.difference(a, b).tobytes()
    return sum(d) / len(d)


def _mc_delta(a: Image.Image, b: Image.Image, tile: int = 16) -> float:
    w, h = a.size
    total = []
    for tx in range(0, w, tile):
        for ty in range(0, h, tile):
            best = None
            for dx in range(-2, 3):
                for dy in range(-1, 2):
                    x0, y0 = max(tx, tx + dx), max(ty, ty + dy)
                    x1, y1 = min(tx + tile, tx + tile + dx, w), min(ty + tile, ty + tile + dy, h)
                    if x1 - x0 < 4 or y1 - y0 < 4:
                        continue
                    ra = a.crop((x0 - dx, y0 - dy, x1 - dx, y1 - dy))
                    rb = b.crop((x0, y0, x1, y1))
                    v = _delta(ra, rb)
                    best = v if best is None or v < best else best
            total.append(best or 0.0)
    return sum(total) / len(total)


def loop_metric(gif: bytes) -> dict:
    frames, _ = gif_frames(gif)
    return loop_metric_frames(frames)


def loop_metric_frames(frames: list[Image.Image]) -> dict:
    frames = [f.convert("RGB") for f in frames]
    n = len(frames)
    if n < 2:
        return {"frames": n, "seam": 0.0, "median": 0.0, "p90": 0.0, "ratio": 0.0,
                "seam_mc": 0.0, "median_mc": 0.0, "p90_mc": 0.0, "ratio_mc": 0.0,
                "ok_raw": True, "ok_mc": True, "ok": True}
    def stats(fn):
        d = [fn(frames[i], frames[i + 1]) for i in range(n - 1)]
        s = fn(frames[-1], frames[0])
        med = statistics.median(d)
        p90 = sorted(d)[max(0, int(round(0.9 * (len(d) - 1))))]
        return s, med, p90

    s, med, p90 = stats(_delta)
    smc, medmc, p90mc = stats(_mc_delta)
    ok_raw = s <= 1.25 * p90 + 0.5
    ok_mc = smc <= 1.25 * p90mc + 0.5
    return {"frames": n, "seam": round(s, 2), "median": round(med, 2), "p90": round(p90, 2),
            "ratio": round(s / med, 2) if med else (float("inf") if s else 0.0),
            "seam_mc": round(smc, 2), "median_mc": round(medmc, 2), "p90_mc": round(p90mc, 2),
            "ratio_mc": round(smc / medmc, 2) if medmc else (float("inf") if smc > 0.5 else 0.0),
            "ok_raw": ok_raw, "ok_mc": ok_mc, "ok": ok_raw and ok_mc}
