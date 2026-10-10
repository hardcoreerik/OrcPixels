"""Every (non-backup) animated preset must loop seamlessly: see tools/loopcheck.py."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from loopcheck import animated_presets, loop_metric  # noqa: E402
from orcpixels import presets as PR  # noqa: E402


@pytest.mark.parametrize("name", animated_presets())
def test_seamless_loop(name):
    m = loop_metric(PR.file_path(PR.get(name)).read_bytes())
    print(name, m)
    assert m["ok"], f"{name}: loop seam {m}"
