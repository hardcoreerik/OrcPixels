"""Animated-GIF import (Image Converter): all frames + timing + loop info, trim, speed,
adaptive/contain/crop at the selected display's size, legacy limits, Library save, playlist."""
import io
import json

import pytest
from PIL import Image, ImageDraw

from orcpixels import displays as D
from orcpixels import paths
from orcpixels import presets as PR
from orcpixels import studio as ST
from orcpixels.loopmetric import loop_metric_frames


def make_gif(n=20, size=(96, 96), durs=None, loop=0, partial=False, noisy=False):
    """A dot orbiting (seamless); per-frame durations; optionally partial frames (GIF
    disposal) and noise (big file)."""
    import math
    import random
    rnd = random.Random(1)
    frames = []
    for i in range(n):
        im = Image.new("RGB", size, (0, 0, 40))
        d = ImageDraw.Draw(im)
        a = 2 * math.pi * i / n
        cx, cy = size[0] / 2 + size[0] / 3 * math.cos(a), size[1] / 2 + size[1] / 3 * math.sin(a)
        d.ellipse([cx - 8, cy - 8, cx + 8, cy + 8], fill=(255, 160, 0))
        if noisy:
            for _ in range(size[0] * size[1] // 3):
                im.putpixel((rnd.randrange(size[0]), rnd.randrange(size[1])),
                            (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
        frames.append(im)
    durs = durs or [80] * n
    buf = io.BytesIO()
    kw = {"save_all": True, "append_images": frames[1:], "duration": durs, "disposal": 1 if partial else 2}
    if loop is not None:
        kw["loop"] = loop
    frames[0].save(buf, format="GIF", optimize=partial, **kw)
    return buf.getvalue()


@pytest.fixture
def disp(monkeypatch):
    """Select a display (size + driver) for the request, like the web layer does."""
    def use(w, h, driver="virtual"):
        d = {"id": f"t{w}x{h}", "driver": driver, "width": w, "height": h,
             **({"address": "AA:BB:CC:DD:EE:FF"} if driver == "ipixel" else {})}
        monkeypatch.setattr(D, "current", lambda: d)
        monkeypatch.setattr(D, "size", lambda: (w, h))
        monkeypatch.setattr(D, "is_ipixel", lambda dd=None: driver == "ipixel")
        return d
    return use


def test_source_info_frames_timing_loop():
    info = ST.source_info(make_gif(10, durs=[50, 150] * 5, loop=0))
    assert info["format"] == "GIF" and info["frames"] == 10 and info["duration_ms"] == 1000 and info["loop"] == 0
    once = ST.source_info(make_gif(4, loop=None))
    assert once["loop"] is None


def test_all_frames_decoded_with_disposal():
    data = make_gif(16, partial=True)
    fr, durs = ST.decode_frames(data)
    assert len(fr) == 16 and durs == [80] * 16
    # partial frames are composited: each frame shows exactly one dot (no trail)
    for f in fr:
        lit = sum(1 for p in f.convert("RGB").getdata() if p[0] > 200)
        assert 100 < lit < 400


def test_trim_and_speed():
    fr = list(range(10))
    durs = [100] * 10
    a, d = ST.trim(fr, durs, 250, 750)
    assert a == [2, 3, 4, 5, 6, 7] and sum(d) == 500 and d[0] == 50 and d[-1] == 50
    assert ST.trim(fr, durs, 0, 0) == (fr, durs)
    assert ST.speed_up([100, 100], 2.0) == [50, 50] and ST.speed_up([100], 0.5) == [200]
    with pytest.raises(ValueError):
        ST.norm_params({"trim_start": 500, "trim_end": 300})


@pytest.mark.parametrize("w,h", [(64, 16), (64, 64), (96, 24)])
def test_convert_at_display_size_and_loop(disp, w, h):
    disp(w, h)
    job = ST.convert(make_gif(20), {"fps": 12.5, "max_frames": 64, "fit_limits": False})
    assert all(f.size == (w, h) for f in job.frames)
    assert len(job.frames) == 20 and job.frame_ms == 80          # every frame, real timing
    assert loop_metric_frames(job.frames)["ok"]                  # the source loop stays seamless
    info = job.info(with_frames=False)
    assert info["source_info"]["frames"] == 20 and info["size"] == [w, h]


def test_strategies(disp):
    disp(64, 16)
    data = make_gif(6, size=(96, 96))
    assert ST.convert(data, {"strategy": "contain"}).params["applied_mode"] == "fit"
    assert ST.convert(data, {"strategy": "crop"}).params["applied_mode"] == "fill"
    assert ST.convert(data, {"strategy": "adaptive"}).params["applied_mode"] == "fit"     # 1:1 into 4:1
    disp(64, 64)
    assert ST.convert(data, {"strategy": "adaptive"}).params["applied_mode"] == "fill"    # same shape
    with pytest.raises(ValueError):
        ST.norm_params({"strategy": "zoom"})


def test_speed_and_trim_in_convert(disp):
    disp(64, 16)
    data = make_gif(20)                                     # 1.6 s
    fast = ST.convert(data, {"speed": 2.0, "fps": 25, "max_frames": 64, "fit_limits": False})
    assert sum([fast.frame_ms] * len(fast.frames)) == pytest.approx(800, abs=60)
    cut = ST.convert(data, {"trim_start": 400, "trim_end": 1200, "fps": 12.5, "max_frames": 64, "fit_limits": False})
    assert len(cut.frames) * cut.frame_ms == pytest.approx(800, abs=60)


def test_fit_to_legacy_limits_on_ble_sign(disp):
    disp(64, 16, driver="ipixel")
    big = make_gif(40, size=(64, 16), noisy=True)
    free = ST.convert(big, {"fps": 12.5, "max_frames": 64, "fit_limits": False})
    assert len(free.encoded()) > ST.BIG_GIF
    job = ST.convert(big, {"fps": 12.5, "max_frames": 64})
    assert len(job.encoded()) <= ST.BIG_GIF and job.notes
    assert len(job.frames) * job.frame_ms == pytest.approx(len(free.frames) * free.frame_ms, rel=0.1)
    disp(64, 16, driver="wled")                               # network panel: no BLE budget
    assert ST.legacy_limits()["max_bytes"] == 0


def test_save_to_library_and_size_set(disp, tmp_path, monkeypatch):
    monkeypatch.setattr(PR, "DIR", tmp_path / "p16")
    monkeypatch.setattr(PR, "DIR64", tmp_path / "p64")
    (tmp_path / "p16").mkdir()
    (tmp_path / "p64").mkdir()
    monkeypatch.setattr(paths, "library", lambda: tmp_path / "lib")
    disp(64, 64)
    data = make_gif(12)
    job = ST.convert(data, {"fps": 12.5, "fit_limits": False})
    meta = ST.save_preset(job, "orbit", "user", "Orbit")
    assert (tmp_path / "p64" / "orbit.gif").exists() and meta["size"] == [64, 64]
    assert (tmp_path / "lib" / "imports" / "orbit.gif").read_bytes() == data
    assert json.loads((tmp_path / "p64" / "orbit.json").read_text())["source_file"] == "imports/orbit.gif"
    names = [p["name"] for p in PR.list_presets((64, 64))]
    assert "orbit" in names


def test_saved_gif_goes_into_a_playlist(disp, tmp_path, monkeypatch):
    from orcpixels import playlist as PL
    monkeypatch.setattr(PR, "DIR", tmp_path)
    monkeypatch.setattr(paths, "library", lambda: tmp_path / "lib")
    disp(64, 16)
    job = ST.convert(make_gif(10), {"fit_limits": False})
    ST.save_preset(job, "orbit16", "user")
    monkeypatch.setattr(PL, "playlists_path", lambda: tmp_path / "playlists.json")
    pl = PL.save("gifs", [{"preset": "orbit16", "duration": 10}, "orbit16"])
    assert [i["preset"] for i in pl["items"]] == ["orbit16", "orbit16"]
    assert PR.get("orbit16")["kind"] == "gif"
