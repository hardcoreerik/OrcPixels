"""LED matrix display library: every driver against a protocol-level simulator (real UDP
sockets / HTTP server on localhost, bytes checked against the specs). No hardware."""
import asyncio
import base64
import io
import json
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from orcpixels import drivers as DR
from orcpixels import fit as FIT
from orcpixels import profiles as PF
from orcpixels.drivers import packets as PK


def run(c):
    return asyncio.run(c)


def grad(w, h):
    img = Image.new("RGB", (w, h))
    img.putdata([(x * 4 % 256, y * 4 % 256, (x + y) % 256) for y in range(h) for x in range(w)])
    return img


class Udp:
    def __init__(self):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("127.0.0.1", 0))
        self.s.settimeout(2)
        self.port = self.s.getsockname()[1]

    def recv(self, n):
        return [self.s.recvfrom(65535)[0] for _ in range(n)]


class Http:
    """Records JSON requests; replies with `reply(path, body)`."""

    def __init__(self, reply):
        self.reqs = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                outer.reqs.append((self.command, self.path, body, dict(self.headers)))
                out = json.dumps(reply(self.path, body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _do

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def cfg(profile, **kw):
    return PF.config_for({"profile": profile, **kw})


# ------------------------------------------------------------------ fit / profiles
@pytest.mark.parametrize("w,h,want", [(32, 32, (64, 64)), (64, 32, (64, 16)), (128, 64, (64, 64)),
                                      (32, 8, (64, 16)), (64, 64, (64, 64)), (64, 16, (64, 16)),
                                      (128, 32, (64, 16)), (16, 16, (64, 64))])
def test_render_size(w, h, want):
    assert FIT.render_size(w, h) == want


def test_fit_crisp_upscale_and_box_downscale():
    src = grad(64, 64)
    up = FIT.fit(src, 128, 64)
    assert up.size == (128, 64) and up.getpixel((0, 0)) == (0, 0, 0)        # letterboxed
    big = FIT.fit(grad(64, 16), 128, 64)
    assert big.getpixel((1, 16)) == big.getpixel((0, 16)) == (0, 0, 0)        # 2x nearest: pixel pairs
    assert FIT.fit(src, 32, 32).size == (32, 32)


def test_profile_library_valid():
    ps = PF.load()
    assert len(ps) >= 20
    drivers = {p["driver"] for p in ps.values()}
    assert drivers == {"ipixel", "virtual", "wled", "udp", "pixoo", "serial", "tidbyt"}
    for p in ps.values():
        d = DR.make(PF.config_for({"profile": p["id"], "host": "127.0.0.1", "options": {"device_id": "x"}}))
        c = d.capabilities()
        assert (c.width, c.height) == (p["width"], p["height"])
    with pytest.raises(ValueError):
        PF.validate({"id": "x", "label": "x", "driver": "nope", "width": 8, "height": 8})


# ------------------------------------------------------------------ UDP protocols
def test_ddp_bytes_and_push_flag():
    u = Udp()
    d = DR.make(cfg("hub75-64x32-ddp", host="127.0.0.1", port=u.port, limits={"max_brightness": 100}))
    run(d.connect())
    img = grad(64, 32)
    run(d.send_frame(img))
    pk = u.recv(5)                                     # 64*32*3 = 6144 bytes -> 4x1440 + 384
    data = b""
    for i, p in enumerate(pk):
        flags, seq, typ, dest, off, ln = struct.unpack(">BBBBIH", p[:10])
        assert flags & 0xC0 == 0x40 and seq == 1 and typ == 0x0B and dest == 1
        assert off == i * 1440 and ln == len(p) - 10
        assert bool(flags & 1) == (i == 4)
        data += p[10:]
    assert data == img.tobytes()
    run(d.close())


def test_e131_packet_layout():
    u = Udp()
    d = DR.make(cfg("hub75-64x32-e131", host="127.0.0.1", port=u.port, limits={"max_brightness": 100}))
    run(d.connect())
    img = grad(64, 32)
    run(d.send_frame(img))
    pk = u.recv(13)                                    # 6144 / 510 -> 13 universes
    data = b""
    for i, p in enumerate(pk):
        assert p[0:2] == b"\x00\x10" and p[4:16] == b"ASC-E1.17\x00\x00\x00"
        assert struct.unpack(">I", p[18:22])[0] == 4 and struct.unpack(">I", p[40:44])[0] == 2
        assert p[22:38] == d.cid
        assert p[44:53] == b"OrcPixels" and p[108] == 100
        assert struct.unpack(">H", p[113:115])[0] == i + 1                 # universe
        assert p[117] == 0x02 and p[118] == 0xA1
        n = struct.unpack(">H", p[123:125])[0]
        assert p[125] == 0 and n == len(p) - 125
        assert struct.unpack(">H", p[16:18])[0] & 0x0FFF == len(p) - 16
        data += p[126:]
    assert data == img.tobytes()
    assert PK.e131_multicast(1) == "239.255.0.1" and PK.e131_multicast(300) == "239.255.1.44"


def test_artnet_artdmx():
    u = Udp()
    d = DR.make(cfg("hub75-64x32-artnet", host="127.0.0.1", port=u.port))
    run(d.connect())
    run(d.send_frame(grad(64, 32)))
    pk = u.recv(13)
    for i, p in enumerate(pk):
        assert p[:8] == b"Art-Net\x00" and p[8:10] == b"\x00\x50" and p[10:12] == b"\x00\x0e"
        assert p[12] == 1 and p[14] == i and p[15] == 0
        assert struct.unpack(">H", p[16:18])[0] == len(p) - 18 and (len(p) - 18) % 2 == 0


def test_brightness_and_power_limits_in_software():
    u = Udp()
    d = DR.make(cfg("hub75-64x32-ddp", host="127.0.0.1", port=u.port))
    run(d.connect())
    run(d.set_brightness(100))                         # clamped to profile max 70
    assert d.level == 70
    run(d.send_frame(Image.new("RGB", (64, 32), (200, 200, 200))))
    assert u.recv(5)[0][10] == 140
    run(d.set_power(False))                            # black frame
    assert all(b == 0 for p in u.recv(5) for b in p[10:])


# ------------------------------------------------------------------ WLED
def test_wled_json_and_ddp_and_realtime():
    http = Http(lambda path, body: {"leds": {"matrix": {"w": 32, "h": 8}}, "ver": "0.14.0"}
                if path == "/json/info" else {"success": True})
    try:
        u = Udp()
        d = DR.make(cfg("wled-32x8", host="127.0.0.1", port=u.port, options={"http_port": http.port}))
        run(d.connect())
        assert (d.width, d.height) == (32, 8)
        run(d.set_brightness(50))
        run(d.set_power(True))
        assert [r[2] for r in http.reqs if r[1] == "/json/state"] == [{"bri": 128}, {"on": True}]
        run(d.send_frame(grad(64, 16)))
        p = u.recv(1)[0]
        assert p[0] == 0x41 and len(p) == 10 + 32 * 8 * 3
        rt = DR.make(cfg("wled-32x32-realtime", host="127.0.0.1", port=u.port,
                         options={"http_port": http.port, "size_from_device": False}))
        run(rt.connect())
        run(rt.send_frame(grad(64, 64)))
        a, b, c = u.recv(3)                            # 1024 LEDs -> 489 + 489 + 46
        assert a[:4] == bytes([4, 2, 0, 0]) and b[2:4] == struct.pack(">H", 489)
        assert len(a) == 4 + 489 * 3 and len(c) == 4 + 46 * 3
    finally:
        http.close()


# ------------------------------------------------------------------ Pixoo
def test_pixoo_http_api():
    http = Http(lambda path, body: {"error_code": 0, "PicId": 7} if body["Command"] == "Draw/GetHttpGifId"
                else {"error_code": 0})
    try:
        d = DR.make(cfg("pixoo-64", host="127.0.0.1", port=http.port, limits={"max_fps": 100}))
        run(d.connect())
        img = grad(64, 64)
        run(d.send_frame(img))
        cmd = [r[2] for r in http.reqs]
        assert [c["Command"] for c in cmd] == ["Draw/ResetHttpGifId", "Draw/GetHttpGifId", "Draw/SendHttpGif"]
        f = cmd[-1]
        assert f["PicID"] == 8 and f["PicWidth"] == 64 and f["PicNum"] == 1 and f["PicOffset"] == 0
        assert base64.b64decode(f["PicData"]) == img.tobytes()
        run(d.send_animation([img, img, img], [100, 100, 100]))
        anim = [r[2] for r in http.reqs][-3:]
        assert [a["PicOffset"] for a in anim] == [0, 1, 2] and {a["PicNum"] for a in anim} == {3}
        assert {a["PicID"] for a in anim} == {9}
        run(d.set_brightness(40))
        assert http.reqs[-1][2] == {"Command": "Channel/SetBrightness", "Brightness": 40}
    finally:
        http.close()


# ------------------------------------------------------------------ Tidbyt / Tronbyt
def test_tidbyt_push(monkeypatch):
    http = Http(lambda path, body: {})
    try:
        monkeypatch.setenv("TRONBYT_API_KEY", "test-key")
        d = DR.make(cfg("tronbyt-64x32", host=f"http://127.0.0.1:{http.port}",
                        options={"device_id": "dev1"}, limits={"max_fps": 100}))
        run(d.connect())
        run(d.send_animation([grad(64, 16), Image.new("RGB", (64, 16), "red")], [100, 100]))
        m, path, body, hdr = http.reqs[-1]
        assert m == "POST" and path == "/v0/devices/dev1/push"
        assert hdr["Authorization"] == "Bearer test-key"
        assert body["installationID"] == "orcpixels" and body["background"] is False
        im = Image.open(io.BytesIO(base64.b64decode(body["image"])))
        assert im.format == "WEBP" and im.size == (64, 32) and getattr(im, "n_frames", 1) == 2
    finally:
        http.close()
    monkeypatch.delenv("TIDBYT_API_KEY", raising=False)
    with pytest.raises(ConnectionError):
        run(DR.make(cfg("tidbyt-64x32", options={"device_id": "d"})).connect())


# ------------------------------------------------------------------ serial
class FakeSerial:
    def __init__(self):
        self.out = b""

    def write(self, b):
        self.out += b

    def close(self):
        pass


def test_serial_adalight_and_tpm2_serpentine():
    d = DR.make(cfg("serial-adalight-16x16", serial="COM5"))
    d.ser, d._connected = FakeSerial(), True
    d.limits.max_fps = 1000
    img = grad(16, 16)
    run(d.set_brightness(40))
    run(d.send_frame(img))
    o = d.ser.out
    n = 255
    assert o[:3] == b"Ada" and o[3:6] == bytes([n >> 8, n & 0xFF, (n >> 8) ^ (n & 0xFF) ^ 0x55])
    px = o[6:]
    assert len(px) == 768
    row1 = px[16 * 3:32 * 3]                           # 2nd row reversed (serpentine)
    assert row1[:3] == bytes(int(v * 0.4) for v in img.getpixel((15, 1)))
    t = DR.make(cfg("serial-tpm2-32x8", serial="/dev/ttyACM0"))
    t.ser, t._connected = FakeSerial(), True
    run(t.send_frame(grad(32, 8)))
    assert t.ser.out[:4] == bytes([0xC9, 0xDA, 0x03, 0x00]) and t.ser.out[-1] == 0x36


# ------------------------------------------------------------------ virtual + DriverSign
def test_virtual_driver_and_driversign(tmp_path):
    from orcpixels.drivers.sign import DriverSign
    disp = {"id": "v128", "driver": "virtual", "profile": "virtual-128x64", "width": 128, "height": 64,
            "options": {"out_dir": str(tmp_path)}, "address": "", "label": "Virtual"}
    s = DriverSign(disp)
    assert (s.state.width, s.state.height) == (64, 64)     # renders the curated 64x64 set
    run(s.send_image(grad(64, 64)))
    assert (tmp_path / "last.png").exists() and Image.open(tmp_path / "last.png").size == (128, 64)
    buf = io.BytesIO()
    grad(64, 64).save(buf, format="GIF", save_all=True, append_images=[Image.new("RGB", (64, 64), "red")], duration=[50, 50])
    run(s.send_gif(buf.getvalue()))
    assert Image.open(tmp_path / "last.gif").n_frames == 2
    from orcpixels.drivers.base import NotSupported
    with pytest.raises(NotSupported):
        run(s.set_program_list([1]))
    assert s.device_png()


def test_driversign_streams_animation_for_pixel_stream_panels():
    from orcpixels.drivers.sign import DriverSign
    u = Udp()
    disp = {"id": "h", "driver": "udp", "profile": "hub75-64x32-ddp", "host": "127.0.0.1", "port": u.port,
            "width": 64, "height": 32, "address": ""}
    s = DriverSign(disp)

    async def go():
        buf = io.BytesIO()
        fr = [Image.new("RGB", (64, 16), c) for c in ((255, 0, 0), (0, 255, 0))]
        fr[0].save(buf, format="GIF", save_all=True, append_images=fr[1:], duration=[40, 40], loop=0)
        await s.send_gif(buf.getvalue())
        await asyncio.sleep(0.3)
        await s._stop_anim()
    run(go())
    assert s.driver.sent_frames >= 3


def test_web_add_virtual_display(tmp_path, monkeypatch):
    try:
        from fastapi.testclient import TestClient
        TestClient.__init__                                    # starlette needs httpx at construction
        import httpx  # noqa: F401
    except (ImportError, RuntimeError):
        pytest.skip("httpx not installed")

    from orcpixels import displays as D
    from orcpixels import web
    from orcpixels import playlist as PL
    from orcpixels import settings as S
    monkeypatch.setattr(D, "PATH", tmp_path / "displays.json")
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    try:
        c = TestClient(web.create_multi_app())
    except RuntimeError as e:
        pytest.skip(str(e))
    with c:
        assert any(p["id"] == "hub75-64x32-ddp" for p in c.get("/api/displays/profiles").json()["profiles"])
        r = c.post("/api/displays/add", json={"profile": "virtual-64x32", "label": "Preview"})
        assert r.status_code == 200, r.text
        info = r.json()
        assert info["driver"] == "virtual" and info["render_size"] == [64, 16]
        assert (info["width"], info["height"], info["id"]) == (64, 32, "virtual64x32")
        assert c.post("/api/displays/add", json={"profile": "wled-32x8", "host": "bad host!"}).status_code == 400
