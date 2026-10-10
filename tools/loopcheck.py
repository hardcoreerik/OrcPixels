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
Run:  python tools/loopcheck.py [preset ...]   (default: all non-backup GIF presets)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orcpixels import presets as PR  # noqa: E402
from orcpixels.loopmetric import _delta, _mc_delta, loop_metric, loop_metric_frames  # noqa: E402,F401

BACKUP_RE = re.compile(r"_v\d+$")


def animated_presets(include_backups: bool = False) -> list[str]:
    return [m["name"] for m in PR.list_presets()
            if m.get("kind") == "gif" and (include_backups or not BACKUP_RE.search(m["name"]))]


def main(names=None) -> int:
    names = names or animated_presets()
    bad = 0
    print(f"{'preset':24} {'N':>3} {'seam':>6} {'med':>6} {'ratio':>6} | {'seamMC':>6} {'medMC':>6} {'ratioMC':>7}  result")
    for n in names:
        m = loop_metric(PR.file_path(PR.get(n)).read_bytes())
        bad += not m["ok"]
        print(f"{n:24} {m['frames']:3} {m['seam']:6} {m['median']:6} {m['ratio']:6} | "
              f"{m['seam_mc']:6} {m['median_mc']:6} {m['ratio_mc']:7}  {'seamless' if m['ok'] else 'JUMP'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
