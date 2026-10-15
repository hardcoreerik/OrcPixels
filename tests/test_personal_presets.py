"""Metadata / size checks for the 48 tailored p_<family>_<name> presets.

Seamless-loop checks for these GIFs live in tests/test_loops.py (auto-parametrized).
"""
import json
from pathlib import Path

import pytest

PRESETS = Path(__file__).resolve().parents[1] / "presets"
FAMILIES = {"proj", "rf", "orc", "code", "hw", "amb"}
PERSONAL = sorted(p for p in PRESETS.glob("p_*.json")
                  if not p.stem.startswith(("p_orco_", "p_sig_")))  # orc_co / rf_sig: own test files


def test_fifty_personal_presets():
    assert len(PERSONAL) == 48


@pytest.mark.parametrize("path", PERSONAL, ids=lambda p: p.stem)
def test_personal_preset_metadata(path):
    meta = json.loads(path.read_text(encoding="utf-8"))
    assert meta["name"] == path.stem
    assert meta["family"] in FAMILIES
    assert path.stem.startswith(f"p_{meta['family']}_")
    assert meta["description"] and "why_erik" not in meta   # no personal notes in shipped metadata
    assert meta["live_data"] is False
    gif = PRESETS / meta["file"]
    assert gif.exists()
    assert gif.stat().st_size < 15 * 1024, "keep BLE uploads short (~1.1 KB/s)"
    from PIL import Image
    im = Image.open(gif)
    assert im.size == (64, 16)
