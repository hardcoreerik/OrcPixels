#!/usr/bin/env python3
"""Read-only probe for the OrcSDR web console (Tab5 companion page).

Only issues HTTP GETs:
  GET /api/status    -> JSON status (freq/mode/signal + 64-bin uint8 spectrum)
  GET /api/spectrum  -> 64 raw bytes, uint8 0..255 per bin (fresher spectrum)
Never touches /api/action (POST, mutating) or the audio endpoints.

Usage:
  python orcsdr_probe.py [--host 192.0.2.10] [--frames N] [--interval 0.25] [--no-spectrum-endpoint]
Standard library only.
"""
import argparse
import json
import time
import urllib.request

COLS, ROWS = 64, 16


def http_get(url, timeout=3.0):
    req = urllib.request.Request(url, method="GET", headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_status(host):
    return json.loads(http_get(f"http://{host}/api/status").decode("utf-8", "replace"))


def get_spectrum(host):
    """64 bytes, each 0..255. Device normalises: (level - (mean-4dB)) / 24dB, clamped."""
    return list(http_get(f"http://{host}/api/spectrum"))


def downsample(bins, cols=COLS, rows=ROWS, vmax=255):
    """Max-pool any-length bin list to `cols` columns, scaled to 0..rows."""
    n = len(bins)
    if n == 0:
        return [0] * cols
    out = []
    for c in range(cols):
        a = c * n // cols
        b = max(a + 1, (c + 1) * n // cols)
        v = max(bins[a:b])
        out.append(max(0, min(rows, round(v * rows / vmax))))
    return out


def render(cols, rows=ROWS):
    lines = []
    for r in range(rows, 0, -1):
        lines.append("".join("#" if h >= r else "." for h in cols))
    return "\n".join(lines)


def fmt_freq(hz):
    if not hz:
        return "-"
    return f"{hz/1e6:.3f} MHz" if hz >= 1e6 else f"{hz/1e3:.1f} kHz"


def print_status(s):
    print(f"mode={s.get('mode')!r} freq={fmt_freq(s.get('frequency_hz'))} "
          f"span={s.get('span_hz')} Hz bw={s.get('filter_bandwidth_hz')} Hz "
          f"signal={s.get('signal_dbfs')} dBFS L/R={s.get('left_dbfs')}/{s.get('right_dbfs')}")
    print(f"receiving={s.get('receiving')} rtl_ready={s.get('rtl_ready')} "
          f"stereo={s.get('stereo')} rds_locked={s.get('rds_locked')} "
          f"PS={str(s.get('program_service','')).strip()!r} "
          f"RT={str(s.get('radio_text','')).strip()!r}")
    print(f"ip={s.get('wifi_ip')} sps={s.get('effective_sps')} vol={s.get('volume')} "
          f"batt={s.get('battery_percent')}% clock={s.get('clock')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.0.2.10")
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--no-spectrum-endpoint", action="store_true",
                    help="use status.spectrum (updated ~every 400 ms) instead of /api/spectrum")
    a = ap.parse_args()

    s = get_status(a.host)
    print_status(s)
    for i in range(a.frames):
        if i:
            time.sleep(a.interval)
        bins = s.get("spectrum", []) if a.no_spectrum_endpoint else get_spectrum(a.host)
        cols = downsample(bins)
        print(f"\nframe {i}: {len(bins)} bins raw min/max={min(bins, default=0)}/{max(bins, default=0)}")
        print("cols(0-16):", cols)
        print(render(cols))


if __name__ == "__main__":
    main()
