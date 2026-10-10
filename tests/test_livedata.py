"""Live Data screens: every fetcher parsed from trimmed real responses (mocked HTTP, no
network), politeness/stale behaviour, all 30 screens render 64x16 with seamless loops,
the stream engine's takeover / fallback / resume rules, and the /api/livedata routes."""
import asyncio
import json
import time
from pathlib import Path

import pytest

from orcpixels import livedata as LD
from orcpixels import livedata_screens as SC
from orcpixels import settings as S
from orcpixels.loopmetric import loop_metric_frames
from orcpixels.sources import livefeeds as LF

FX = Path(__file__).parent / "fixtures" / "livedata"
ROUTES = {"stations/KPDX": "nws_obs.json", "api.weather.gov/points/": "nws_points.json",
          "gridpoints/PQR": "nws_forecast.json", "alerts/active": "nws_alerts_some.json",
          "air-quality-api.open-meteo.com": "aqi.json", "planetary-k-index": "kp.json",
          "solar-wind-speed": "sw_speed.json", "solar-wind-mag-field": "sw_mag.json",
          "xray-flares-latest": "xray_flares.json", "10cm-flux": "f107.json",
          "hamqsl.com": "hamqsl.xml", "wheretheiss.at": "iss.json", "celestrak.org": "tle.json",
          "earthquake.usgs.gov": "quake.json", "opensky-network.org": "opensky.json",
          "tidesandcurrents.noaa.gov": "tides.json", "waterservices.usgs.gov": "river.json",
          "api.waterdata.usgs.gov": "river_ogc.json",
          "api.github.com": "github.json",
          # markets + news (screens 16-30)
          "finance.yahoo.com/v7/finance/spark": "yahoo_spark.json",
          "finance.yahoo.com/v8/finance/chart": "yahoo_chart.json",
          "api.coingecko.com": "coingecko.json", "api.coinbase.com": "coinbase.json",
          "frankfurter": "frankfurter.json", "v0/topstories": "hn_top.json", "v0/item/": "hn_item.json",
          "feeds.npr.org": "npr.xml", "feeds.bbci.co.uk": "bbc.xml", "opb.org": "opb.xml",
          "oregonlive.com": "oregonlive.xml", "arstechnica": "ars.xml", "theverge.com": "verge.xml",
          "arrl.org": "arrl.xml", "reddit.com": "reddit_ham.xml"}


class FakeNet:
    def __init__(self):
        self.calls, self.down, self.override = [], False, {}

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        assert timeout and timeout <= 10
        if self.down:
            raise TimeoutError("timed out")
        for k, f in {**ROUTES, **self.override}.items():
            if k in url:
                return (FX / f).read_bytes()
        raise AssertionError("unexpected URL " + url)


@pytest.fixture(autouse=True)
def net(monkeypatch, tmp_path):
    n = FakeNet()
    monkeypatch.setattr(LF, "HTTP", n)                       # never the real network
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")   # never the real settings
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("USGS_API_KEY", raising=False)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    from orcpixels.sources import ft8feed as FT

    def no_serial(port, baud):                                 # never a real COM port in tests
        raise OSError(f"serial disabled in tests ({port})")
    monkeypatch.setattr(FT, "SERIAL_OPEN", no_serial)
    FT.SOURCE.reset()
    return n


def fetch(cls):
    f = cls()
    assert asyncio.run(f.refresh()) is True
    assert f.error is None, f.error
    return f


# ------------------------------------------------------------------ fetchers
def test_nws_now():
    v = fetch(LF.NwsNow).value
    assert isinstance(v["temp_f"], int) and -40 < v["temp_f"] < 130
    assert v["desc"] and "rh" in v and "wind_mph" in v


def test_nws_forecast_caches_grid_url(net):
    f = fetch(LF.NwsForecast)
    assert len(f.value["periods"]) >= 4 and f.value["periods"][0]["name"]
    n_points = sum("/points/" in u for u, _ in net.calls)
    asyncio.run(f.refresh(force=True))
    assert sum("/points/" in u for u, _ in net.calls) == n_points == 1   # grid URL cached


def test_nws_alerts_some_and_none(net):
    v = fetch(LF.NwsAlerts).value
    assert v["count"] == 2 and {a["event"] for a in v["alerts"]} >= {"Wind Advisory"}
    net.override = {"alerts/active": "nws_alerts_none.json"}
    v = fetch(LF.NwsAlerts).value
    assert v["count"] == 0 and v["alerts"] == []


def test_aqi():
    v = fetch(LF.AirQuality).value
    assert 0 <= v["aqi"] < 500 and v["pm25"] is not None


def test_spacewx():
    v = fetch(LF.SpaceWx).value
    assert len(v["kp"]) >= 8 and all(0 <= k <= 9 for k in v["kp"])
    assert v["speed"] > 100 and v["bz"] is not None


def test_xray():
    v = fetch(LF.XRay).value
    assert v["current"][0] in "ABCMX" and v["sfi"] > 50
    assert LF.xray_flux("M1.0") == pytest.approx(1e-5) and LF.xray_flux("C2.5") == pytest.approx(2.5e-6)


def test_hf_bands():
    v = fetch(LF.HfBands).value
    assert set(v["bands"]) == set(SC.BANDS)
    assert all(b["day"] in ("Good", "Fair", "Poor") for b in v["bands"].values())
    assert int(v["sfi"]) > 50


def test_iss_position_track_and_pass():
    f = fetch(LF.Iss)
    v = f.value
    assert -90 <= v["lat"] <= 90 and -180 <= v["lon"] <= 180 and v["alt_km"] > 300
    assert len(v["track"]) > 10
    el = f.tle
    la, lo = LF.subpoint(el, el["epoch"])
    assert -52 <= la <= 52                                  # ISS inclination 51.6
    ps = v["pass"]
    assert ps is None or (ps["los"] > ps["aos"] and 10 <= ps["max_el"] <= 90)


def test_quakes_pnw_earthquakes_only(net):
    v = fetch(LF.Quakes).value
    assert v["quakes"] and all(q["mag"] >= 0 for q in v["quakes"])
    assert all("explosion" not in q.get("type", "earthquake") for q in v["quakes"])
    assert any("eventtype=earthquake" in u for u, _ in net.calls)


def test_aircraft():
    v = fetch(LF.Aircraft).value
    assert v["count"] >= 0 and len(v["closest"]) <= 3
    if len(v["closest"]) > 1:
        assert v["closest"][0]["km"] <= v["closest"][1]["km"]


def test_tides_and_interpolation():
    v = fetch(LF.Tides).value
    ev = v["events"]
    assert len(ev) >= 4 and {e["type"] for e in ev} == {"H", "L"}
    a, b = ev[0], ev[1]
    mid = LF.tide_height(ev, (a["t"] + b["t"]) / 2)
    assert mid == pytest.approx((a["v"] + b["v"]) / 2, abs=0.01)
    assert LF.tide_height(ev, a["t"]) == pytest.approx(a["v"])


def test_river_ogc_then_legacy_fallback(net, monkeypatch):
    v = fetch(LF.River).value
    assert v["via"] == "ogc" and "WILLAMETTE" in v["site"].upper()
    assert len(v["series"]) > 10 and isinstance(v["ft"], float) and v["trend"] is not None
    assert "X-Api-Key" not in net.calls[-1][1]

    def ogc_down(url, headers, timeout):
        if "api.waterdata" in url:
            net.calls.append((url, dict(headers)))
            raise OSError("503")
        return net(url, headers, timeout)
    monkeypatch.setattr(LF, "HTTP", ogc_down)
    monkeypatch.setenv("USGS_API_KEY", "k1")
    v = fetch(LF.River).value
    assert v["via"] == "nwis" and "WILLAMETTE" in v["site"].upper() and len(v["series"]) > 10
    assert net.calls[-2][1].get("X-Api-Key") == "k1"


def test_sun_moon_local(net):
    import datetime as dt
    rise, sset = LF.sun_times(dt.date(2026, 10, 2), *LF.HOME)
    assert LF.hm12(rise) in ("7:10A", "7:11A") and LF.hm12(sset) in ("6:49P", "6:50P", "6:51P")
    m = LF.moon_phase(LF.parse_iso("2026-09-26T16:49:00Z"))          # full moon
    assert m["illum"] > 0.97
    fetch(LF.SunMoon)
    fetch(LF.UtcClock)
    assert net.calls == []                                           # no network


def test_github_optional_token(net, monkeypatch):
    v = fetch(LF.GitHub).value
    assert v["repos"] > 0 and v["stars"] >= v["top"][0][1]
    assert "Authorization" not in net.calls[-1][1]
    monkeypatch.setenv("GITHUB_TOKEN", "t0k")
    fetch(LF.GitHub)
    assert net.calls[-1][1]["Authorization"] == "Bearer t0k"


def test_pt_dst():
    assert LF.pt_offset_h(LF.parse_iso("2026-07-01T12:00:00Z")) == -7
    assert LF.pt_offset_h(LF.parse_iso("2026-12-01T12:00:00Z")) == -8
    assert LF.hm12(LF.parse_iso("2026-10-02T19:05:00Z")) == "12:05P"


# ------------------------------------------------------------------ politeness / stale
def test_poll_interval_backoff_and_stale(net):
    f = fetch(LF.NwsNow)
    n = len(net.calls)
    assert asyncio.run(f.refresh()) is False and len(net.calls) == n     # not due yet
    good = f.value
    net.down = True
    f.attempt_at -= f.poll_s + 1                                         # now due
    assert asyncio.run(f.refresh()) is True
    assert f.error and f.value == good and f.usable and f.stale          # last good, stale
    assert f.retry_s() == 30 and not f.due()                             # backoff
    f.attempt_at -= 31
    asyncio.run(f.refresh())
    assert f.fails == 2 and f.retry_s() == 60
    f.ok_at -= f.max_stale + 1
    assert not f.usable                                                  # too old to show
    net.down = False
    f.attempt_at -= 61
    asyncio.run(f.refresh())
    assert f.error is None and f.usable and not f.stale


MARKETS = {"stocks", "indexes", "commod", "rates", "spark", "crypto"}


def test_polite_poll_intervals():
    feeds = SC.make_feeds()
    for k, f in feeds.items():
        assert f.poll_s >= (30 if k == "iss" else 120 if k in MARKETS else 15 if k.startswith("ft8_") else 300), k
    assert feeds["hf_bands"].poll_s >= 3600 and feeds["aircraft"].poll_s >= 300
    assert all(f.key_env in (None, "GITHUB_TOKEN", "USGS_API_KEY", "X_BEARER_TOKEN") for f in feeds.values())
    assert len(feeds) == 39 and set(feeds) == set(SC.SCREENS) and SC.OPTIONAL == {"x_feed"}


def test_http_get_sends_user_agent(monkeypatch):
    seen = {}

    class R:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return b"{}"

    def fake_urlopen(req, timeout=None):
        seen["ua"], seen["t"], seen["m"] = req.get_header("User-agent"), timeout, req.get_method()
        return R()
    monkeypatch.setattr(LF.urllib.request, "urlopen", fake_urlopen)
    LF.http_get("https://example.invalid/x")
    assert "OrcPixels" in seen["ua"] and seen["t"] == LF.HTTP_TIMEOUT_S and seen["m"] == "GET"


# ------------------------------------------------------------------ screens
def loaded_feeds(net=None):
    feeds = SC.make_feeds()

    async def go():
        await asyncio.gather(*(f.refresh() for f in feeds.values()))
    asyncio.run(go())
    return feeds


@pytest.mark.parametrize("key", [k for k in SC.SCREENS if k not in SC.OPTIONAL and not k.startswith("ft8_")])
def test_screen_renders_and_loops_seamlessly(key, net):     # FT8 screens: tests/test_ft8.py (serial data)
    feeds = loaded_feeds()
    f = feeds[key]
    assert f.error is None, f.error
    now = time.time()
    for t in (now, now + 3 * 3600, now + 11 * 3600):                       # day + night states
        n = SC.loop_len(key, f)
        fr = [SC.render(key, f, i, t) for i in range(n)]
        assert all(im.size == (64, 16) for im in fr)
        assert any(im.getbbox() for im in fr)
        m = loop_metric_frames(fr)
        assert m["ok"], (key, m)
        assert SC.render(key, f, n, t).tobytes() == fr[0].tobytes()     # k wraps to the loop


def test_alerts_none_screen_and_stale_dot(net):
    net.override = {"alerts/active": "nws_alerts_none.json"}
    f = fetch(LF.NwsAlerts)
    img = SC.render("alerts", f, 0)
    assert img.getpixel((63, 0)) == (0, 0, 0)
    f.error = "TimeoutError: timed out"
    assert SC.render("alerts", f, 0).getpixel((63, 0)) == SC.STALE


def test_no_data_and_loading_screens(net):
    f = LF.Quakes()
    assert SC.render("quake", f, 0).getbbox()                          # LOADING
    net.down = True
    asyncio.run(f.refresh())
    assert f.value is None and f.error
    assert SC.render("quake", f, 0).getbbox()                          # NO DATA


def test_bad_payload_never_raises(net):
    f = LF.NwsNow()
    f.value, f.ok_at = {"nonsense": 1}, time.time()
    assert SC.render("wx_now", f, 0).size == (64, 16)


def test_led_gif_preview(net):
    feeds = loaded_feeds()
    data = LD.led_gif("spacewx", feeds["spacewx"], 2)
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(data))
    assert im.format == "GIF" and im.n_frames == SC.loop_len("spacewx", feeds["spacewx"])


# ------------------------------------------------------------------ engine
class FakeSign:
    rotate_180 = True
    supervised = False

    def __init__(self):
        self.connected = True
        self.last_frame = self.last_gif = None
        self.frames = 0
        self.lock = asyncio.Lock()
        self.display_seq = 0
        self.live_seq = None
        self.showing = None
        self.calls = []

    def set_showing(self, kind, name=None, **extra):
        self.showing = {"kind": kind, "name": name, **extra}

    calls: list

    async def send_image(self, img):                     # legacy transport (SignClient.send_image)
        self.calls.append(("send_image", img.size))
        async with self.lock:
            await asyncio.sleep(0.002)
            self.frames += 1
            self.last_frame = img
        self.display_seq += 1
        self.set_showing("image", "image")

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def send_live_png(self, png, upright, chunk=244, response=False, ack_timeout=None,
                            timeout=None, expect_seq=None):
        self.calls.append(("send_live_png", chunk, response, png[25]))   # png[25] = PNG colour type
        async with self.lock:
            if expect_seq is not None and self.display_seq != expect_seq:
                from orcpixels.ble_sign import TakenOver
                raise TakenOver("taken")
            assert png[:8] == b"\x89PNG\r\n\x1a\n" and upright.size == (64, 16)
            await asyncio.sleep(0.002)
            self.frames += 1
            self.last_frame = upright
            self.live_seq = self.display_seq
        if not self.showing or self.showing.get("kind") != "live":
            self.set_showing("live", "OrcSDR Live")

    async def preset(self, name):
        async with self.lock:
            await asyncio.sleep(0.01)
            self.last_frame = object()
        self.display_seq += 1
        self.set_showing("preset", name)


class ShortNow(LF.NwsNow):
    """Same parser; tiny politeness numbers so the outage plays out in milliseconds."""
    poll_s = 0.02

    @property
    def max_stale(self):
        return 0.15

    @property
    def stale_after(self):
        return 0.05


def make_engine(sign=None, transport="fast"):
    calls = []

    async def fallback():
        calls.append("playlist")
        return "playlist test3"

    feeds = SC.make_feeds()
    feeds["wx_now"] = ShortNow()
    live = LD.DataLive(sign or FakeSign(), fallback=fallback, feeds=feeds)
    live.transport = transport
    live.poll_s = 0.01
    live.interval = 0.0
    live.loss_fails, live.loss_min_s = 3, 0.05
    live.probe_s, live.probe_max_s, live.confirm_s = 0.03, 0.06, 0.02
    live.ack_timeout_s, live.send_timeout_s, live.connect_timeout_s = 0.1, 0.2, 0.2
    return live, calls


async def until(cond, timeout=5.0):
    t = time.monotonic()
    while not cond():
        if time.monotonic() - t > timeout:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.005)


def test_engine_streams_falls_back_once_and_resumes(net):
    async def run():
        live, calls = make_engine()
        await live.start(screens=["wx_now"], interval=0.0)
        live.interval = 0.0
        await until(lambda: live.sign.frames >= 3)
        assert live.sign.showing["name"] == "Live Data: Weather now"
        net.down = True                                       # every source goes away
        await until(lambda: live.state == "waiting")
        await asyncio.sleep(0.1)
        assert calls == ["playlist"] and live.enabled         # fallback exactly once, still armed
        frames = live.sign.frames
        await asyncio.sleep(0.1)
        assert live.sign.frames == frames                     # no data frames while waiting
        net.down = False
        await until(lambda: live.state == "streaming" and live.sign.frames > frames)
        assert live.resumes == 1 and calls == ["playlist"]
        await live.stop()
        assert not live.enabled
    asyncio.run(run())


def test_engine_starts_into_fallback_when_source_never_answers(net):
    async def run():
        net.down = True
        live, calls = make_engine()
        await live.start(screens=["wx_now"])
        await until(lambda: live.state == "waiting")
        assert calls == ["playlist"] and live.sign.frames == 0 and live.enabled
        await live.stop()
    asyncio.run(run())


def test_preset_takeover_disarms_with_reason(net):
    async def run():
        live, _ = make_engine()
        await live.start(screens=["clock"])
        await until(lambda: live.sign.frames >= 2)
        await live.sign.preset("p_orc_eyes")
        await until(lambda: not live.enabled)
        assert live.stopped_reason == "preset p_orc_eyes sent" and live.state == "off"
    asyncio.run(run())


def test_rotation_cycles_usable_screens(net):
    async def run():
        live, _ = make_engine()
        await live.start(screens=["clock", "sunmoon", "quake"], rotate=True, rotate_s=5)
        live.rotate_s = 0.05
        seen = set()
        t = time.monotonic()
        while time.monotonic() - t < 2 and len(seen) < 3:
            seen.add(live.current)
            await asyncio.sleep(0.01)
        assert seen == {"clock", "sunmoon", "quake"}
        await live.stop()
    asyncio.run(run())


def test_one_live_source_at_a_time(net):
    from tests.test_orcsdr_watchdog import FakeOrcSDR
    from orcpixels import orcsdr_live as OL

    async def run():
        sign = FakeSign()
        data, _ = make_engine(sign)
        sdr = OL.OrcSDRLive(sign, client=FakeOrcSDR(), fallback=data.fallback)
        sdr.transport = "fast"
        sdr.poll_s = 0.01
        sdr.peers, data.peers = [data], [sdr]
        await data.start(screens=["clock"])
        await until(lambda: sign.frames >= 2)
        await sdr.start(interval=0.0)
        assert not data.enabled and data.stopped_reason == "OrcSDR Live started"
        await until(lambda: sign.showing["name"] == "OrcSDR Live")
        await data.start(screens=["clock"])
        assert not sdr.enabled and sdr.stopped_reason == "Live Data started"
        await until(lambda: sign.showing["name"].startswith("Live Data"))
        await data.stop()
    asyncio.run(run())


def test_switch_screen_while_running_and_persist(net):
    async def run():
        live, _ = make_engine()
        live.persist = True
        await live.start(screens=["clock"])
        assert S.load()[LD.SETTING_ARMED] is True and S.load()[LD.SETTING_SCREENS] == ["clock"]
        await live.start(screens=["sunmoon"])
        assert live.current == "sunmoon" and S.load()[LD.SETTING_SCREENS] == ["sunmoon"]
        await live.stop()
        assert S.load()[LD.SETTING_ARMED] is False
        with pytest.raises(ValueError):
            live.set_selection(screens=["nope"])
    asyncio.run(run())


# ------------------------------------------------------------------ transports (22:17 PT fix)
def test_default_transport_is_legacy_for_both_engines():
    from orcpixels import orcsdr_live as OL
    from tests.test_orcsdr_watchdog import FakeOrcSDR
    assert OL.DEFAULT_TRANSPORT == "legacy"
    assert LD.DataLive(FakeSign()).transport == "legacy"
    assert OL.OrcSDRLive(FakeSign(), client=FakeOrcSDR()).transport == "legacy"
    assert "may freeze" in OL.TRANSPORTS["fast"]


@pytest.mark.parametrize("transport", ["legacy", "fast", "fast-rgb", "fast-acked"])
def test_start_sequence_matches_orcsdr_live(net, transport):
    """Live Data must put exactly the same BLE command sequence on the air as OrcSDR Live
    (same send method, chunking, response mode and PNG flavour), frame for frame."""
    from orcpixels import orcsdr_live as OL
    from tests.test_orcsdr_watchdog import FakeOrcSDR

    async def first_calls(make):
        sign = FakeSign()
        live = make(sign)
        live.transport = transport
        live.interval, live.poll_s = 0.0, 0.01
        await live.start()
        await until(lambda: len(sign.calls) >= 3)
        await live.stop()
        return sign.calls[:3]

    def mk_data(sign):
        return LD.DataLive(sign, fallback=None, feeds=SC.make_feeds())

    def mk_sdr(sign):
        return OL.OrcSDRLive(sign, client=FakeOrcSDR())

    async def run():
        d = await first_calls(mk_data)
        o = await first_calls(mk_sdr)
        assert d == o, (d, o)
        if transport == "legacy":
            assert d[0] == ("send_image", (64, 16))
        else:
            assert d[0][0] == "send_live_png" and d[0][1] == 244
            assert d[0][2] is (transport == "fast-acked")
            assert d[0][3] == (2 if transport == "fast-rgb" else 3)       # RGB vs palette PNG
    asyncio.run(run())


def test_legacy_live_data_streams_and_takeover_still_detected(net):
    async def run():
        live, calls = make_engine(transport="legacy")
        await live.start(screens=["clock"])
        await until(lambda: live.sign.frames >= 3)
        assert all(c[0] == "send_image" for c in live.sign.calls)
        assert live.enabled and live.sign.showing["name"].startswith("Live Data")
        await live.sign.preset("p_orc_eyes")
        await until(lambda: not live.enabled)
        assert live.stopped_reason == "preset p_orc_eyes sent"
    asyncio.run(run())


def test_send_test_frame_one_frame_not_armed(net):
    async def run():
        live, _ = make_engine(transport="legacy")
        out = await live.send_test_frame("xray")
        assert out["transport"] == "legacy" and live.sign.calls == [("send_image", (64, 16))]
        assert not live.enabled and "test frame" in live.sign.showing["name"]
        await live.start(screens=["clock"])
        with pytest.raises(RuntimeError):
            await live.send_test_frame("xray")
        await live.stop()
    asyncio.run(run())


# ------------------------------------------------------------------ routes
@pytest.fixture
def app(tmp_path, monkeypatch):
    from orcpixels import playlist as PL
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    from orcpixels.web import create_app
    return create_app(address=None)


def test_routes_list_preview_config(app):
    from tests.test_routes_http import asgi
    st, r = asgi(app, "GET", "/api/livedata/screens")
    assert st == 200 and len(r["screens"]) == 39
    assert {s["key"] for s in r["screens"]} == set(SC.SCREENS)
    assert all(s["needs_key"] is (s["key"] == "x_feed") for s in r["screens"])
    assert {s["group"] for s in r["screens"]} == {"Weather, sky + radio", "Markets", "News", "Optional", "FT8 / FT4"}
    st, body = asgi(app, "GET", "/api/livedata/screens/clock/frame.png", query="scale=2")
    assert st == 200 and body[:4] == b"\x89PNG"
    st, body = asgi(app, "GET", "/api/livedata/screens/tides/led.gif", query="scale=2")
    assert st == 200 and body[:6] == b"GIF89a"
    st, body = asgi(app, "GET", "/api/livedata/screens/iss/led.png", query="scale=2")
    assert st == 200 and body[:4] == b"\x89PNG"
    st, _ = asgi(app, "GET", "/api/livedata/screens/nope/frame.png")
    assert st == 404
    st, r = asgi(app, "POST", "/api/livedata/config", {"screens": ["iss", "quake"], "rotate": True, "rotate_s": 30})
    assert st == 200 and r["live"]["screens"] == ["iss", "quake"] and r["live"]["rotate"]
    assert S.load()[LD.SETTING_SCREENS] == ["iss", "quake"] and not S.load().get(LD.SETTING_ARMED)
    st, _ = asgi(app, "POST", "/api/livedata/config", {"screens": ["nope"]})
    assert st == 400
    st, _ = asgi(app, "POST", "/api/livedata/live", {"action": "dance"})
    assert st == 400
    st, r = asgi(app, "POST", "/api/livedata/config", {"transport": "fast-rgb"})
    assert st == 200 and r["live"]["transport"] == "fast-rgb" and S.load()[LD.SETTING_TRANSPORT] == "fast-rgb"
    st, _ = asgi(app, "POST", "/api/livedata/config", {"transport": "warp"})
    assert st == 400
    assert "legacy" in r["live"]["transports"]
    assert app.state.orcsdr_live.peers == [app.state.livedata_live]
    assert app.state.livedata_live.peers == [app.state.orcsdr_live]


# ------------------------------------------------------------------ wire capture (22:38 PT)
def _wire():
    import importlib.util
    spec = importlib.util.spec_from_file_location("live_wire_capture",
                                                  Path(__file__).parents[1] / "tools" / "live_wire_capture.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("busy", [False, True])
def test_fast_v2_puts_legacy_bytes_on_air(busy):
    """Real-sign result: only legacy draws. fast-v2 must be byte-, chunk- and write-type-
    identical to legacy (SignClient.send_image) for single- and multi-chunk frames."""
    import random
    from PIL import Image
    W = _wire()
    if busy:
        rnd = random.Random(7)
        img = Image.new("RGB", (64, 16))
        img.putdata([tuple(rnd.choice((0, 40, 120, 255)) for _ in range(3)) for _ in range(1024)])
    else:
        img = SC.render("clock", SC.make_feeds()["clock"], 0, 1791000000.0)
    cap = {t: asyncio.run(W.capture(t, img)) for t in ("legacy", "fast-v2", "fast-rgb", "fast-acked")}
    leg = cap["legacy"]
    assert all(leg["resp_seq"]) and leg["commands"][-1]["png_colortype"] == 2
    v2 = cap["fast-v2"]
    assert (v2["raw"], v2["resp_seq"], v2["chunk_lens"]) == (leg["raw"], leg["resp_seq"], leg["chunk_lens"])
    # the two variants that froze the sign differ from legacy in exactly one respect each
    assert cap["fast-rgb"]["raw"] == leg["raw"] and cap["fast-rgb"]["resp_seq"] != leg["resp_seq"]
    assert cap["fast-acked"]["raw"] != leg["raw"] and all(cap["fast-acked"]["resp_seq"])
    for c in cap.values():                    # same header fields: option 0, slot 0, CRC ok
        png = c["commands"][-1]
        assert png["cmd"] == "0200" and png["option"] == 0 and png["tail"] == "0000" and png["crc_ok"]
        assert png["size"] == png["payload"]
