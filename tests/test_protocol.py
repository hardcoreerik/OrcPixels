"""Offline tests. If pypixelcolor is importable, compare bytes against it."""
import binascii
import io

import pytest
from PIL import Image

from orcpixels import protocol as P
from orcpixels.render import test_pattern as make_pattern


def test_simple_commands():
    assert P.set_brightness(50).frames[0].data == bytes([5, 0, 4, 0x80, 50])
    assert P.set_power(True).frames[0].data == bytes([5, 0, 7, 1, 1])
    assert P.set_power(False).frames[0].data == bytes([5, 0, 7, 1, 0])


def test_png_frame_layout():
    png = P.image_to_png(make_pattern())
    f = P.send_png(png).frames[0].data
    assert int.from_bytes(f[0:2], "little") == len(f)
    assert f[2:5] == b"\x02\x00\x00"
    assert int.from_bytes(f[5:9], "little") == len(png)
    assert f[9:13] == (binascii.crc32(png) & 0xFFFFFFFF).to_bytes(4, "little")
    assert f[15:] == png


def test_classify():
    assert P.classify_notify(bytes([5, 0, 1, 0, 1])) == "window"
    assert P.classify_notify(bytes([5, 0, 1, 0, 3])) == "done"


def test_text_matches_pypixelcolor():
    ref = pytest.importorskip("pypixelcolor.commands.send_text")
    for text, kw in [("HELLO", {}), ("Hi é 世 {}", {"color": "ff0000", "bg_color": "000033"}),
                     ("scroll", {"animation": 1, "speed": 50})]:
        ours = P.send_text(text, **kw)
        theirs = ref.send_text(text, char_height=16, **kw)
        assert [f.data for f in ours.frames] == [w.data for w in theirs.windows], text


def test_png_matches_pypixelcolor():
    ref = pytest.importorskip("pypixelcolor.commands.send_image")
    png = P.image_to_png(make_pattern())
    theirs = ref._build_send_plan(png, False)
    assert [f.data for f in P.send_png(png).frames] == [w.data for w in theirs.windows]


def test_branding_and_splash():
    from orcpixels import branding as B
    assert B.find_project("OrcSDR-radio-refactor").key == "OrcSDR"
    assert B.find_project("orcsdr").key == "OrcSDR"
    assert B.find_project("SomethingElse") is None
    assert B.find_app("Claude.exe").key == "claude"
    assert B.project_frame("OrcSDR").size == (64, 16)
    assert B.status_frame(B.find_app("codex.exe"), B.find_project("OrcSDR"), "main").size == (64, 16)
    gif, frames = B.splash_gif()
    assert gif[:6] == b"GIF89a" and all(f.size == (64, 16) for f in frames)
    f = P.send_gif(gif).frames[0].data
    assert f[2:4] == b"\x03\x00" and f[13] == 0x02


def test_presets_and_rotation(tmp_path, monkeypatch):
    import asyncio
    from orcpixels import presets as PR, settings as S
    from orcpixels.ble_sign import SignClient
    names = {m["name"] for m in PR.list_presets()}
    assert {"orcsdr_orc", "orcsdr_wave", "orcsdr_wave_small"} <= names
    assert PR.preview_image("orcsdr_wave_small").size == (64, 16)
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")
    assert S.get("rotate_180") is False          # default: not rotated
    S.save({"rotate_180": True})
    assert S.get("rotate_180") is True

    sent = []
    sign = SignClient("00:00:00:00:00:00", rotate_180=True)
    async def fake_run(cmd, retry=True, **kw):
        sent.append(cmd)
    sign.run = fake_run
    img = PR.preview_image("orcsdr_orc")
    asyncio.run(sign.send_image(img))
    assert sent[0].name == "orientation" and sent[0].frames[0].data[-1] == 0
    png = sent[1].frames[0].data[15:]
    assert Image.open(io.BytesIO(png)).convert("RGB").tobytes() == img.rotate(180).tobytes()
    assert sign.last_frame.tobytes() == img.tobytes()          # preview stays upright
    asyncio.run(sign.send_text("HI"))
    assert sent[2].name == "orientation" and sent[2].frames[0].data[-1] == 2


def test_orcsdr_default_is_anim():
    from orcpixels import branding as B, presets as PR
    assert B.default_preset("OrcSDR-radio-refactor") == "orcsdr_wave_small_anim"
    assert PR.get("orcsdr_wave_small_anim")["kind"] == "gif"
