"""The 30 'Oregon Radio Company' / TheOrc.dev presets (family orc_co, tools/make_orco_presets.py).

Seamless-loop checks for these GIFs also run in tests/test_loops.py (auto-parametrized).
"""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "presets"
ORCO = sorted(PRESETS.glob("p_orco_*.json"))


def test_thirty_orco_presets():
    assert len(ORCO) == 30


def test_some_use_the_domain():
    used = [p.stem for p in ORCO if json.loads(p.read_text(encoding="utf-8")).get("uses_theorc_dev")]
    assert len(used) >= 3, used


@pytest.mark.parametrize("path", ORCO, ids=lambda p: p.stem)
def test_orco_metadata_and_size(path):
    meta = json.loads(path.read_text(encoding="utf-8"))
    assert meta["name"] == path.stem and meta["family"] == "orc_co"
    assert meta["kind"] == "gif" and meta["live_data"] is False
    assert meta["description"] and meta["title"]
    gif = PRESETS / meta["file"]
    assert gif.stat().st_size <= 12 * 1024, "BLE budget: ~12 KB sends in under ~10 s"
    assert meta["bytes"] == gif.stat().st_size
    im = Image.open(gif)
    assert im.size == (64, 16)
    assert 1 < im.n_frames <= meta["frames"]       # Pillow merges identical consecutive frames (holds)
    for k in ("still", "preview"):
        assert (PRESETS / meta[k]).exists()


@pytest.mark.parametrize("path", ORCO, ids=lambda p: p.stem)
def test_orco_loops_seamless(path):
    from orcpixels.loopmetric import loop_metric
    meta = json.loads(path.read_text(encoding="utf-8"))
    m = loop_metric((PRESETS / meta["file"]).read_bytes())
    assert m["ok"], m


def test_at_least_five_use_the_oregon_outline():
    used = [p.stem for p in ORCO if json.loads(p.read_text(encoding="utf-8")).get("uses_oregon_outline")]
    assert len(used) >= 10, used


@pytest.mark.parametrize("h", [16, 12, 9])
def test_oregon_outline_assets(h):
    for kind in ("outline", "fill"):
        im = Image.open(ROOT / "assets" / f"oregon_{kind}_{h}.png")
        assert im.height == h and 1.25 < im.width / h < 1.45     # Oregon is ~1.36:1 at this projection


def test_oregon_shape_features():
    """Shape sanity from the real geometry: flat south border, Columbia notch in the north,
    coast on the west, Snake River bulge in the NE then a straight east edge."""
    sys.path.insert(0, str(ROOT / "tools"))
    import oregon_shape as OR
    m = OR.mask(16)
    w = m.width
    rows = [[x for x in range(w) if m.getpixel((x, y))] for y in range(16)]
    assert len(rows[15]) >= w - 3                                   # straight 42N south border
    assert max(rows[1]) > max(rows[8])                             # NE (Wallowas/Snake) sticks out east
    assert max(r[-1] for r in rows[9:15]) - min(r[-1] for r in rows[9:15]) <= 1   # straight 117W east edge
    assert all(r[0] <= 2 for r in rows[3:15])                      # Pacific coast on the west
    top = [min((y for y in range(16) if m.getpixel((x, y))), default=16) for x in range(w)]
    assert max(top[4:12]) >= 2 and top[w // 2 + 4] <= 1           # Columbia dips south of 46N, flat 46N east part
