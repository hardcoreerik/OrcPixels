"""Benchmark live-frame upload variants on the real sign (stand-alone; web server must be stopped).

Encodings x transports, each for N frames of changing spectrum content (real OrcSDR data if
reachable via GET, else synthetic). Measures wall time from first GATT write to the final
'done' ack (05 00 02 00 03) per frame, records every fa03 notification, counts NG/timeouts.

    python tools/bench_live_ble.py [--frames 20] [--only enc:transport,...] [--soak SECONDS]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics as st
import time

from bleak import BleakClient

from orcpixels import protocol as P
from orcpixels.ble_sign import SignClient
from orcpixels.orcsdr_live import OrcSDRLive, render_frame
from orcpixels import live_codec as LC


def synth(i):
    """Synthetic spectrum: noise floor + a carrier + a wandering signal."""
    b = []
    for x in range(64):
        v = random.uniform(0.0, 0.25)
        v += 1.0 * math.exp(-((x - 32) / 1.5) ** 2)
        v += 0.7 * math.exp(-((x - (20 + 15 * math.sin(i / 4))) / 2.0) ** 2)
        b.append(min(1.0, v))
    return b


class Bench:
    def __init__(self, sign: SignClient):
        self.sign = sign
        self.c: BleakClient = sign._client
        self.notes: list[tuple[float, bytes]] = []
        self.evt = asyncio.Event()
        sign.on_notify.append(self._on)

    def _on(self, data: bytes):
        self.notes.append((time.perf_counter(), bytes(data)))
        self.evt.set()

    async def send(self, frame: bytes, chunk: int, response: bool, timeout: float = 5.0):
        """-> (ms to final ack, notes during frame, status) ; status 'ok'|'ng'|'timeout'"""
        self.notes.clear()
        self.evt.clear()
        t0 = time.perf_counter()
        for pos in range(0, len(frame), chunk):
            await self.c.write_gatt_char(P.WRITE_UUID, frame[pos:pos + chunk], response=response)
        end = t0 + timeout
        while True:
            for _, d in self.notes:
                if len(d) >= 5 and d[0] == 5 and d[4] == 3:
                    return (time.perf_counter() - t0) * 1000, list(self.notes), "ok"
                if len(d) >= 5 and d[0] == 5 and d[4] == 0:
                    return (time.perf_counter() - t0) * 1000, list(self.notes), "ng"
            left = end - time.perf_counter()
            if left <= 0:
                return (time.perf_counter() - t0) * 1000, list(self.notes), "timeout"
            self.evt.clear()
            try:
                await asyncio.wait_for(self.evt.wait(), left)
            except asyncio.TimeoutError:
                pass


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--only", default="")
    ap.add_argument("--soak", type=float, default=0)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--waterfall", action="store_true")
    a = ap.parse_args()

    sign = SignClient()
    await sign.connect()
    c = sign._client
    mtu = getattr(c, "mtu_size", None)
    props = None
    for s in c.services:
        for ch in s.characteristics:
            if ch.uuid.lower() == P.WRITE_UUID:
                props = ch.properties
                mw = getattr(ch, "max_write_without_response_size", None)
    print(json.dumps({"mtu": mtu, "write_props": props, "max_write_wo_rsp": mw}))
    await sign._ensure_orientation(0)
    b = Bench(sign)

    live = OrcSDRLive(sign=None)
    use_real = not a.synthetic
    if use_real:
        await live.sample_once()
        if live.status is None:
            print("OrcSDR unreachable -> synthetic spectrum"); use_real = False

    async def next_img(i, wf):
        if use_real:
            await live.sample_once()
            img = render_frame(live.status, live.bins, live.peaks, live.history, wf, None)
        else:
            bb = synth(i)
            live.history.append(bb)
            from orcpixels.sources.orcsdr import OrcSDRStatus
            stt = OrcSDRStatus(mode="FM", frequency_hz=96_100_000, signal_dbfs=-22.0,
                               receiving=True, rtl_ready=True)
            img = render_frame(stt, bb, bb, live.history, wf, None)
        return img.rotate(180) if sign.rotate_180 else img

    encs = {"png_rgb": LC.png_rgb, "png_pal": LC.png_palette}
    safe_chunk = 244
    full = (mtu - 3) if mtu else 244
    transports = {"244_rsp": (244, True), "244_norsp": (244, False),
                  f"mtu{full}_rsp": (full, True), f"mtu{full}_norsp": (full, False),
                  "360_rsp": (360, True), "500_rsp": (500, True)}
    only = [s for s in a.only.split(",") if s]
    combos = [(e, t) for e in encs for t in transports if not only or f"{e}:{t}" in only]

    results = []
    for wf in ([a.waterfall] if a.soak else [False, True]):
        for e, t in combos:
            chunk, rsp = transports[t]
            ms, sizes, bad, patterns = [], [], [], {}
            n = a.frames
            t_start = time.perf_counter()
            i = 0
            while True:
                if a.soak:
                    if time.perf_counter() - t_start > a.soak:
                        break
                elif i >= n:
                    break
                img = await next_img(i, wf)
                data = encs[e](img)
                frame = P.send_png(data).frames[0].data
                sizes.append(len(data))
                try:
                    dt, notes, status = await b.send(frame, chunk, rsp)
                except Exception as ex:  # noqa: BLE001
                    dt, notes, status = 0, [], f"err:{type(ex).__name__}:{ex}"
                pat = " ".join(d.hex() for _, d in notes)
                patterns[pat] = patterns.get(pat, 0) + 1
                if status != "ok":
                    bad.append(status)
                    if status.startswith("err") or not sign.connected:
                        print("  error, reconnecting:", status)
                        await sign._safe_disconnect(); await sign.connect(); b.c = sign._client
                        await sign._ensure_orientation(0)
                    else:
                        await asyncio.sleep(1.0)
                else:
                    ms.append(dt)
                i += 1
            wall = time.perf_counter() - t_start
            r = {"waterfall": wf, "enc": e, "transport": t, "frames": i,
                 "bytes_avg": round(st.mean(sizes)), "ms_med": round(st.median(ms)) if ms else None,
                 "ms_p95": round(sorted(ms)[int(len(ms) * .95) - 1]) if ms else None,
                 "ms_max": round(max(ms)) if ms else None,
                 "fps_wall": round(i / wall, 2), "bad": len(bad), "bad_kinds": sorted(set(bad)),
                 "ack_patterns": dict(sorted(patterns.items(), key=lambda kv: -kv[1])[:3])}
            results.append(r)
            print(json.dumps(r))
    await sign.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
