"""Adaptive layout (docs/DESIGN.md): classification, tiers, adaptive scenes at the target
sizes, fixed presets + Live screens at other sizes, compatibility rating, golden references."""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from orcpixels import adaptive as AD
from orcpixels import layout as L
from orcpixels import scenes as SCN
from orcpixels.loopmetric import loop_metric_frames

ROOT = Path(__file__).resolve().parents[1]
TARGETS = SCN.TEST_SIZES                      # 16x16, 64x32, 256x16, 96x24


@pytest.mark.parametrize("w,h,fam", [(16, 16, "square"), (64, 64, "square"), (32, 16, "wide"), (64, 32, "wide"),
                                     (128, 64, "wide"), (64, 16, "ultrawide"), (96, 24, "ultrawide"),
                                     (96, 32, "ultrawide"), (128, 16, "strip"), (256, 16, "strip"),
                                     (320, 16, "strip"), (16, 32, "portrait"), (32, 64, "portrait"),
                                     (16, 64, "tall")])
def test_classify(w, h, fam):
    assert L.classify(w, h) == fam


def test_orientation_matters_and_tiers():
    assert L.classify(64, 16) == "ultrawide" and L.classify(16, 64) == "tall"
    assert L.tier(16, 16) == "tiny" and L.tier(64, 16) == "small" and L.tier(64, 32) == "medium"
    assert L.tier(64, 64) == "large" and L.tier(128, 128) == "xl" and L.tier(256, 16) == "small"
    assert L.Ctx(96, 24).describe()["ratio_label"] == "4.00:1"


def test_rating():
    assert L.rate([(64, 16)], 64, 16) == "native"
    assert L.rate([(64, 16)], 128, 32) == "scaled"
    assert L.rate([(64, 16)], 96, 24) == "fallback"
    assert L.rate([(64, 16)], 96, 24, adaptive=True) == "adaptive"


@pytest.mark.parametrize("name", list(SCN.SCENES))
@pytest.mark.parametrize("size", SCN.PREVIEW_SIZES)
def test_adaptive_scene_renders_and_loops(name, size):
    fr, durs = SCN.frames(name, *size)
    assert all(f.size == size for f in fr) and len(fr) == len(durs) >= 2
    assert loop_metric_frames(fr)["ok"], (name, size)
    lit = sum(1 for p in fr[0].getdata() if sum(p) > 30)
    assert lit >= max(8, size[0] * size[1] // 60), (name, size, lit)


@pytest.mark.parametrize("name", list(SCN.SCENES))
def test_adaptive_layout_differs_by_shape(name):
    """Not just a rescale: the 64x32 composition is not the 64x16 frame stretched."""
    a = SCN.frames(name, 64, 16)[0][0].resize((64, 32), Image.Resampling.NEAREST)
    b = SCN.frames(name, 64, 32)[0][0]
    assert a.tobytes() != b.tobytes()


def test_every_preset_at_target_sizes():
    from orcpixels import presets as PR
    names = [p["name"] for p in PR.list_presets((64, 16))]
    assert len(names) >= 90
    for n in names:
        for (w, h) in TARGETS:
            fr, durs, rating = AD.render_preset(n, w, h, "contain")
            assert fr[0].size == (w, h) and rating in L.RATINGS
        assert AD.rating_for(n, 64, 16) == "native"
    fr, _, rating = AD.render_preset(names[0], 128, 32, "adaptive")
    assert rating == "scaled" and fr[0].size == (128, 32)
    assert AD.render_preset(names[0], 96, 24, "crop")[2] == "fallback"


def test_every_live_screen_at_target_sizes(monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    import led64_sheet
    from orcpixels import livedata_screens as SC
    feeds, _ = led64_sheet.fixture_feeds()
    for key in SC.SCREENS:
        for (w, h) in TARGETS:
            m = L.best_master(((64, 16), (64, 64)), w, h)
            img = SC.render(key, feeds[key], 0, None, size=m)
            out, rating = L.place([img], [m], w, h, "contain")
            assert out[0].size == (w, h) and rating in ("scaled", "fallback")


def test_golden_references_unchanged():
    """Exact 64x16 / 64x64 output must not change unless deliberately updated
    (python tools/golden.py --update)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import golden
    cur = golden.compute()
    ref_assets = json.loads((ROOT / "tests/golden/assets.json").read_text(encoding="utf-8"))
    assert cur["assets"] == ref_assets
    ref_live = json.loads((ROOT / "tests/golden/live.json").read_text(encoding="utf-8"))
    changed = [k for k in ref_live if cur["live"].get(k) != ref_live[k]]
    assert not changed, changed


def test_viewing_size_vs_physical_rotation():
    from orcpixels import drivers as DR
    d = DR.make({"driver": "virtual", "width": 16, "height": 64, "layout": {"rotation": 90}, "options": {"write_files": False}})
    out = d.prepare(Image.new("RGB", (16, 64), (255, 0, 0)))
    assert out.size == (64, 16)
