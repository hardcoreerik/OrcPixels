from orcpixels.orcsdr_live import fmt_freq, render_frame
from orcpixels.sources.orcsdr import OrcSDRStatus, downsample


def test_downsample_shapes():
    assert downsample(list(range(64))) == [v / 255 for v in range(64)]
    assert len(downsample(list(range(2048)), 64, vmax=2047)) == 64
    assert downsample([], 64) == [0.0] * 64
    assert max(downsample([255] * 10)) == 1.0


def test_fmt_freq():
    assert fmt_freq(96_100_000) == "96.1"
    assert fmt_freq(162_550_000) == "162.55"
    assert fmt_freq(850_000) == "850K"
    assert fmt_freq(0) == "---"


def test_render_sizes():
    st = OrcSDRStatus.from_json({"mode": "FM", "frequency_hz": 96_100_000, "signal_dbfs": -22.4,
                                 "receiving": True, "rtl_ready": True})
    b = downsample([0, 128, 255] * 21 + [0])
    for wf in (False, True):
        img = render_frame(st, b, b, [b] * 5, waterfall=wf)
        assert img.size == (64, 16)
    assert render_frame(None, None, error="x").size == (64, 16)
