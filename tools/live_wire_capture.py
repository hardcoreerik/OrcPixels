"""Offline: capture the exact GATT writes of one live frame per transport (no sign).

  python tools/live_wire_capture.py [--screen xray]

A fake BleakClient records every write_gatt_char (uuid, bytes, response flag, time) and
answers the final write with the real sign's 05 00 02 00 03. Drives the real engine
(DataLive._send_frame) and SignClient code paths, so what is printed is what goes on air.
"""
from __future__ import annotations

import argparse
import asyncio
import binascii
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from orcpixels import protocol as P  # noqa: E402,F401
from orcpixels.orcsdr_live import TRANSPORTS  # noqa: E402


class FakeClient:
    is_connected = True
    mtu_size = 517

    def __init__(self, sign):
        self.sign, self.writes = sign, []
        self.expect = 0

    async def write_gatt_char(self, uuid, data, response=False):
        data = bytes(data)
        self.writes.append({"uuid": uuid[4:8], "len": len(data), "response": response,
                            "t": time.perf_counter(), "data": data})
        await asyncio.sleep(0.001 if response else 0)
        self.expect += len(data)
        # the sign acks once the declared frame length has arrived
        buf = b"".join(w["data"] for w in self.writes[self._start:])
        if len(buf) >= 2 and len(buf) >= int.from_bytes(buf[:2], "little"):
            cmd = buf[2:4]
            reply = {b"\x02\x00": b"\x05\x00\x02\x00\x03", b"\x06\x80": b"\x05\x00\x06\x80\x01"}.get(
                cmd, b"\x05\x00" + cmd + b"\x01")
            self._start = len(self.writes)
            self.sign._notify(None, bytearray(reply))

    _start = 0


def make_sign():
    from orcpixels.ble_sign import SignClient
    s = SignClient(address=None, rotate_180=True)
    s._client = FakeClient(s)
    s.state.connected = True
    s._dev_orientation = None           # as after a (re)connect
    return s


def parse(writes):
    buf = b"".join(w["data"] for w in writes)
    out = []
    i = 0
    while i + 2 <= len(buf):
        n = int.from_bytes(buf[i:i + 2], "little")
        f = buf[i:i + n]
        d = {"frame_len": n, "cmd": f[2:4].hex()}
        if f[2:4] == b"\x02\x00":
            pl = f[15:]
            d.update(option=f[4], size=int.from_bytes(f[5:9], "little"), payload=len(pl),
                     crc_ok=int.from_bytes(f[9:13], "little") == (binascii.crc32(pl) & 0xFFFFFFFF),
                     tail=f[13:15].hex(), png_colortype=pl[25] if len(pl) > 25 else None,
                     png_bitdepth=pl[24] if len(pl) > 24 else None, png_sha=binascii.crc32(pl))
        else:
            d["bytes"] = f.hex()
        out.append(d)
        i += max(n, 1)
    return out


async def capture(transport: str, img: Image.Image):
    from orcpixels.livedata import DataLive
    sign = make_sign()
    live = DataLive(sign)
    live.transport = transport
    live._seq = sign.display_seq
    await live._send_frame(img)
    w = sign._client.writes
    gaps = [round((b["t"] - a["t"]) * 1000, 2) for a, b in zip(w, w[1:])]
    return {"transport": transport, "writes": len(w), "chunk_lens": [x["len"] for x in w],
            "response": sorted({x["response"] for x in w}), "gaps_ms": gaps,
            "resp_seq": [x["response"] for x in w],
            "commands": parse(w), "raw": b"".join(x["data"] for x in w)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", default="xray")
    a = ap.parse_args()
    from orcpixels import livedata_screens as SC
    feeds = SC.make_feeds()
    f = feeds[a.screen]
    f.value = None
    import random
    rnd = random.Random(7)
    busy = Image.new("RGB", (64, 16))
    busy.putdata([tuple(rnd.choice((0, 40, 120, 255)) for _ in range(3)) for _ in range(64 * 16)])
    for label, img in (("clock frame (1 chunk)", SC.render("clock", feeds["clock"], 0, 1791000000.0)),
                       ("busy frame (multi-chunk)", busy)):
        print(f"######## {label}")
        res = {t: asyncio.run(capture(t, img)) for t in TRANSPORTS}
        for t, r in res.items():
            print(f"== {t}: {r['writes']} writes, chunks {r['chunk_lens']}, response per write "
                  f"{[w for w in r['resp_seq']]}")
            for c in r["commands"]:
                print("   ", c)
        base = res["legacy"]["raw"]
        for t, r in res.items():
            print(f"{t:10} bytes identical to legacy: {r['raw'] == base}; write types identical: "
                  f"{r['resp_seq'] == res['legacy']['resp_seq']}; chunking identical: "
                  f"{r['chunk_lens'] == res['legacy']['chunk_lens']}")


if __name__ == "__main__":
    main()
