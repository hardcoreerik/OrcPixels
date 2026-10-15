"""The 10 'RF / Signal' presets (family rf_sig, tools/make_rfsig_presets.py), built from the
Midnight Synth concept boards. Each one is native at its board size and has a hand-laid
companion at the other size, so both preset lists (64x16 and 64x64) show the whole family."""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
P16, P64 = ROOT / "presets", ROOT / "presets64"
SIG16 = sorted(P16.glob("p_sig_*.json"))
SIG64 = sorted(P64.glob("p_sig_*.json"))
NATIVE16 = {"p_sig_foxhunt", "p_sig_jamstorm", "p_sig_modwave", "p_sig_beacon", "p_sig_tuner"}
NATIVE64 = {"p_sig_satuplink", "p_sig_syncgrid", "p_sig_hophop", "p_sig_spectrum", "p_sig_aprs"}


def test_ten_concepts_at_both_sizes():
    assert {p.stem for p in SIG16} == NATIVE16 | NATIVE64
    assert {p.stem for p in SIG64} == NATIVE16 | NATIVE64


@pytest.mark.parametrize("path", SIG16 + SIG64, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_metadata_size_and_budget(path):
    meta = json.loads(path.read_text(encoding="utf-8"))
    big = path.parent == P64
    assert meta["name"] == path.stem and meta["family"] == "rf_sig"
    assert meta["kind"] == "gif" and meta["live_data"] is False
    assert meta["title"] and meta["description"]
    assert meta["native_size"] == ("64x16" if path.stem in NATIVE16 else "64x64")
    gif = path.parent / meta["file"]
    assert meta["bytes"] == gif.stat().st_size
    assert gif.stat().st_size <= (34 if big else 12) * 1024, "BLE budget"
    im = Image.open(gif)
    assert im.size == ((64, 64) if big else (64, 16))
    assert 1 < im.n_frames <= meta["frames"]
    assert (path.parent / meta["preview"]).exists()
    if not big:
        assert (P16 / meta["still"]).exists()


@pytest.mark.parametrize("path", SIG16 + SIG64, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_loops_seamless(path):
    from orcpixels.loopmetric import loop_metric
    meta = json.loads(path.read_text(encoding="utf-8"))
    m = loop_metric((path.parent / meta["file"]).read_bytes())
    assert m["ok"], m


@pytest.mark.parametrize("name", sorted(NATIVE16 | NATIVE64))
def test_native_at_both_master_sizes(name):
    from orcpixels import adaptive as AD
    assert AD.rating_for(name, 64, 16) == "native"
    assert AD.rating_for(name, 64, 64) == "native"


def test_aprs_uses_placeholder_callsign_only():
    sys.path.insert(0, str(ROOT / "tools"))
    import make_rfsig_presets as M
    text = " ".join(v for _, v in M.APRS_LINES) + " " + " ".join(M.APRS_ROLL)
    assert "N0CALL" in text
    calls = [w for w in text.split() if any(c.isdigit() for c in w) and any(c.isalpha() for c in w)
             and not w.endswith(("N", "W", "KT"))]
    assert all(w.startswith("N0CALL") for w in calls), calls
