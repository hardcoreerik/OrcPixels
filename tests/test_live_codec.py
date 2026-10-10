import io
import math

from PIL import Image

from orcpixels.live_codec import png_palette, png_rgb
from orcpixels.orcsdr_live import DEFAULT_INTERVAL, DEFAULT_TRANSPORT, DEFAULT_WATERFALL, render_frame
from orcpixels.sources.orcsdr import OrcSDRStatus


def _frames(wf):
    st = OrcSDRStatus(mode="FM", frequency_hz=96_100_000, signal_dbfs=-22.0, receiving=True, rtl_ready=True)
    hist = []
    for i in range(6):
        b = [min(1.0, 0.1 + math.exp(-((x - 32) / 1.5) ** 2) + 0.7 * math.exp(-((x - 20 - i) / 2) ** 2))
             for x in range(64)]
        hist.append(b)
        yield render_frame(st, b, b, hist, waterfall=wf)


def test_palette_png_lossless_for_spectrum_and_smaller():
    for img in _frames(False):
        data = png_palette(img)
        back = Image.open(io.BytesIO(data))
        assert back.mode == "P" and back.size == (64, 16)
        assert back.convert("RGB").tobytes() == img.tobytes()
        assert len(data) < len(png_rgb(img))


def test_palette_png_waterfall_at_most_16_colours():
    for img in _frames(True):
        back = Image.open(io.BytesIO(png_palette(img))).convert("RGB")
        assert len(back.getcolors(4096)) <= 16
        assert back.size == (64, 16)


def test_live_defaults():
    assert DEFAULT_TRANSPORT == "legacy" and DEFAULT_WATERFALL is True and DEFAULT_INTERVAL == 0.0
