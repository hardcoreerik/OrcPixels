"""HTTP-level route tests: drive the FastAPI app through raw ASGI with the same request
shapes the web UI sends (no httpx in the venv, so no TestClient)."""
import asyncio
import json

import pytest

from orcpixels import playlist as PL
from orcpixels import presets as PR


def asgi(app, method, path, body=None, query=""):
    raw = json.dumps(body).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(raw)).encode())]}
    sent = {"body": b"", "status": None}
    msgs = [{"type": "http.request", "body": raw, "more_body": False}]

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            sent["status"] = m["status"]
        elif m["type"] == "http.response.body":
            sent["body"] += m.get("body", b"")

    asyncio.run(app(scope, receive, send))
    try:
        data = json.loads(sent["body"] or b"null")
    except ValueError:
        data = sent["body"]
    return sent["status"], data


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    from orcpixels.web import create_app
    return create_app(address=None)


def test_playlist_save_list_delete_like_the_ui(app):
    # exactly what plSave() posts: {items:[{preset, duration}]}
    st, r = asgi(app, "POST", "/api/playlists/Default",
                 {"items": [{"preset": "orcsdr_wave_small_anim", "duration": None}]})
    assert st == 200, r
    assert PL.get("Default")["items"] == [{"preset": "orcsdr_wave_small_anim", "duration": None}]
    st, r = asgi(app, "GET", "/api/playlists")
    assert st == 200 and "Default" in r["playlists"]
    assert r["playlists"]["Default"]["slots"] == [1]
    st, r = asgi(app, "POST", "/api/playlists/Default", {"items": [{"preset": "no_such_preset"}]})
    assert st == 400
    st, _ = asgi(app, "DELETE", "/api/playlists/Default")
    assert st == 200 and "Default" not in PL.load_all()


def test_every_post_route_resolves_its_body_model():
    """Guard against the forward-ref trap: no route may expose a pydantic model (or an
    unresolved ForwardRef) as a query parameter."""
    from orcpixels.web import create_app
    app = create_app(address=None)
    bad = []
    for r in app.routes:
        dep = getattr(r, "dependant", None)
        if dep is None:
            continue
        for q in dep.query_params:
            ann = q.field_info.annotation
            if "ForwardRef" in str(ann) or (isinstance(ann, type) and hasattr(ann, "model_fields")):
                bad.append((r.path, q.name, str(ann)))
    assert not bad, bad


def test_settings_and_studio_text_over_http(app):
    st, r = asgi(app, "POST", "/api/studio/text", {"text": "HI", "font": "tiny"})
    assert st == 200 and r["png"].startswith("data:image/png")
    st, r = asgi(app, "POST", "/api/studio/frames", {"frames": []})
    assert st == 400
    assert PR.get("orcsdr_wave_small_anim")


# ------------------------------------------------------------------ OrcSDR Live (UI request shapes)
def test_live_start_stop_like_the_ui(app, monkeypatch):
    live = app.state.orcsdr_live
    calls = []

    async def fake_start(waterfall=None, interval=None):
        calls.append(("start", waterfall, interval))

    async def fake_stop(restore=False):
        calls.append(("stop", restore))
        return "playlist test3"

    monkeypatch.setattr(live, "start", fake_start)
    monkeypatch.setattr(live, "stop", fake_stop)
    # exactly what sdrToggle() posts (checkbox -> bool, number input -> float)
    st, r = asgi(app, "POST", "/api/orcsdr/live", {"action": "start", "waterfall": True, "interval": 0.9})
    assert st == 200, r
    st, r = asgi(app, "POST", "/api/orcsdr/live", {"action": "stop", "waterfall": True, "interval": 0.9})
    assert st == 200, r
    assert calls == [("start", True, 0.9), ("stop", True)]
    assert r["live"]["stopped_reason"].startswith("stopped by user")
    st, _ = asgi(app, "POST", "/api/orcsdr/live", {"action": "pause"})
    assert st == 400
    # the config calls the Live/Settings tabs make
    for body in ({"waterfall": False}, {"interval": 1.5}, {"transport": "fast"}):
        monkeypatch.setattr("orcpixels.settings.save", lambda upd: upd, raising=False)
        st, r = asgi(app, "POST", "/api/orcsdr/config", body)
        assert st == 200, (body, r)


def test_dash_reports_live_and_queue(app):
    st, d = asgi(app, "GET", "/api/dash", query="since=0")
    assert st == 200
    assert d["live"]["enabled"] is False and "stopped_reason" in d["live"]
    assert d["queue"] == {"queued": 0, "sending": None, "queued_what": [], "est_s": 0}


def test_live_start_drops_queued_display_sends():
    """Rapid preset clicks queue sends behind the BLE lock; starting Live must not wait for
    (and then be replaced by) all of them."""
    from orcpixels import protocol as P
    from orcpixels.ble_sign import SignClient, Superseded

    async def scenario():
        s = SignClient(address=None, rotate_180=False)
        order = []

        async def fake_locked(cmd, progress=None):
            order.append(cmd.name)
            await asyncio.sleep(0.05)
            return b"\x05\x00\x03\x00\x03"

        s._run_locked = fake_locked
        gif = P.send_gif(b"GIF89a" + b"\x00" * 64)
        first = asyncio.create_task(s.run(gif, supersedable=True))
        await asyncio.sleep(0.01)                     # first one is on the air
        queued = [asyncio.create_task(s.run(gif, supersedable=True)) for _ in range(3)]
        ctl = asyncio.create_task(s.run(P.set_brightness(10)))   # not a display send
        await asyncio.sleep(0.01)
        assert s.queue_info()["queued"] == 4
        assert s.supersede_pending("OrcSDR Live was started") == 3
        await first
        res = await asyncio.gather(*queued, ctl, return_exceptions=True)
        assert all(isinstance(r, Superseded) for r in res[:3]) and not isinstance(res[3], Exception)
        assert order.count(gif.name) == 1
        st = [e["status"] for e in s.activity.recent(10)]
        assert st.count("skipped") == 3 and st.count("acked") == 2
        assert s.queue_info()["queued"] == 0

    asyncio.run(scenario())


def test_takeover_reason_names_what_was_sent():
    from orcpixels.orcsdr_live import OrcSDRLive
    from orcpixels.ble_sign import SignClient
    s = SignClient(address=None, rotate_180=False)
    live = OrcSDRLive(s)
    s.set_showing("preset", "p_orc_eyes")
    assert live._takeover_reason() == "preset p_orc_eyes sent"
    s.showing = None
    assert "another display command" in live._takeover_reason()


def test_duplicate_preset_click_is_rejected(app):
    sign = app.state.sign
    e = sign.activity.start("send_gif", 10000, what="preset p_orc_eyes")
    e["status"] = "queued"
    st, r = asgi(app, "POST", "/api/presets/p_orc_eyes/show")
    assert st == 409 and "already being sent" in r["detail"]
