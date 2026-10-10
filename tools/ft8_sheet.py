"""Contact sheet of the 8 FT8 / FT4 Live Data screens (nothing is sent to the sign).

  python tools/ft8_sheet.py [--out ft8_contact.png] [--capture tests/fixtures/livedata/ft8_serial.txt]
                            [--live] [--mycall K7XYZ] [--frames 6]

Data: --live asks OrcSDR once (read-only: GET /api/ft8, else serial FT8 STATUS + FT8 DECODES
on the configured port); otherwise a saved console capture (default: the real 20 m capture
in tests/fixtures). Extra rows show the idle state and a "my call heard" alert scenario.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from orcpixels import livedata_screens as SC  # noqa: E402
from orcpixels import livedata_ft8 as F8  # noqa: E402
from orcpixels.sources import ft8feed as FT  # noqa: E402
from orcpixels.sources import livefeeds as LF  # noqa: E402
from orcpixels.studio import led_frame  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def font(size):
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def feed_with(v):
    f = FT.Ft8Feed()
    f.value, f.ok_at, f.attempt_at = v, time.time(), time.time()
    return f


# a synthetic cycle 11 min after the capture (marked as such on the sheet) to show the alert kinds
LATER = """ORC_FT8_DECODE utc={T} mode=FT8 hz=1210 dt_ms=200 snr=-16 sync=80 kind=CQ call=ZL2AGY grid=RE78 msg="CQ ZL2AGY RE78"
ORC_FT8_DECODE utc={T} mode=FT8 hz=1820 dt_ms=100 snr=-9 sync=88 kind=QSO call=VE6KD grid=DO33 msg="K5NWZ VE6KD DO33"
ORC_FT8_DECODE utc={T} mode=FT8 hz=640 dt_ms=300 snr=-20 sync=72 kind=QSO call=JA8NRS grid=QN02 msg="N7DNF JA8NRS QN02"
ORC_FT8_DECODE utc={T} mode=FT8 hz=2290 dt_ms=150 snr=-4 sync=90 kind=QSO call=W7ZZ grid=DN17 msg="{ME} W7ZZ DN17"
"""


def build(text: str, mycall: str = "", now: float | None = None, split: bool = True, later: bool = False):
    """Feed the capture through Ft8Source like live polls would (older cycles first, so
    repeats / new-grid alerts happen as they would on air)."""
    st, decs = FT.parse_serial_reply(text)
    src = FT.Ft8Source()
    old = LF._setting
    LF._setting = lambda k: mycall if k == "livedata_ft8_mycall" else old(k)
    try:
        slots = sorted({d["utc"] for d in decs})
        if split and len(slots) > 1:
            src.merge(st, [d for d in decs if d["utc"] == slots[0]], "serial", now=slots[0] + 14)
            for s in slots[1:]:
                src.merge(st, [d for d in decs if d["utc"] <= s], "serial", now=s + 14)
        v = src.merge(st, decs, "serial", now=now or (max(slots) + 14 if slots else time.time()))
        if later and slots:
            t = max(slots) + 660
            _, extra = FT.parse_serial_reply(LATER.replace("{T}", str(t)).replace("{ME}", mycall or "K7ABC"))
            v = src.merge(st, extra + decs, "serial", now=t + 14)
    finally:
        LF._setting = old
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "ft8_contact.png"))
    ap.add_argument("--capture", default=str(ROOT / "tests" / "fixtures" / "livedata" / "ft8_serial.txt"))
    ap.add_argument("--live", action="store_true", help="read OrcSDR once (read-only queries)")
    ap.add_argument("--mycall", default="JA8NRS", help="callsign for the 'my call' scenario rows")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--scale", type=int, default=4)
    a = ap.parse_args()
    label = "saved capture " + Path(a.capture).name
    text = Path(a.capture).read_text(encoding="utf-8")
    if a.live:
        try:
            st, decs, via = FT.SOURCE.query()
            label = f"LIVE via {via} " + time.strftime("%H:%M PT", time.localtime())
            v_live = FT.SOURCE.merge(st, decs, via)
        except Exception as e:  # noqa: BLE001
            print(f"live read failed ({e}); using the capture", file=sys.stderr)
            v_live = None
    else:
        v_live = None
    v = v_live or build(text)
    now = (v["latest"] or time.time()) + 21.0 if not v_live else time.time()
    rows = [(s.key, s.short, v, now, label) for s in F8.SCREENS_FT8]
    rows.insert(1, ("ft8_ticker", "TICKER (continued)", v, now, label))
    # scenarios
    v_me = build(text, mycall=a.mycall)
    rows.append(("ft8_ticker", f"TICKER ({a.mycall} as my call)", v_me, now, "same capture, my call = " + a.mycall))
    v_al = build(text, mycall="K7XYZ", later=True)
    nl = v_al["latest"] + 21.0
    rows.append(("ft8_alert", "ALERTS (synthetic later cycle)", v_al, nl, "+11 min: ZL, JA, new grid, K7XYZ called"))
    rows.append(("ft8_map", "MAP (with synthetic DX)", v_al, nl, "+11 min synthetic cycle"))
    v_idle = build(text)
    v_idle = dict(v_idle, idle=True)
    rows.append(("ft8_ticker", "TICKER idle (no decodes 45 s+)", v_idle, now + 60, "smart idle state"))
    rows.append(("ft8_cycle", "CYCLE (mid-slot countdown)", v, now + 7, "7 s later"))

    sc, nf = a.scale, a.frames
    fw, fh = 64 * sc, 16 * sc
    label_w, pad, row_h = 330, 8, fh + 16
    Wd = label_w + nf * (fw + pad) + pad
    head = 54
    sheet = Image.new("RGB", (Wd, head + len(rows) * row_h + 10), (13, 15, 18))
    d = ImageDraw.Draw(sheet)
    f_b, f_s, f_h = font(15), font(12), font(20)
    st = v.get("status") or {}
    d.text((pad, 8), f"OrcPixels FT8 / FT4 Live Data: 8 screens  |  OrcSDR {st.get('band', '?')} {st.get('mode', '?')}, "
           f"{len(v.get('decodes') or [])} decodes, {v.get('unique')} stations, home {v.get('home')}",
           fill=(225, 238, 255), font=f_h)
    d.text((pad, 33), f"{label}  |  rendered " + time.strftime("%Y-%m-%d %H:%M", time.localtime()) +
           " PT  |  frames sampled across each seamless loop  |  nothing sent to the sign", fill=(125, 135, 148), font=f_s)
    summary = {}
    for r, (k, title, vv, nw, note) in enumerate(rows):
        y = head + r * row_h
        f = feed_with(vv)
        d.rectangle((0, y, Wd, y + row_h - 4), fill=(21, 24, 29) if r % 2 == 0 else (17, 19, 24))
        d.text((pad, y + 6), f"{r + 1:2d}. {title}", fill=(225, 238, 255), font=f_b)
        d.text((pad, y + 27), k, fill=(125, 135, 148), font=f_s)
        n = SC.loop_len(k, f)
        d.text((pad, y + 44), f"{note}  |  loop {n} frames"[:60], fill=(110, 240, 166), font=f_s)
        if k == "ft8_ticker" and n > 4:            # consecutive 64 px windows: reads as one strip
            start = 4 * nf if "continued" in title else 0
            ks = [(start + 4 * i) % n for i in range(nf)]
        else:
            ks = sorted({int(i * n / nf) for i in range(nf)})
        for j, kk in enumerate(ks):
            sheet.paste(led_frame(SC.render(k, f, kk, nw + kk), sc).convert("RGB"), (label_w + pad + j * (fw + pad), y + 6))
        summary[f"{r + 1}:{k}"] = {"title": title, "loop": n}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(a.out)
    print(json.dumps({"out": a.out, "data": label, "decodes": len(v.get("decodes") or []), "rows": summary}, indent=1))


if __name__ == "__main__":
    main()
