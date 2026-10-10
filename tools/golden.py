"""Golden references (docs/DESIGN.md, phase 5): every shipped 64x16 / 64x64 preset asset
and every Live Data screen rendered from the recorded fixtures at a fixed time, hashed.
tests/test_adaptive.py fails when any of them changes.

    python tools/golden.py            # check
    python tools/golden.py --update   # deliberate update: rewrite tests/golden/*.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
GOLD = ROOT / "tests" / "golden"
FIXED_NOW = 1_759_800_000.0          # 2025-10-07 01:20 UTC, inside the fixture window


def assets() -> dict:
    out = {}
    for d in ("presets", "presets64"):
        for p in sorted((ROOT / d).iterdir()):
            if p.suffix in (".gif", ".png") and "_preview_" not in p.name:
                out[f"{d}/{p.name}"] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


def live() -> dict:
    import time
    from unittest import mock

    import os

    import led64_sheet
    from orcpixels import livedata_screens as SC
    from orcpixels import place as PLACE
    from orcpixels.sources import livefeeds as LF
    from tests.conftest import SAMPLE, SAMPLE_SETTINGS    # same sample place / settings as the tests
    orig = LF._setting
    with mock.patch("time.time", return_value=FIXED_NOW), mock.patch.object(PLACE, "OVERRIDE", dict(SAMPLE)), \
            mock.patch.dict(os.environ, {"ORCPIXELS_TZ": "us-pacific"}), \
            mock.patch.object(LF, "_setting", lambda k: orig(k) or SAMPLE_SETTINGS.get(k)):
        feeds, _ = led64_sheet.fixture_feeds()
        out = {}
        for key in sorted(SC.SCREENS):
            f = feeds[key]
            for size in ((64, 16), (64, 64)):
                n = SC.loop_len(key, f, size)
                h = hashlib.sha256()
                for i in range(min(n, 8)):
                    h.update(SC.render(key, f, i, FIXED_NOW, size=size).tobytes())
                out[f"{key}@{size[0]}x{size[1]}"] = h.hexdigest()[:16]
    _ = time
    return out


def compute() -> dict:
    return {"assets": assets(), "live": live()}


def main():
    cur = compute()
    if "--update" in sys.argv:
        GOLD.mkdir(parents=True, exist_ok=True)
        for k, v in cur.items():
            (GOLD / f"{k}.json").write_text(json.dumps(v, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("updated", {k: len(v) for k, v in cur.items()})
        return 0
    bad = 0
    for k, v in cur.items():
        ref = json.loads((GOLD / f"{k}.json").read_text(encoding="utf-8"))
        for name in sorted(set(ref) | set(v)):
            if ref.get(name) != v.get(name):
                print(f"{k}: {name} changed"); bad += 1
    print("ok" if not bad else f"{bad} changed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
