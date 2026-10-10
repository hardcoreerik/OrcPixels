"""Soak-test OrcSDR Live through the real controller + SignClient (web server stopped).

    python tools/soak_live.py --seconds 130 [--transport fast|legacy] [--no-waterfall]
Prints a JSON line every 10 s and a summary (fps, ms/frame, bytes, failures, reconnects).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time

from orcpixels.ble_sign import SignClient
from orcpixels.orcsdr_live import OrcSDRLive


class SynthClient:
    """Stands in for OrcSDRClient when the device is offline: same call shape + latency."""
    base_url = "synthetic"

    def __init__(self):
        self.i = 0

    async def status(self):
        from orcpixels.sources.orcsdr import OrcSDRStatus
        await asyncio.sleep(0.15)
        return OrcSDRStatus(mode="FM", frequency_hz=96_100_000, span_hz=2_400_000,
                            signal_dbfs=-22.0 + random.uniform(-1, 1), receiving=True,
                            rtl_ready=True, fetched_at=time.time())

    async def spectrum_raw(self):
        await asyncio.sleep(0.15)
        self.i += 1
        c = 20 + 15 * math.sin(self.i / 8)
        out = []
        for x in range(64):
            v = random.uniform(0, 0.3) + math.exp(-((x - 32) / 1.5) ** 2) \
                + 0.7 * math.exp(-((x - c) / 2.0) ** 2)
            out.append(int(min(1.0, v) * 255))
        return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=130)
    ap.add_argument("--transport", default="fast")
    ap.add_argument("--no-waterfall", action="store_true")
    ap.add_argument("--interval", type=float, default=0.0)
    ap.add_argument("--chunk", type=int, default=None, help="override FAST_CHUNK")
    ap.add_argument("--poll", type=float, default=None, help="override SPECTRUM_POLL_S")
    ap.add_argument("--synthetic", action="store_true",
                    help="fake OrcSDR client (noise floor + carrier + wandering signal, 150 ms latency)")
    a = ap.parse_args()
    import orcpixels.orcsdr_live as L
    if a.chunk:
        L.FAST_CHUNK = a.chunk
    if a.poll:
        L.SPECTRUM_POLL_S = a.poll
    sign = SignClient()
    await sign.connect()
    connects = {"n": 0}
    orig = sign._connect_locked

    async def counted(*args, **kw):
        was = sign.connected
        await orig(*args, **kw)
        if not was:
            connects["n"] += 1
    sign._connect_locked = counted
    live = OrcSDRLive(sign)
    if a.synthetic:
        live.client = SynthClient()
    from collections import deque
    live.send_ms = deque(maxlen=1_000_000)
    from orcpixels import settings as S
    S.DEFAULTS  # transport is read from settings in start(); override afterwards
    await live.start(waterfall=not a.no_waterfall, interval=a.interval)
    live.transport = a.transport
    t0 = time.monotonic()
    while time.monotonic() - t0 < a.seconds:
        await asyncio.sleep(10)
        i = live.info()
        i["t"] = round(time.monotonic() - t0)
        i["reconnects"] = connects["n"]
        i["device_error"] = live.device_error
        print(json.dumps(i), flush=True)
    el = time.monotonic() - t0
    info = live.info()
    await live.stop()
    all_ms = sorted(live.send_ms)
    print(json.dumps({"summary": True, "transport": a.transport, "waterfall": live.waterfall,
                      "seconds": round(el), "chunk": L.FAST_CHUNK, "poll_s": L.SPECTRUM_POLL_S, "frames": info["frames_sent"],
                      "fps": round(info["frames_sent"] / el, 2), "bytes_avg": info["bytes_avg"],
                      "ms_med": round(all_ms[len(all_ms) // 2]) if all_ms else None,
                      "ms_p95": round(all_ms[int(len(all_ms) * .95)]) if all_ms else None,
                      "ms_max": round(all_ms[-1]) if all_ms else None,
                      "send_failures": info["send_failures"], "reconnects": connects["n"],
                      "last_error": sign.state.last_error}), flush=True)
    await sign.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
