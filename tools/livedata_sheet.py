"""Contact sheet of the Live Data screens rendered from REAL data (one forced fetch per
source; nothing is sent to the sign).

  python tools/livedata_sheet.py [--out out\\livedata_contact.png] [--frames 4]
                                 [--set all|weather|markets_news]

Each row: screen title, source, fetch result, then LED-dot frames sampled across the loop
(one per detail page). Prints a JSON summary (ok / error per source) to stdout.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from orcpixels import livedata_screens as SC  # noqa: E402
from orcpixels.sources import livefeeds as LF  # noqa: E402
from orcpixels.studio import led_frame  # noqa: E402


def font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


async def fetch_all(feeds):
    async def one(f):
        t = time.monotonic()
        await f.refresh(force=True)
        return f.key, round(time.monotonic() - t, 2)
    return dict(await asyncio.gather(*(one(f) for f in feeds.values())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "livedata_contact.png"))
    ap.add_argument("--frames", type=int, default=4)
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--set", default="all", choices=("all", "weather", "markets_news"))
    a = ap.parse_args()
    feeds = SC.make_feeds()
    if a.set != "all":
        want = {"weather": {"Weather, sky + radio"}, "markets_news": {"Markets", "News", "Optional"}}[a.set]
        feeds = {k: f for k, f in feeds.items() if SC.SCREENS[k].group in want}
    secs = asyncio.run(fetch_all(feeds))
    now = time.time()
    sc, nf = a.scale, a.frames
    fw, fh = 64 * sc, 16 * sc
    label_w, pad, row_h = 420, 8, fh + 16
    W = label_w + nf * (fw + pad) + pad
    head = 54
    sheet = Image.new("RGB", (W, head + len(feeds) * row_h + 10), (13, 15, 18))
    d = ImageDraw.Draw(sheet)
    f_b, f_s, f_h = font(15), font(12), font(20)
    ok_n = sum(1 for f in feeds.values() if f.error is None and f.value is not None)
    req = [k for k in feeds if k not in SC.OPTIONAL]
    ok_req = sum(1 for k in req if feeds[k].error is None and feeds[k].value is not None)
    opt = f" + {len(feeds) - len(req)} optional" if len(feeds) > len(req) else ""
    d.text((pad, 8), f"OrcPixels Live Data: {len(req)} screens{opt}, real data ({ok_req}/{len(req)} sources OK)",
           fill=(225, 238, 255), font=f_h)
    d.text((pad, 33), "fetched " + time.strftime("%Y-%m-%d %H:%M", time.localtime(now)) +
           " PT (box/PC local)  |  frames sampled across each seamless loop  |  red dot top-right = stale",
           fill=(125, 135, 148), font=f_s)
    summary = {}
    for r, (k, f) in enumerate(feeds.items()):
        y = head + r * row_h
        ok = f.error is None and f.value is not None
        d.rectangle((0, y, W, y + row_h - 4), fill=(21, 24, 29) if r % 2 == 0 else (17, 19, 24))
        d.text((pad, y + 6), f"{r + 1:2d}. {f.title}", fill=(225, 238, 255), font=f_b)
        d.text((pad, y + 27), f.source[:58], fill=(125, 135, 148), font=f_s)
        key = "no key" if not f.key_env else (f"needs {f.key_env} (optional screen)" if getattr(f, "needs_key", False)
                                              else f"no key (optional {f.key_env})")
        via = (f.value or {}).get("via") if isinstance(f.value, dict) else None
        st = ((f"OK in {secs[k]} s" + (f" via {via}" if via else "")) if not f.local else "local (no network)") if ok \
            else ("not enabled (no token, no request)" if getattr(f, "needs_key", False) else f"FAIL: {f.error}"[:56])
        d.text((pad, y + 44), f"{st}  |  {key}  |  poll {int(f.poll_s)} s"[:70],
               fill=(110, 240, 166) if ok else ((200, 190, 120) if getattr(f, "needs_key", False) else (255, 138, 128)), font=f_s)
        n = SC.loop_len(k, f)
        ks = sorted({int(i * n / nf) for i in range(nf)})
        for j, kk in enumerate(ks):
            sheet.paste(led_frame(SC.render(k, f, kk, now), sc).convert("RGB"), (label_w + pad + j * (fw + pad), y + 6))
        summary[k] = {"title": f.title, "source": f.source, "ok": ok, "error": f.error, "secs": secs[k],
                      "key_env": f.key_env, "local": f.local, "loop": n, "via": via,
                      "needs_key": bool(getattr(f, "needs_key", False)),
                      "requests": f.requests}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(a.out)
    print(json.dumps({"out": a.out, "ok": ok_n, "ok_required": ok_req, "required": len(req), "screens": summary},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
