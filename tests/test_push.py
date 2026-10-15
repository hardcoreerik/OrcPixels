"""Push API (orcpixels/push.py, docs/PUSH_API.md): board rules, rendering at the native
and adaptive sizes, the live engine (legacy frames, coalescing, restore, user takeover)
and the HTTP routes incl. /api/health and the localhost / LAN-token rule."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

from orcpixels import push as PU

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ board
def test_board_coalesces_and_expires_by_ttl():
    c = Clock()
    b = PU.Board(clock=c)
    for p in range(0, 101, 5):                       # 21 rapid updates -> one entry, newest wins
        b.set_status("rsa", "RSA", "scan", p, ttl_s=10)
    kind, s = b.current()
    assert kind == "status" and s.pct == 100 and len(b.statuses) == 1
    c.t += 9.9
    assert b.current() is not None
    c.t += 0.2
    assert b.current() is None and not b.statuses


def test_board_clamps_and_cleans_inputs():
    b = PU.Board(clock=Clock())
    s = b.set_status("  ", "", "x" * 500, 140, "d\u00e9tail", ttl_s=99999)
    assert s.source == "default" and s.title == "default"
    assert s.pct == 100 and s.ttl_s == PU.MAX_TTL_S and len(s.step) == PU.MAX_LEN["step"]
    assert s.detail == "detail"
    assert b.set_status("a", pct=-3).pct == 0
    assert b.set_status("a", pct="nope").pct is None
    assert b.set_status("a", ttl_s=0.1).ttl_s == PU.MIN_TTL_S
    f = b.set_flash("a", seconds=999)
    assert f.headline == "DONE" and f.until - b.clock() == PU.MAX_FLASH_S


def test_flash_ends_the_run_and_wins_then_expires():
    c = Clock()
    b = PU.Board(clock=c)
    b.set_status("rsa", "RSA", "count", 90)
    b.set_status("other", "CI", "build", 10)
    b.set_flash("rsa", "RSA", "96k LOC", seconds=4)
    assert "rsa" not in b.statuses                   # no clear needed after a flash
    kind, f = b.current()
    assert kind == "flash" and f.headline == "96k LOC"
    c.t += 4.0
    kind, s = b.current()
    assert kind == "status" and s.source == "other"  # back to the other source's status
    b.set_status("rsa", step="again")                # a new run replaces an old flash
    assert "rsa" not in b.flashes


def test_newest_source_wins_and_clear():
    c = Clock()
    b = PU.Board(clock=c)
    b.set_status("a", step="1")
    c.t += 1
    b.set_status("b", step="2")
    assert b.current()[1].source == "b"
    assert b.clear("b") == 1 and b.current()[1].source == "a"
    assert b.clear() == 1 and b.current() is None


def test_mute_lasts_until_the_session_ends():
    c = Clock()
    b = PU.Board(clock=c)
    b.set_status("rsa", step="x", ttl_s=5)
    b.mute_active()
    assert b.current() is None
    b.set_status("rsa", step="y", ttl_s=5)           # same session: still muted
    assert b.current() is None and "rsa" in b.muted
    b.set_flash("rsa", headline="done", seconds=3)   # muted flash stays hidden too
    assert b.current() is None
    c.t += 3.1                                       # session over -> unmuted
    assert b.current() is None and not b.muted
    b.set_status("rsa", step="next run")
    assert b.current()[1].step == "next run"
    b.mute_active()
    b.clear("rsa")
    b.set_status("rsa", step="after clear")
    assert b.current() is not None


# ------------------------------------------------------------------ rendering
SIZES = [(64, 16), (64, 64), (16, 16), (64, 32), (256, 16), (96, 24)]


def _cur():
    import push_previews as PP
    return PP.samples()


@pytest.mark.parametrize("size", SIZES)
def test_renders_every_size_without_overflow(size):
    for label, cur in _cur():
        for k in range(8):
            img = PU.render(cur, size, k=k, now=1079.0)
            assert img.size == size and img.mode == "RGB"
            assert img.getbbox() is not None, (label, size)


def test_native_golden_references():
    """64x16 / 64x64 are native: their pixels are pinned (tools/push_previews.py --golden)."""
    import push_previews as PP
    ref = json.loads((ROOT / "tests/golden/push.json").read_text(encoding="utf-8"))
    assert PP.golden() == ref


def test_static_status_does_not_change_between_frames():
    """Known pct + one page of text: identical frames -> the engine sends nothing new."""
    b = PU.Board(clock=Clock())
    s = b.set_status("rsa", "RSA", "count", 42)
    frames = {PU.render(("status", s), (64, 16), k=k, now=1000).tobytes() for k in range(10)}
    assert len(frames) == 1


def test_indeterminate_bar_loops_seamlessly():
    b = PU.Board(clock=Clock())
    s = b.set_status("rsa", "RSA", "clone", None)
    iw = 62
    seg, step = max(3, iw // 5), max(1, iw // 16)
    n = -(-(iw + seg) // step)
    f = [PU.render(("status", s), (64, 16), k=k, now=1000).crop((0, 12, 64, 16)).tobytes() for k in range(2 * n)]
    assert f[0] == f[n] and len(set(f[:n])) > 3


def test_long_step_pages_on_the_strip():
    b = PU.Board(clock=Clock())
    s = b.set_status("rsa", "RSA", "Counting lines of code in every language", 50)
    a = PU.render(("status", s), (64, 16), k=0, now=1000)
    z = PU.render(("status", s), (64, 16), k=PU.PAGE_FRAMES, now=1000)
    assert a.tobytes() != z.tobytes()


# ------------------------------------------------------------------ engine
class FakeState:
    def __init__(self, w, h):
        self.width, self.height = w, h


class FakeSign:
    rotate_180 = False

    def __init__(self, w=64, h=16):
        self.state = FakeState(w, h)
        self.connected = True
        self.last_frame = None
        self.last_gif = None
        self.display_seq = 0
        self.showing = None
        self.resume_hint = None
        self.sent = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def send_image(self, img):
        await asyncio.sleep(0.002)
        self.sent.append(img.copy())
        self.last_frame = img
        self.last_gif = None
        self.resume_hint = None
        self.display_seq += 1
        self.set_showing("image", "img")

    async def send_gif(self, data, first=None):
        self.sent.append(("gif", data))
        self.last_gif, self.last_frame = data, first
        self.display_seq += 1

    def set_showing(self, kind, name=None, **extra):
        self.showing = {"kind": kind, "name": name, **extra}

    async def user_preset(self, name):           # what presets.play does
        await self.send_image(Image.new("RGB", (64, 16), (9, 9, 9)))
        self.resume_hint = {"kind": "preset", "name": name}
        self.set_showing("preset", name)


class FakePeer:
    NAME = "Live Data"

    def __init__(self):
        self.enabled = False
        self.starts = self.stops = 0
        self.stopped_reason = None

    async def start(self, *a, **k):
        self.enabled = True
        self.starts += 1

    async def stop(self, *a, **k):
        self.enabled = False
        self.stops += 1


def make(sign=None):
    live = PU.PushLive(sign or FakeSign())
    live.interval = 0.05
    live.poll_s = 0.01
    live.send_timeout_s, live.connect_timeout_s = 1.0, 1.0
    live.restore_settle_s = 0.0
    return live


async def until(pred, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


def test_engine_streams_legacy_coalesces_and_restores_the_preset(monkeypatch):
    from orcpixels import presets as PR
    played = []

    async def fake_play(sign, name):
        played.append(name)
        await sign.user_preset(name)

    monkeypatch.setattr(PR, "play", fake_play)

    async def run():
        sign = FakeSign()
        await sign.user_preset("orco_wave")
        live = make(sign)
        assert live.transport == "legacy"
        live.board.set_status("rsa", "RSA", "count", 1, ttl_s=30)
        await live.start()
        await until(lambda: len(sign.sent) >= 2)
        n0 = len(sign.sent)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for p in range(2, 60):                       # 58 updates, faster than the frame interval
            live.board.set_status("rsa", "RSA", "count", p, ttl_s=30)
            await asyncio.sleep(0)
        await asyncio.sleep(0.12)
        elapsed = loop.time() - t0
        assert len(sign.sent) - n0 <= elapsed / live.interval + 2   # coalesced to the interval
        assert live.board.current()[1].pct == 59                    # newest value wins
        assert sign.showing["kind"] == "live" and sign.showing["name"] == "Push: RSA"
        n1 = len(sign.sent)
        live.board.set_flash("rsa", "RSA", "96k LOC", seconds=2)
        await until(lambda: len(sign.sent) > n1, 2)
        assert sign.sent[-1].getbbox() is not None
        live.board.clear("rsa")
        await until(lambda: played == ["orco_wave"])
        await until(lambda: not live.enabled)
        assert live.restored == "preset orco_wave" and sign.resume_hint["name"] == "orco_wave"
    asyncio.run(run())


def test_engine_restarts_the_live_source_it_replaced():
    async def run():
        live = make()
        peer = FakePeer()
        peer.enabled = True
        live.peers = [peer]
        live.board.set_status("rsa", step="x", ttl_s=2)
        await live.start()
        assert peer.stops == 1 and not peer.enabled
        await until(lambda: live.sign.sent)
        live.board.clear()
        await until(lambda: peer.starts == 1)
        assert live.restored == "Live Data" and not live.enabled
    asyncio.run(run())


def test_engine_restores_last_frame_when_nothing_else_is_known():
    async def run():
        sign = FakeSign()
        prev = Image.new("RGB", (64, 16), (1, 2, 3))
        await sign.send_image(prev)
        sign.set_showing("text", "HELLO")
        live = make(sign)
        live.board.set_status("rsa", step="x")
        await live.start()
        await until(lambda: len(sign.sent) >= 2)
        live.board.clear("rsa")
        await until(lambda: live.restored is not None)
        assert sign.sent[-1].tobytes() == prev.tobytes() and sign.showing["kind"] == "text"
    asyncio.run(run())


def test_user_takeover_mutes_the_source():
    async def run():
        sign = FakeSign()
        live = make(sign)
        live.board.set_status("rsa", step="x", ttl_s=30)
        await live.start()
        await until(lambda: sign.sent)
        await sign.user_preset("mine")               # the user picks something
        await until(lambda: not live.enabled)
        assert "rsa" in live.board.muted and live.restored is None
        live.board.set_status("rsa", step="y", ttl_s=30)
        live.arm()
        await asyncio.sleep(0.1)
        assert not live.enabled and sign.showing["name"] == "mine"   # never fights the user
        live.board.clear("rsa")                      # session over: the next run shows again
        live.board.set_status("rsa", step="next")
        live.arm()
        await until(lambda: live.enabled)
        await live.stop()
    asyncio.run(run())


def test_peer_start_stops_push_and_mutes():
    async def run():
        live = make()
        live.board.set_status("rsa", step="x", ttl_s=30)
        await live.start()
        await until(lambda: live.sign.sent)
        await live.stop()                             # what OrcSDRLive.start does to its peers
        assert not live.enabled and "rsa" in live.board.muted
    asyncio.run(run())


def test_push_that_arrives_during_expiry_keeps_streaming():
    async def run():
        live = make()
        live.board.set_status("rsa", step="x", ttl_s=2)
        await live.start()
        await until(lambda: live.sign.sent)
        live.board.clear()
        live.board.set_status("rsa", step="y")         # before the engine noticed the clear
        await asyncio.sleep(0.2)
        assert live.enabled
        await live.stop(mute=False)
    asyncio.run(run())


# ------------------------------------------------------------------ HTTP
def asgi(app, method, path, body=None, client="127.0.0.1"):
    """Raw ASGI call (as tests/test_routes_http.py) from a chosen client address."""
    raw = json.dumps(body).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": b"", "server": ("test", 80), "client": (client, 1),
             "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]}
    sent = {"body": b"", "status": None}
    msgs = [{"type": "http.request", "body": raw, "more_body": False}]

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            sent["status"] = m["status"]
        elif m["type"] == "http.response.body":
            sent["body"] += m.get("body", b"")

    async def go():
        await app(scope, receive, send)
        live = getattr(getattr(app, "state", None), "push_live", None)
        if live is not None and live.enabled:
            await live.stop(mute=False)

    asyncio.run(go())
    try:
        return sent["status"], json.loads(sent["body"] or b"null")
    except ValueError:
        return sent["status"], sent["body"]


@pytest.fixture
def app(tmp_path, monkeypatch):
    from orcpixels import playlist as PL
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    from orcpixels.web import create_app
    return create_app(address=None)


def test_routes_status_flash_clear_and_health(app):
    st, r = asgi(app, "GET", "/api/health")                # no sign connected -> 503 + reason
    assert st == 503 and r["ok"] is False and r["usable"] is False and "not connected" in r["reason"]
    assert r["app"] == "OrcPixels" and r["push"]["api"] == 1 and r["displays"][0]["connected"] is False
    st, r = asgi(app, "POST", "/api/push/status",
                 {"source": "rsa", "title": "RSA", "step": "Counting lines", "pct": 42.5, "ttl_s": 30})
    assert st == 200 and r["ok"] and r["shown"] is False and r["reason"] == "sign not connected"
    st, r = asgi(app, "GET", "/api/push")
    b = r["live"]["board"]
    assert b["current"] == {"kind": "status", "source": "rsa"} and b["statuses"][0]["pct"] == 42.5
    st, r = asgi(app, "POST", "/api/push/status", {"source": "rsa", "step": "x", "pct": None})
    assert st == 200
    st, r = asgi(app, "POST", "/api/push/flash", {"source": "rsa", "title": "RSA", "headline": "96k LOC"})
    assert st == 200
    st, r = asgi(app, "GET", "/api/push")
    assert r["live"]["board"]["current"]["kind"] == "flash"
    st, r = asgi(app, "POST", "/api/push/clear", {"source": "rsa"})
    assert st == 200 and r["cleared"] == 1
    st, r = asgi(app, "POST", "/api/push/clear", {})
    assert st == 200 and r["cleared"] == 0
    st, _ = asgi(app, "POST", "/api/push/status", {"source": "rsa", "pct": "lots"})
    assert st == 422


def test_lan_clients_need_a_token(app, monkeypatch):
    from orcpixels import settings as S
    st, _ = asgi(app, "POST", "/api/push/status", {"source": "rsa"}, client="192.168.1.50")
    assert st == 403
    st, _ = asgi(app, "GET", "/api/health", client="192.168.1.50")
    assert st == 503                                  # health stays readable (no data in it)
    orig = S.load
    monkeypatch.setattr(S, "load", lambda: {**orig(), "lan_token": "s3cret"})
    st, _ = asgi(app, "POST", "/api/push/status", {"source": "rsa"}, client="192.168.1.50")
    assert st == 200                                  # LanGuard has already checked the token


def test_languard_rejects_wrong_token(monkeypatch):
    from orcpixels import settings as S
    from orcpixels.web import LanGuard, create_app
    orig = S.load
    monkeypatch.setattr(S, "load", lambda: {**orig(), "lan_token": "s3cret"})
    st, _ = asgi(LanGuard(create_app(address=None)), "POST", "/api/push/status", {"source": "x"},
                 client="192.168.1.50")
    assert st == 401


def test_multi_display_health_and_push_routing(tmp_path, monkeypatch):
    from orcpixels import displays as D
    from orcpixels import web
    regs = [{"id": "strip", "label": "Strip", "address": "AA:BB:CC:DD:EE:01", "width": 64, "height": 16,
             "autoconnect": False},
            {"id": "matrix", "label": "Matrix", "address": "AA:BB:CC:DD:EE:02", "width": 64, "height": 64,
             "autoconnect": False}]
    monkeypatch.setattr(D, "load", lambda: [dict(r) for r in regs])
    m = web.create_multi_app()
    st, r = asgi(m, "GET", "/api/health")
    assert st == 503 and [d["id"] for d in r["displays"]] == ["strip", "matrix"]
    assert r["displays"][0]["primary"] and r["displays"][1]["height"] == 64 and r["display"] == "strip"
    from orcpixels import web as W                    # ?display=<id> picks another target
    r = W.health([{"id": "strip", "primary": True, "connected": False},
                  {"id": "matrix", "connected": True}], "matrix")
    assert r.status_code == 200 and json.loads(r.body)["display"] == "matrix"
    st, r = asgi(m, "POST", "/d/matrix/api/push/status", {"source": "rsa", "step": "x", "pct": 5})
    assert st == 200 and r["display"] == "matrix"
    assert m.apps["matrix"].state.push_live.board.current() is not None
    assert m.apps["strip"].state.push_live.board.current() is None


@pytest.fixture
def up_app(app, monkeypatch):
    """create_app() whose sign reports connected (no BLE): arming is stubbed out."""
    from orcpixels.ble_sign import SignClient
    monkeypatch.setattr(SignClient, "connected", property(lambda s: True))
    armed = []
    monkeypatch.setattr(app.state.push_live, "arm", lambda: armed.append(1))
    app.state.armed = armed
    return app


def test_health_is_200_when_the_sign_is_usable(up_app):
    st, r = asgi(up_app, "GET", "/api/health")
    assert st == 200 and r["ok"] and r["usable"] and r["reason"] is None


def test_rsa_alias_routes(up_app):
    app, board = up_app, up_app.state.push_live.board
    st, r = asgi(app, "POST", "/api/rsa/status", {"text": "Counting lines of code here!", "pct": 57, "line2": "3/7"})
    assert st == 200 and r["ok"] and r["shown"] and app.state.armed
    kind, s = board.current()
    assert kind == "status" and s.source == "rsa" and s.title == "RSA 3/7"
    assert s.step == "Counting lines of code h"[:24] and len(s.step) <= 24 and s.pct == 57
    assert s.ttl_s == 60                                  # a crashed run expires in 60 s
    st, r = asgi(app, "POST", "/api/rsa/flash", {"text": "DONE", "number": "7.4k", "label": "LOC",
                                                 "ok": True, "seconds": 5})
    kind, f = board.current()
    assert st == 200 and kind == "flash" and f.ok and f.headline == "7.4k" and f.sub == "LOC" and f.tag == "DONE"
    assert 4.9 <= f.until - f.posted <= 5.0 and "rsa" not in board.statuses
    st, r = asgi(app, "POST", "/api/rsa/flash", {"text": "FAIL", "label": "git clone"})
    kind, f = board.current()
    assert f.ok is False and f.headline == "FAIL" and f.tag == "FAIL"
    red = PU.render(("flash", f), (64, 16), k=1)
    assert any(c[0] > 200 and c[1] < 160 for _, c in red.getcolors(4096))   # red, not green
    st, r = asgi(app, "POST", "/api/rsa/flash", {"source": "rsa", "run_id": "r1", "text": "DONE",
                                                 "number": 96412, "label": "LOC", "ok": True, "seconds": 5})
    assert st == 200 and board.current()[1].headline == "96K"     # bare numbers / extra keys are fine
    st, r = asgi(app, "POST", "/api/rsa/flash", {"text": "DONE", "number": 7.0})
    assert st == 200 and board.current()[1].headline == "7"
    st, r = asgi(app, "POST", "/api/rsa/clear")
    assert st == 200 and r["cleared"] == 1 and board.current() is None
    st, r = asgi(app, "POST", "/api/rsa/status", {"text": "x", "pct": 150})
    assert st == 200 and board.current()[1].pct == 100
    st, r = asgi(app, "POST", "/api/rsa/status", {})       # missing fields are fine
    assert st == 200


def test_rsa_status_expires_after_60s_without_updates():
    c = Clock()
    b = PU.Board(clock=c)
    b.set_status(PU.RSA_SOURCE, "RSA 1/7", "clone", 10, "", PU.RSA_TTL_S)
    c.t += 59
    assert b.current() is not None
    c.t += 2
    assert b.current() is None
