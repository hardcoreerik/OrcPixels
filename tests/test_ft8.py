"""FT8 / FT4 Live Data screens (OrcSDR decodes): serial-line + HTTP JSON parsing from a real
capture (mocked serial / HTTP only, never a real COM port or the radio), the shared source
(one query for all screens, dedupe, repeats, alerts), offline locations (grid -> place,
callsign -> DXCC, distance / bearing), settings, idle state and seamless loops of all 8."""
import asyncio
import io
import json
import urllib.error

import pytest

from orcpixels import livedata_ft8 as F8
from orcpixels import livedata_screens as SC
from orcpixels import settings as S
from orcpixels.loopmetric import loop_metric_frames
from orcpixels.sources import ft8feed as FT
from orcpixels.sources import hamgeo as G
from orcpixels.sources import livefeeds as LF
from tests.test_livedata import FX, net  # noqa: F401  (autouse: mocked HTTP + temp settings + no serial)

CAPTURE = (FX / "ft8_serial.txt").read_text(encoding="utf-8")
FT8_KEYS = [s.key for s in F8.SCREENS_FT8]


class FakeSerial:
    """Answers the two read-only console queries from the real capture, with RX log noise."""
    opened = []

    def __init__(self, port, baud, reply=CAPTURE):
        self.port, self.baud, self.out, self.written, self.closed = port, baud, b"", [], False
        self.reply = reply
        FakeSerial.opened.append(self)

    def reset_input_buffer(self):
        pass

    def write(self, b):
        self.written.append(b)
        lines = self.reply.splitlines()
        if b.startswith(b"FT8 STATUS"):
            sel = ["ORC_RX level=12"] + [ln for ln in lines if ln.startswith("ORC_FT8_STATUS")]
        else:
            sel = [ln for ln in lines if ln.startswith("ORC_FT8_DECODE")]
        self.out += ("\n".join(sel) + "\n").encode()

    def read(self, n):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk

    def close(self):
        self.closed = True


@pytest.fixture
def serial_ok(monkeypatch):
    FakeSerial.opened = []
    monkeypatch.setattr(FT, "SERIAL_OPEN", lambda port, baud: FakeSerial(port, baud))
    return FakeSerial


def http_404(net):
    def h(url, headers, timeout):
        net.calls.append((url, dict(headers)))
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"nope"))
    return h


def fetch_ft8(key="ft8_ticker"):
    f = SC.SCREENS[key].feed()
    asyncio.run(f.refresh())
    return f


def built(mycall="", reply=CAPTURE, monkeypatch=None):
    """Value as live polls would build it: one merge per cycle, oldest first."""
    if mycall:
        S.save({"livedata_ft8_mycall": mycall})
    st, decs = FT.parse_serial_reply(reply)
    src = FT.Ft8Source()
    slots = sorted({d["utc"] for d in decs})
    for s in slots:
        v = src.merge(st, [d for d in decs if d["utc"] <= s], "serial", now=s + 14)
    return src, v


# ------------------------------------------------------------------ parsing
def test_parse_real_serial_capture():
    st, decs = FT.parse_serial_reply(CAPTURE)
    assert st == {"mode": "FT8", "band": "20m", "dial_hz": 14074000, "runtime": "ready", "clock_ok": True,
                  "rx_running": True, "slots": 33, "last_decodes": 10, "store": 64, "slot_s": 15.0}
    assert len(decs) == 40 and decs[0]["utc"] == 1791501945
    d = decs[-1]
    assert d == {"utc": 1791501885, "mode": "FT8", "hz": 1409, "dt_ms": 180, "snr": -14, "sync": 89, "kind": "QSO",
                 "call": "AJ6CG", "grid": "", "msg": "KI0E AJ6CG R-02"}
    cq = [x for x in decs if x["kind"] == "CQ"]
    assert {x["call"] for x in cq} == {"K4TMR", "K5NWZ", "K5RJM", "KF9UG"} and all(x["msg"].startswith("CQ ") for x in cq)


def test_parse_decode_line_edge_cases():
    d = FT.parse_decode_line('ORC_FT8_DECODE utc=1791501945 mode=FT4 hz=812 dt_ms=-40 snr=- sync=60 kind=FREE '
                             'call=- grid=- msg="TNX BOB 73 GL"')
    assert d["snr"] is None and d["call"] == "" and d["grid"] == "" and d["msg"] == "TNX BOB 73 GL" and d["dt_ms"] == -40
    assert FT.parse_decode_line("ORC_RX level=3") is None
    assert FT.parse_decode_line('ORC_FT8_DECODE mode=FT8 msg="CQ X"') is None          # no utc: dropped
    d = FT.parse_decode_line('ORC_FT8_DECODE utc=1 mode=FT8 hz=1 dt_ms=0 snr=3 sync=1 kind=QSO call=K1ABC grid=RR73 msg="W1AW K1ABC RR73"')
    assert d["grid"] == ""                                                             # RR73 is not a grid
    st, decs = FT.parse_serial_reply("noise ORC_FT8_STATUS mode=FT4 band=40m dial_hz=7047500 clock=0\njunk\n")
    assert st["mode"] == "FT4" and st["slot_s"] == 7.5 and st["clock_ok"] is False and decs == []


def test_parse_http_json_proposed_endpoint():
    st, decs = FT.parse_http_json((FX / "ft8_api.json").read_bytes())
    assert st["band"] == "20m" and st["slot_s"] == 15.0 and st["last_decodes"] == 10 and st["clock_ok"]
    assert len(decs) == 16 and decs[2]["snr"] is None and decs[0]["utc"] >= decs[-1]["utc"]
    st, decs = FT.parse_http_json({"mode": "FT4", "slot_ms": 7500, "decodes": [
        {"utc": 5, "mode": "FT4", "hz": 900, "snr": -3, "kind": "CQ", "call": "EA8XYZ", "grid": "IL18", "msg": "CQ EA8XYZ IL18"},
        {"bogus": 1}]})
    assert st["slot_s"] == 7.5 and len(decs) == 1 and decs[0]["grid"] == "IL18"
    with pytest.raises(ValueError):
        FT.parse_http_json(b'{"mode": "AM"}')                # /api/status is not an FT8 reply


# ------------------------------------------------------------------ transports
def test_http_source_when_firmware_has_api_ft8(net, serial_ok):
    net.override["/api/ft8"] = "ft8_api.json"
    f = fetch_ft8()
    assert f.error is None and f.value["via"] == "http" and serial_ok.opened == []
    assert net.calls[0][0] == "http://192.0.2.10/api/ft8?n=64"
    S.save({"livedata_ft8_url": "http://10.0.0.9"})
    FT.SOURCE.polled_at = 0
    asyncio.run(f.refresh(force=True))
    assert net.calls[-1][0].startswith("http://10.0.0.9/api/ft8")


def test_auto_falls_back_to_serial_and_remembers_404(net, serial_ok, monkeypatch):
    monkeypatch.setattr(LF, "HTTP", http_404(net))
    f = fetch_ft8()
    assert f.error is None and f.value["via"] == "serial" and len(net.calls) == 1
    s = serial_ok.opened[0]
    assert s.port == "COM9" and s.baud == 115200 and s.closed
    assert s.written == [b"FT8 STATUS\n", b"FT8 DECODES 64\n"]         # read-only queries, nothing else
    FT.SOURCE.polled_at = 0
    asyncio.run(f.refresh(force=True))
    assert len(net.calls) == 1 and len(serial_ok.opened) == 2          # 404 cached: straight to serial


def test_serial_only_settings_and_busy_port(net, monkeypatch):
    S.save({"livedata_ft8_source": "serial", "livedata_ft8_port": "COM9", "livedata_ft8_baud": "921600"})
    seen = []

    def busy(port, baud):
        seen.append((port, baud))
        raise OSError("could not open port 'COM9': PermissionError(13, 'Access is denied.')")
    monkeypatch.setattr(FT, "SERIAL_OPEN", busy)
    f = fetch_ft8()
    assert f.value is None and "COM9" in f.error and seen == [("COM9", 921600)] and net.calls == []
    assert SC.render("ft8_ticker", f, 0).getbbox()                       # NO DATA frame, no crash


def test_serial_open_keeps_dtr_rts_low(monkeypatch):
    import serial
    log = []

    class S_:
        def __init__(self):
            self.dtr = self.rts = True

        def open(self):
            log.append((self.port, self.baudrate, self.dtr, self.rts))
    monkeypatch.setattr(serial, "Serial", S_)
    FT._serial_open("COM9", 115200)
    assert log == [("COM9", 115200, False, False)]                     # no ESP32 reset on open


def test_serial_timeout_when_port_is_not_orcsdr(monkeypatch):
    class Dial(FakeSerial):
        def write(self, b):
            self.out += b"ORCDIAL_COMMAND_ERROR unknown\n"
    monkeypatch.setattr(FT, "SERIAL_OPEN", lambda p, b: Dial(p, b))
    with pytest.raises(TimeoutError, match="ORC_FT8_STATUS"):
        FT.serial_query("COM14", 115200, timeout=0.3)


def test_off_source_never_queries(net, serial_ok):
    S.save({"livedata_ft8_source": "off"})
    f = fetch_ft8()
    assert f.value is None and "off" in f.error and net.calls == [] and serial_ok.opened == []


def test_all_eight_screens_share_one_query(net, serial_ok, monkeypatch):
    monkeypatch.setattr(LF, "HTTP", http_404(net))
    feeds = {k: SC.SCREENS[k].feed() for k in FT8_KEYS}
    assert len({f.key for f in feeds.values()}) == 8 and len({f.title for f in feeds.values()}) == 8

    async def go():
        for f in feeds.values():
            await f.refresh()
    asyncio.run(go())
    assert len(serial_ok.opened) == 1 and len(net.calls) == 1
    assert all(f.value is feeds["ft8_ticker"].value for f in feeds.values())
    assert all(f.local and 15 <= f.poll_s <= 60 for f in feeds.values())


# ------------------------------------------------------------------ store
def test_dedupe_and_repeats_across_cycles():
    src, v = built()
    n = len(src.history)
    assert n == 40
    st, decs = FT.parse_serial_reply(CAPTURE)
    src.merge(st, decs, "serial", now=1791501975)                   # same reply again: nothing new
    assert len(src.history) == n
    msgs = [d["msg"] for d in v["ticker"]]
    assert len(msgs) == len(set(msgs))                               # each message once in the ticker
    rep = next(d for d in v["ticker"] if d["msg"] == "CX2CN KC2GTR -23")
    assert rep["rep"] == 2 and rep["utc"] == 1791501945              # newest copy, counted twice
    assert v["ticker"][0]["utc"] == 1791501945 and v["ticker"][0]["snr"] == 4     # newest cycle, strongest first
    assert [c for _, c in v["cycles"][:5]] == [10, 9, 6, 11, 4]
    assert len(v["last_slot"]) == 10 and v["idle"] is False


def test_top_callers_cq_and_farthest():
    _, v = built()
    top = v["calls"][0]
    assert top["n"] == 2 and v["unique"] == 30
    assert next(c for c in v["calls"] if c["call"] == "N7DNF")["best"] == 6
    assert [d["call"] for d in v["cq"]] == ["K5RJM", "K5NWZ", "K4TMR", "KF9UG"]
    assert v["far"][0]["call"] == "KE4PMS" and v["far"][0]["loc"]["place"] == "MORGANTOWN WV"


def test_idle_when_no_recent_decodes():
    src, v = built()
    v2 = src.build(now=1791501945 + 15 * 6)
    assert v2["idle"] is True and v["idle"] is False
    empty = FT.Ft8Source().merge({"mode": "FT8", "band": "40m", "slot_s": 15.0, "rx_running": True}, [], "serial",
                                 now=1791501990)
    assert empty["idle"] and empty["ticker"] == [] and empty["latest"] is None


# ------------------------------------------------------------------ locations
def test_grid_math_and_places():
    lat, lon = G.grid_latlon("CN85")
    assert (lat, lon) == (45.5, -123.0) and G.latlon_grid(45.52, -122.68) == "CN85"
    assert G.grid_latlon("CN85pm")[0] == pytest.approx(45.52, abs=0.03)
    km, brg = G.dist_bearing(G.grid_latlon("CN85"), G.grid_latlon("DN13"))
    assert 500 < km < 560 and 100 < brg < 125
    assert G.place_label(G.grid_place("DN13")) == "BOISE ID"
    assert G.place_label(G.grid_place("PM95")).startswith("TOKYO")
    assert G.grid_place("JJ00") is None and not G.valid_grid("RR73") and not G.valid_grid("ZZ99")


def test_callsign_dxcc_fallback():
    assert G.dxcc("JA8NRS")["name"] == "Japan"
    assert G.dxcc("5Z4VJ")["name"] == "Kenya"
    assert G.dxcc("KH6/K7ABC")["name"] == "Hawaii"
    assert G.dxcc("K7ABC/P")["name"] == "United States"
    assert G.dxcc("VE7XYZ")["name"] == "Canada" and G.dxcc("PY5TD")["cont"] == "SA"
    assert G.dxcc("") is None


def test_locate_labels_like_the_ticker():
    l = G.locate("K7ABC", "DN13", "CN85")
    assert (l["place"], l["dist"], l["dir"], l["src"]) == ("BOISE ID", "326MI", "SE", "grid")
    assert G.locate("K7ABC", "DN13", "CN85", miles=False)["dist"] == "525KM"
    j = G.locate("JA8NRS", None, "CN85")
    assert j["place"] == "JAPAN" and j["src"] == "dxcc" and j["dist"].endswith("MI") and j["dir"] == "NW"
    u = G.locate("KC2GTR", None, "CN85")                       # US call, no grid: country only, no fake distance
    assert u["place"] == "USA" and u["dist"] == "" and u["lat"] is not None


def test_home_grid_and_units_settings_relocate():
    S.save({"livedata_ft8_home_grid": "FN31", "livedata_ft8_units": "km"})
    _, v = built()
    d = next(x for x in v["decodes"] if x["call"] == "KE4PMS")
    assert d["loc"]["dist"].endswith("KM") and d["loc"]["km"] < 1000


# ------------------------------------------------------------------ alerts
def test_alerts_my_call_new_grid_dxcc():
    src, v = built(mycall="JA8NRS")
    kinds = [(a["kind"], a["d"]["call"]) for a in v["alerts"]]
    assert kinds == [("MYCALL", "N7DNF"), ("MYCALL", "K7BTW")]   # N7DNF once, although heard in two cycles
    assert v["mycall"] == "JA8NRS" and all(d["mine"] == ("JA8NRS" in d["msg"].split()) for d in v["decodes"])
    t = 1791501945 + 700
    later = [FT.parse_decode_line(f'ORC_FT8_DECODE utc={t} mode=FT8 hz=1210 dt_ms=0 snr=-16 sync=80 kind=CQ '
                                  f'call=ZL2AGY grid=RE78 msg="CQ ZL2AGY RE78"'),
             FT.parse_decode_line(f'ORC_FT8_DECODE utc={t} mode=FT8 hz=800 dt_ms=0 snr=-6 sync=80 kind=QSO '
                                  f'call=K0XYZ grid=EN34 msg="W1AW K0XYZ EN34"')]
    v = src.merge(None, later, "serial", now=t + 14)
    kinds = [(a["kind"], a["d"]["call"]) for a in v["alerts"]]
    assert kinds[:2] == [("NEWGRID", "K0XYZ"), ("NEWDXCC", "ZL2AGY")]


def test_first_batch_raises_no_new_grid_flood():
    st, decs = FT.parse_serial_reply(CAPTURE)
    v = FT.Ft8Source().merge(st, decs, "serial", now=1791501960)
    assert v["alerts"] == [] and v["grids"] >= 15


# ------------------------------------------------------------------ settings
def test_validate_settings():
    assert FT.validate("livedata_ft8_port", "com5") == "COM5"
    assert FT.validate("livedata_ft8_port", "/dev/ttyACM0") == "/dev/ttyACM0"
    assert FT.validate("livedata_ft8_mycall", " k7abc ") == "K7ABC"
    assert FT.validate("livedata_ft8_home_grid", "cn85PM") == "CN85pm"
    assert FT.validate("livedata_ft8_url", "192.0.2.10/") == "http://192.0.2.10"
    assert FT.validate("livedata_ft8_source", "Serial") == "serial"
    assert FT.validate("livedata_ft8_mycall", "") == ""
    for k, bad in (("livedata_ft8_port", "COM9; rm"), ("livedata_ft8_baud", "1234"), ("livedata_ft8_mycall", "HELLO"),
                   ("livedata_ft8_home_grid", "ZZ99"), ("livedata_ft8_source", "wifi"), ("livedata_ft8_units", "ft"),
                   ("nope", "x")):
        with pytest.raises(ValueError):
            FT.validate(k, bad)
    assert FT.CONFIG["livedata_ft8_mycall"][0] == "" and FT.CONFIG["livedata_ft8_home_grid"][0] == ""


@pytest.fixture
def app(tmp_path, monkeypatch):
    from orcpixels import playlist as PL
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    from orcpixels.web import create_app
    return create_app(address=None)


def test_feed_config_route_has_ft8_fields(app):
    from tests.test_routes_http import asgi
    st, r = asgi(app, "GET", "/api/livedata/feed-config")
    keys = {f["key"] for f in r["fields"]}
    assert st == 200 and set(FT.CONFIG) <= keys
    st, r = asgi(app, "POST", "/api/livedata/feed-config", {"values": {"livedata_ft8_mycall": "k7abc",
                                                                      "livedata_ft8_port": "COM18"}})
    assert st == 200 and S.load()["livedata_ft8_mycall"] == "K7ABC" and S.load()["livedata_ft8_port"] == "COM18"
    st, r = asgi(app, "POST", "/api/livedata/feed-config", {"values": {"livedata_ft8_home_grid": "XX"}})
    assert st == 400
    st, r = asgi(app, "GET", "/api/livedata/screens")
    ft = [s for s in r["screens"] if s["group"] == "FT8 / FT4"]
    assert [s["key"] for s in ft] == FT8_KEYS


# ------------------------------------------------------------------ screens
def feed_of(v):
    f = FT.Ft8Feed()
    f.value, f.ok_at, f.attempt_at = v, 1e12, 1e12
    return f


def test_ticker_text_has_mode_snr_call_place_distance():
    _, v = built(mycall="JA8NRS")
    segs = F8.ticker_segs(v)
    text = "".join(t for t, _ in segs)
    assert text.startswith("FT8 +4 N7DNF >JA8NRS BILLINGS MT 677MI E")
    assert any(t == "N7DNF" and c == F8.MAG for t, c in segs)             # my call: magenta
    cq = F8.entry_segs(next(d for d in v["decodes"] if d["call"] == "K5RJM"), "")
    assert "".join(t for t, _ in cq).strip() == "FT8 -8 CQ K5RJM DALLAS TX 1649MI SE"
    assert dict((t, c) for t, c in cq)["CQ "] == SC.AMB
    assert F8.snr_col(4) != F8.snr_col(-12) != F8.snr_col(-20) and F8.snr_col(None) == SC.GREY


@pytest.mark.parametrize("key", FT8_KEYS)
def test_every_screen_loops_seamlessly_and_never_errors(key):
    _, v = built(mycall="JA8NRS")
    f = feed_of(v)
    now = 1791501945 + 21.0
    n = SC.loop_len(key, f)
    fr = [SC.render(key, f, i, now) for i in range(n)]
    assert n >= 2 and all(im.getbbox() for im in fr)
    red_err = [im for im in fr if im.getpixel((5, 11)) == SC.RED and im.getpixel((1, 1)) == SC.LPB]
    assert not red_err
    assert SC.render(key, f, n, now).tobytes() == fr[0].tobytes()        # wraps exactly
    if n > 2:
        assert loop_metric_frames(fr + fr[:1])["ok"] or loop_metric_frames(fr)["ok"]


def test_ticker_marquee_is_a_pure_scroll_including_the_seam():
    _, v = built()
    f = feed_of(v)
    now = 1791501966.0
    n = SC.loop_len("ft8_ticker", f)
    fr = [SC.render("ft8_ticker", f, i, now) for i in range(n)] + [SC.render("ft8_ticker", f, 0, now)]
    st = F8.STEP
    for a, b in zip(fr, fr[1:]):
        assert a.crop((st, F8.TOP, 64, 16)).tobytes() == b.crop((0, F8.TOP, 64 - st, 16)).tobytes()
    assert 30 <= n <= 200                                                # ~1 fps: 8 entries, 2 chars / s


def test_idle_frames_explain_and_animate():
    src, _ = built()
    v = src.build(now=1791501945 + 300)
    f = feed_of(v)
    for key in ("ft8_ticker", "ft8_snr", "ft8_audio"):
        assert SC.loop_len(key, f) == 4 or key != "ft8_ticker"
        fr = [SC.render(key, f, i, 1791502250.0) for i in range(4)]
        assert all(im.getbbox() for im in fr) and len({im.tobytes() for im in fr}) > 1       # dots move
    st = dict(v["status"], rx_running=False)
    f2 = feed_of(dict(v, status=st))
    assert SC.render("ft8_ticker", f2, 0, 1791502250.0).tobytes() != SC.render("ft8_ticker", f, 0, 1791502250.0).tobytes()


def test_cycle_countdown_follows_the_slot():
    _, v = built()
    f = feed_of(v)
    assert F8.countdown(v, 1791501960.0) == 15 and F8.countdown(v, 1791501968.2) == 7
    v4 = dict(v, slot_s=7.5)
    assert F8.countdown(v4, 1791501960.0 + 5) == 3
    a, b = SC.render("ft8_cycle", f, 0, 1791501961.0), SC.render("ft8_cycle", f, 0, 1791501969.0)
    assert a.tobytes() != b.tobytes()


def test_map_points_inside_north_america():
    assert F8.na_xy(*G.grid_latlon("CN85")) == (6, 5)
    assert F8.na_xy(35.0, 139.0) is None
    _, v = built()
    f = feed_of(v)
    im = SC.render("ft8_map", f, 0, 1791501966.0)
    land = sum(1 for x in range(40) for y in range(16) if im.getpixel((x, y)) == F8.LAND)
    assert 200 < land < 500
