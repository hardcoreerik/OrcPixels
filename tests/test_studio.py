"""Task B backend: activity ring buffer, now-showing, Image Converter/Creator (studio)."""
import asyncio
import io
import json

import pytest
from PIL import Image

from orcpixels import activity as A
from orcpixels import playlist as PL
from orcpixels import presets as PR
from orcpixels import studio as ST
from orcpixels.ble_sign import SignClient
from orcpixels.loopmetric import loop_metric_frames
from orcpixels.render import encode_gif


def _gif(frames, ms=100):
    return encode_gif(frames, ms)


def _ramp(n=12):
    """A dot moving left->right: the last->first step is a big jump (not seamless)."""
    out = []
    for i in range(n):
        im = Image.new("RGB", (64, 16))
        for x in range(i * 4, i * 4 + 4):
            for y in range(4, 12):
                im.putpixel((x, y), (255, 200, 0))
        out.append(im)
    return out


# ------------------------------------------------------------------ activity
def test_activity_labels_and_since():
    act = A.Activity()
    with A.labelled("preset foo"):
        e = act.start("send_gif", 1234)
    act.finish(e, "acked", b"\x05\x00\x03\x00\x03")
    assert e["what"] == "preset foo" and e["status"] == "acked" and e["reply"] == "0500030003"
    assert act.start("orientation")["kind"] == "control"
    assert act.start("brightness")["kind"] == "content"
    assert [x["id"] for x in act.since(0)] == [1, 2, 3]
    rev = act.rev
    assert act.since(rev) == []


def test_activity_folds_live_frames():
    act = A.Activity()
    for _ in range(5):
        act.live_frame(300, True, b"\x05\x00\x02\x00\x03")
    items = act.recent(10)
    assert len(items) == 1 and items[0]["count"] == 5 and items[0]["bytes"] == 1500
    act.live_frame(300, False, error="TimeoutError")
    act.live_frame(300, True)
    assert [i["status"] for i in act.recent(10)] == ["acked", "failed", "acked"]


def test_signclient_run_records_activity(monkeypatch):
    s = SignClient(address=None, rotate_180=False)

    async def ok(cmd, retry, progress, *a):
        return b"\x05\x00\x04\x80\x01"

    async def bad(cmd, retry, progress, *a):
        raise TimeoutError("no ack")

    from orcpixels import protocol as P
    monkeypatch.setattr(s, "_run_retry", ok)
    with A.labelled("brightness test"):
        asyncio.run(s.run(P.set_brightness(50)))
    monkeypatch.setattr(s, "_run_retry", bad)
    with pytest.raises(TimeoutError):
        asyncio.run(s.run(P.set_brightness(40)))
    a, b = s.activity.recent(5)
    assert a["what"] == "brightness test" and a["status"] == "acked"
    assert b["status"] == "failed" and "no ack" in b["error"]


def test_now_showing_set_by_sends(monkeypatch):
    s = SignClient(address=None, rotate_180=False)

    async def fake_run(cmd, retry=True, progress=None, **kw):
        return b"\x05\x00\x08\x80\x01"

    async def noop(v):
        return None

    monkeypatch.setattr(s, "run", fake_run)
    monkeypatch.setattr(s, "_ensure_orientation", noop)
    asyncio.run(s.send_gif(_gif(_ramp(3))))
    assert s.showing["kind"] == "animation" and s.showing["frames"] == 3
    asyncio.run(s.set_program_list([1, 2]))
    assert s.showing["kind"] == "playlist" and s.showing["slots"] == [1, 2]


# ------------------------------------------------------------------ converter steps
def test_fit_modes():
    wide = Image.new("RGB", (200, 20), (255, 0, 0))
    fit = ST.fit_frame(wide, "fit")
    assert fit.size == (64, 16)
    assert fit.getpixel((32, 0)) == (0, 0, 0) and fit.getpixel((32, 8)) == (255, 0, 0)   # letterboxed
    fill = ST.fit_frame(wide, "fill")
    assert fill.getpixel((0, 0)) == (255, 0, 0) and fill.getpixel((63, 15)) == (255, 0, 0)
    tiny = Image.new("RGB", (4, 4), (0, 255, 0))
    crop = ST.fit_frame(tiny, "crop", pos_x=0, pos_y=0)
    assert crop.getpixel((3, 3)) == (0, 255, 0) and crop.getpixel((4, 4)) == (0, 0, 0)
    assert ST.fit_frame(tiny, "stretch").getpixel((63, 15)) == (0, 255, 0)


def test_reduce_colors_and_dither():
    im = Image.new("RGB", (64, 16))
    for x in range(64):
        for y in range(16):
            im.putpixel((x, y), (x * 4, y * 16, 128))
    out = ST.reduce_colors([im], colors=4, dither="none")[0]
    assert len(set(out.getdata())) <= 4
    out2 = ST.reduce_colors([im], colors=4, dither="fs")[0]
    assert len(set(out2.getdata())) <= 4 and out2.tobytes() != out.tobytes()
    assert ST.reduce_colors([im])[0] is im


def test_resample_keeps_speed_and_caps():
    frames = _ramp(12)
    out, ms = ST.resample(frames, [100] * 12, fps=5, max_frames=32)
    assert len(out) == 6 and ms == 200
    out, ms = ST.resample(frames, [100] * 12, fps=30, max_frames=4)
    assert len(out) == 4 and ms == 300
    out, ms = ST.resample(frames, [100] * 12, fps=30, max_frames=64)
    assert len(out) == 12 and ms == 100      # never invents frames


def _fade(n=12):
    """Fade up, then snap back to black: a visible loop jump."""
    return [Image.new("RGB", (64, 16), (i * 20, 0, 0)) for i in range(n)]


def test_loop_fixes_make_seamless():
    grad = _fade(12)
    assert not loop_metric_frames(grad)["ok"]
    pp = ST.make_loop(grad, "pingpong")
    assert len(pp) == 22 and loop_metric_frames(pp)["ok"]
    xf = ST.make_loop(grad, "crossfade", 4)
    assert len(xf) == 8 and loop_metric_frames(xf)["ok"]


def test_convert_gif_and_png():
    job = ST.convert(_gif(_ramp(8), 120), {"loop": "pingpong", "colors": 16})
    info = job.info()
    assert info["kind"] == "gif" and info["frames"] == 14 and info["loop"]["ok"]
    assert info["frame_urls"][0].startswith("data:image/png;base64,")
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (0, 0, 255)).save(buf, "JPEG")
    still = ST.convert(buf.getvalue(), {"mode": "fill"})
    assert still.info(False)["kind"] == "png" and not still.animated
    with pytest.raises(ValueError):
        ST.convert(buf.getvalue(), {"mode": "zoomy"})


def test_from_frames_roundtrip():
    urls = [ST.data_url(f) for f in _ramp(4)]
    job = ST.from_frames(urls, 150)
    assert job.animated and job.frame_ms == 150 and job.frames[1].getpixel((5, 5)) == (255, 200, 0)
    with pytest.raises(ValueError):
        ST.from_frames([], 100)


def test_render_text_fonts():
    for font in ST.FONTS:
        layer, w = ST.render_text("ORC 42", font, "#ff0000", 1, 1)
        assert layer.size == (64, 16) and w > 0
        px = [p for p in layer.getdata() if p[3]]
        assert px and all(p[:3] == (255, 0, 0) for p in px)          # hard pixels, no AA
    with pytest.raises(ValueError):
        ST.render_text("x", "comic-sans")


def test_import_frames_and_asset_guard():
    r = ST.import_frames(preset="p_rf_adsb")
    assert len(r["frames"]) == 16 and r["frame_ms"] == 120
    assets = ST.list_assets()
    assert assets and all("_preview" not in a["path"] for a in assets)
    r2 = ST.import_frames(asset=assets[0]["path"])
    assert len(r2["frames"]) >= 1
    with pytest.raises(KeyError):
        ST.import_frames(asset="../config/settings.json")


# ------------------------------------------------------------------ save / store
@pytest.fixture
def tmp_presets(tmp_path, monkeypatch):
    monkeypatch.setattr(PR, "DIR", tmp_path)
    return tmp_path


def test_save_preset_rules(tmp_presets):
    job = ST.Job(frames=ST.make_loop(_ramp(6), "pingpong"), frame_ms=100, source="creator")
    meta = ST.save_preset(job, "my_anim", "user", "My anim")
    assert (tmp_presets / "my_anim.gif").exists() and (tmp_presets / "my_anim_preview_10x.png").exists()
    saved = json.loads((tmp_presets / "my_anim.json").read_text())
    assert saved["kind"] == "gif" and saved["loop"] == "seamless" and saved["family"] == "user"
    assert PR.get("my_anim")["frames"] == meta["frames"] == 10
    with pytest.raises(FileExistsError):
        ST.save_preset(job, "my_anim")
    for bad in ("p_rf_mine", "Bad Name", "thing_v2"):
        with pytest.raises(ValueError):
            ST.save_preset(job, bad)
    jump = ST.Job(frames=_fade(8), frame_ms=100)
    with pytest.raises(RuntimeError):
        ST.save_preset(jump, "jumpy")
    assert ST.save_preset(jump, "jumpy", force=True)["loop"].startswith("JUMP")
    still = ST.Job(frames=[_ramp(1)[0]])
    assert ST.save_preset(still, "still_one")["kind"] == "image"


class FakeSign(SignClient):
    def __init__(self):
        super().__init__(address=None, rotate_180=True)
        self.stored, self.programs = [], []

    async def _ensure_orientation(self, value):
        pass

    async def store_gif(self, gif, slot, progress=None):
        self.stored.append(("gif", slot, gif))
        return {"raw": "0500030003", "cmd": 3, "status": 3}

    async def store_png(self, png, slot, progress=None):
        self.stored.append(("png", slot, png))
        return {"raw": "0500020003", "cmd": 2, "status": 3}

    async def set_program_list(self, slots):
        self.programs.append(list(slots))
        return {"raw": "0500088001"}


@pytest.fixture
def tmp_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    return tmp_path


def test_store_slot_records_and_replays(tmp_slots):
    sign = FakeSign()
    job = ST.Job(frames=ST.make_loop(_ramp(4), "pingpong"), frame_ms=100, source="converter")
    r = asyncio.run(ST.store_slot(sign, job, 42, preset="my_anim"))
    kind, slot, data = sign.stored[0]
    assert kind == "gif" and slot == 42 and r["replayed"] is None and not sign.programs
    # rotate_180: device bytes are the rotated GIF, not the upright one
    assert data != job.encoded()
    rec = PL.slot_record()["slots"]["42"]
    assert rec["preset"] == "my_anim" and rec["source"] == "converter" and rec["bytes"] == len(data)
    # a slot of the running playlist: store, then re-send the play order
    rr = PL.slot_record()
    rr["active"] = {"playlist": "test3", "slots": [1, 2, 3]}
    PL._save_record(rr)
    still = ST.Job(frames=[_ramp(1)[0]], source="creator")
    r2 = asyncio.run(ST.store_slot(sign, still, 2))
    assert sign.stored[-1][0] == "png" and r2["replayed"] == "test3" and sign.programs == [[1, 2, 3]]
    assert PL.slot_record()["slots"]["2"]["preset"].startswith("studio:")
    with pytest.raises(ValueError):
        asyncio.run(ST.store_slot(sign, still, 101))


# ------------------------------------------------------------------ routes
def _route(app, path, method):
    for r in app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return r.endpoint
    raise LookupError(path)


def test_routes_dash_and_studio(monkeypatch, tmp_presets, tmp_slots):
    from orcpixels.web import create_app
    app = create_app(address=None)
    sign = app.state.sign
    sent = []

    async def fake_send_gif(gif, first=None):
        sent.append(len(gif))
        sign.set_showing("animation", "x")

    monkeypatch.setattr(sign, "send_gif", fake_send_gif)
    d = asyncio.run(_route(app, "/api/dash", "GET")(since=0))
    for k in ("link", "now", "activity", "rev", "files_rev", "auto", "pid"):
        assert k in d
    urls = [ST.data_url(f) for f in ST.make_loop(_fade(5), "pingpong")]
    FramesReq, TextReq = ST.FramesReq, ST.TextReq
    info = asyncio.run(_route(app, "/api/studio/frames", "POST")(FramesReq(frames=urls, frame_ms=90)))
    assert info["frames"] == 8 and info["loop"]["ok"]
    r = asyncio.run(_route(app, "/api/studio/jobs/{jid}/send", "POST")(info["id"]))
    assert r["ok"] and sent == [info["bytes"]]
    now = asyncio.run(_route(app, "/api/now", "GET")())
    assert now["kind"] == "converter" or now["kind"] == "creator"
    t = asyncio.run(_route(app, "/api/studio/text", "POST")(TextReq(text="HI", font="pix35")))
    assert t["png"].startswith("data:image/png") and t["width"] == 7
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(_route(app, "/api/studio/jobs/{jid}/send", "POST")("nope"))
    assert ei.value.status_code == 404


def test_led_thumbnails():
    data, mt = ST.led_thumb(PR.file_path(PR.get("p_rf_adsb")), 4)
    im = Image.open(io.BytesIO(data))
    assert mt == "image/gif" and im.size == (256, 64) and im.n_frames == 16
    red = Image.new("RGB", (64, 16), (255, 0, 0))
    led = ST.led_frame(red, 4)
    assert led.size == (256, 64)
    assert led.getpixel((1, 1))[0] > 200          # dot centre lit
    assert led.getpixel((0, 0)) == (0, 0, 0)      # gap between dots stays black
    dark = ST.led_frame(Image.new("RGB", (64, 16)), 4)
    assert dark.getpixel((1, 1)) == (22, 24, 28)  # unlit LED faintly visible
