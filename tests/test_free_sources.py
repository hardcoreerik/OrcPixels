"""Free-API Live sources (2026-10-14): the Python plugins (Open-Meteo, Launch Library 2,
Spaceflight News, people in space, NASA NeoWs + EONET, Fear & Greed, mempool.space, US debt,
POTA, Jolpica-F1, FreeToGame, CheapShark, Nager.Date, CityBikes, Statuspage) and the
declarative catalog ("Get more free sources"). Recorded fixtures only, no network.
Render + seamless-loop checks at every size run in tests/test_categories.py (all adaptive
screens), so this file covers parsing, zero-setup defaults, keys, location and the sandbox."""
import asyncio
import copy
import json
import time
from pathlib import Path

import pytest

from orcpixels import categories as CAT
from orcpixels import live_catalog as CATL
from orcpixels import live_templates as T
from orcpixels import livedata_screens as SC
from orcpixels import place as PLACE
from orcpixels.sources import livefeeds as LF
from tests.test_livedata import FX, ROUTES

ROOT = Path(__file__).resolve().parents[1]
T_FX = 1792046700.0           # 2026-10-14 23:45 PT, fixture capture time
PY = {"wxw_now", "wxw_hourly", "wxw_week", "wxw_uv", "launch_next", "launches", "space_news", "astros", "neo", "eonet",
      "fear_greed", "btc_network", "us_debt", "pota_spots", "pota_near", "pota_bands", "f1_next", "f1_drivers",
      "f1_last", "free_games", "game_deals", "holidays", "bikes", "status_board"}
CATALOG = {"dad_joke", "useless_fact", "zen_quote", "advice", "trivia", "xkcd", "on_this_day"}


class Net:
    def __init__(self):
        self.calls, self.down, self.extra = [], False, {}

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        if self.down:
            raise TimeoutError("timed out")
        for k, v in self.extra.items():
            if k in url:
                return v
        for k, f in ROUTES.items():
            if k in url:
                return (FX / f).read_bytes()
        raise AssertionError("unexpected URL " + url)


@pytest.fixture
def net(monkeypatch, tmp_path):
    n = Net()
    monkeypatch.setattr(LF, "HTTP", n)
    monkeypatch.setattr(time, "time", lambda: T_FX)
    from orcpixels import settings as S
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(CATL, "user_file", lambda: tmp_path / "free_sources.json")
    T._CACHE.clear()
    return n


def fetched(key):
    f = SC.SCREENS[key].feed()
    asyncio.run(f.refresh(force=True))
    return f


def test_all_free_sources_registered_with_categories():
    assert PY | CATALOG <= set(SC.SCREENS)
    cats = {k: CAT.of(k, SC.SCREENS[k]) for k in PY | CATALOG}
    assert "other" not in cats.values()
    assert {cats[k] for k in ("wxw_now", "wxw_hourly", "wxw_week", "wxw_uv")} == {"weather"}
    assert {cats[k] for k in ("launch_next", "launches", "space_news", "astros", "neo", "eonet")} == {"space"}
    assert {cats[k] for k in ("pota_spots", "pota_near", "pota_bands")} == {"ham"}
    assert cats["f1_next"] == "sports" and cats["status_board"] == "apps" and cats["holidays"] == "local"
    assert cats["free_games"] == "games" and all(cats[k] == "fun" for k in CATALOG)
    assert all(getattr(SC.SCREENS[k], "adaptive", False) for k in PY | CATALOG)


def test_zero_setup_every_source_parses(net):
    """No key, nothing to configure beyond the home location every install already has."""
    for k in sorted(PY | CATALOG):
        f = fetched(k)
        assert f.error is None and f.value, (k, f.error)
        assert not getattr(f, "needs_key", False), k
        assert f.poll_s >= 300, k                          # polite polling


def test_parsed_values(net):
    w = fetched("wxw_now").value
    assert round(w["cur"]["temperature_2m"]) == 58 and not w["metric"] and len(w["days"]) == 7
    assert SC.SCREENS["wxw_now"].value(w, T_FX)["unit"] == "F"
    nl = SC.SCREENS["launch_next"].value(fetched("launch_next").value, T_FX)
    assert nl["big"].startswith("T-") and nl["col"]
    assert fetched("astros").value["n"] == 10
    neo = fetched("neo").value["rows"]
    assert neo == sorted(neo, key=lambda r: r["ld"])
    assert fetched("fear_greed").value["rows"][0]["v"] == 53
    assert fetched("btc_network").value["height"] > 900000
    assert fetched("us_debt").value["rows"][0]["tot"] > 4e13
    near = SC.SCREENS["pota_near"].items(fetched("pota_near").value)
    kms = [float(r[2][:-2].replace(",", "")) for r in near]
    assert kms == sorted(kms)
    assert SC.SCREENS["f1_drivers"].items(fetched("f1_drivers").value)[0][0] == "ANTONELLI"
    assert fetched("holidays").value["cc"] == "US"
    b = fetched("bikes").value
    assert b["net"] == "BIKETOWN" and b["rows"][0]["km"] <= b["rows"][-1]["km"]
    st = fetched("status_board").value["rows"]
    assert st[0]["ind"] != "none"                              # problems sort first
    assert "ANSWER: BELLS" in fetched("trivia").value["body"].upper()  # html entities decoded
    assert fetched("zen_quote").value["attr"].endswith("ZENQUOTES.IO")


def test_shared_cache_one_request_per_api(net):
    for k in ("wxw_now", "wxw_hourly", "wxw_week", "wxw_uv"):
        fetched(k)
    assert sum("open-meteo" in u for u, _ in net.calls) == 1
    for k in ("pota_spots", "pota_near", "pota_bands"):
        fetched(k)
    assert sum("api.pota.app" in u for u, _ in net.calls) == 1
    assert all("OrcPixels" in h.get("User-Agent", "") for _u, h in net.calls)


def test_location_falls_back_to_ft8_grid(net, monkeypatch):
    monkeypatch.setitem(PLACE.OVERRIDE, "home_lat", None)
    monkeypatch.setitem(PLACE.OVERRIDE, "home_lon", None)
    orig = LF._setting
    monkeypatch.setattr(LF, "_setting", lambda k: "CN85" if k == "livedata_ft8_home_grid" else orig(k))
    lat, lon = T.home_latlon()
    assert 45 <= lat <= 46 and -124 <= lon <= -122
    assert fetched("wxw_now").error is None and f"latitude={lat:.3f}" in net.calls[-1][0]
    monkeypatch.setattr(LF, "_setting", lambda k: None)
    with pytest.raises(PLACE.NotConfigured):
        T.home_latlon()


def test_country_and_units(monkeypatch):
    assert T.country() == "US"
    monkeypatch.setitem(PLACE.OVERRIDE, "home_lat", 52.52)
    monkeypatch.setitem(PLACE.OVERRIDE, "home_lon", 13.40)
    assert T.country() == "DE"
    monkeypatch.setitem(PLACE.OVERRIDE, "tv_country", "GB")
    assert T.country() == "GB"


def test_nasa_key_demo_fallback_and_own_key(net, monkeypatch):
    f = fetched("neo")
    assert "api_key=DEMO_KEY" in net.calls[-1][0] and not f.needs_key
    orig = LF._setting
    monkeypatch.setattr(LF, "_setting", lambda k: "abc123" if k == "key_nasa" else orig(k))
    T._CACHE.clear()
    fetched("neo")
    assert "api_key=abc123" in net.calls[-1][0]
    st = {k["service"]: k for k in T.key_status()}["nasa"]
    assert st["set"] and st["optional"] and st["signup"].startswith("https://") and "neo" in st["used_by"]
    assert "abc123" not in json.dumps(T.key_status()) and "abc123" not in SC.SCREENS["neo"].feed().url


# ------------------------------------------------------------------ declarative catalog
def bundled():
    return json.loads(CATL.BUNDLED.read_text(encoding="utf-8"))


def builtin():
    return CATL.builtin_keys(SC.SCREENS)


def test_bundled_catalog_valid_and_in_sync_with_repo_copy():
    clean, errs = CATL.validate(bundled(), builtin())
    assert not errs and {s["id"] for s in clean["sources"]} == CATALOG
    assert json.loads((ROOT / "catalog" / "free_sources.json").read_text(encoding="utf-8")) == bundled()


@pytest.mark.parametrize("patch,msg", [
    ({"url": "http://icanhazdadjoke.com/"}, "https"),
    ({"url": "https://127.0.0.1/x"}, "IP"),
    ({"url": "https://localhost/x"}, "public"),
    ({"url": "https://router.lan/x"}, "public"),
    ({"url": "https://user:pw@example.com/x"}, "plain"),
    ({"url": "https://{lat}.example.com/x"}, "host"),
    ({"url": "https://example.com/{secret}"}, "placeholder"),
    ({"poll_s": 5}, "poll_s"),
    ({"headers": {"Authorization": "Bearer x"}}, "headers"),
    ({"headers": {"Cookie": "a=b"}}, "headers"),
    ({"template": "script"}, "template"),
    ({"fields": {"body": "{joke|eval}"}}, "filter"),
    ({"id": "wx_now"}, "built-in"),
    ({"id": "../../etc"}, "bad id"),
])
def test_catalog_rejects_unsafe_sources(patch, msg):
    doc = bundled()
    doc["sources"][0].update(patch)
    clean, errs = CATL.validate(doc, builtin())
    assert any(msg in e for e in errs), errs
    assert len(clean["sources"]) == len(doc["sources"]) - 1        # only the bad one is skipped


def test_catalog_rejects_whole_bad_documents():
    for doc in (None, [], {"schema": 2, "sources": []}, {"schema": 1, "sources": []}, {"schema": 1, "sources": "x"}):
        with pytest.raises(CATL.Invalid):
            CATL.validate(doc, builtin())


def test_field_paths_are_data_only():
    obj = {"a": [{"b": "x&amp;y"}], "n": 1234567}
    assert CATL.path_get(obj, "a.0.b") == "x&amp;y" and CATL.path_get(obj, "a.5.b") is None
    assert CATL.render_field("a.0.b", obj) == "x&y"
    assert CATL.render_field("N={n|short} {n|num}", obj) == "N=1.2M 1,234,567"
    assert CATL.render_field("{__class__}{a.__class__}{n.real}", obj) == ""    # no attribute access, ever


def test_get_more_sources_adds_screens_without_update(net, tmp_path):
    doc = bundled()
    doc["version"] = "2026.11.01"
    doc["sources"].append({"id": "kanye_quote", "title": "Kanye quote", "short": "KANYE", "category": "fun",
                           "url": "https://api.kanye.rest/", "poll_s": 3600, "template": "text",
                           "fields": {"body": "quote"}})
    net.extra["raw.githubusercontent.com"] = json.dumps(doc).encode()
    net.extra["api.kanye.rest"] = b'{"quote": "I am a pixel"}'
    screens, feeds = dict(SC.SCREENS), {}
    try:
        res = CATL.refresh(screens, feeds)
        assert res["ok"] and res["added"] == ["kanye_quote"] and "kanye_quote" in feeds
        assert (tmp_path / "free_sources.json").exists() and CATL.status()["version"] == "2026.11.01"
        f = feeds["kanye_quote"]
        asyncio.run(f.refresh(force=True))
        assert f.value["body"] == "I am a pixel"
        img = SC.render("kanye_quote", f, 0, T_FX, size=(64, 64)) if "kanye_quote" in SC.SCREENS else None
        assert img is None or img.getbbox()
        # a broken download is rejected and nothing changes
        net.extra["raw.githubusercontent.com"] = b"<html>404</html>"
        res2 = CATL.refresh(screens, feeds)
        assert not res2["ok"] and res2["error"] and "kanye_quote" in screens
        net.extra["raw.githubusercontent.com"] = b"x" * (CATL.MAX_BYTES + 1)
        assert "too large" in CATL.refresh(screens, feeds)["error"]
    finally:
        CATL.CATALOG_KEYS.discard("kanye_quote")


def test_catalog_keyed_source_needs_key_and_never_calls(net, monkeypatch):
    doc = copy.deepcopy(bundled())
    doc["keys"] = [{"service": "zzdemo", "label": "Demo", "signup": "https://example.com/signup"}]
    doc["sources"] = [{"id": "zz_keyed", "title": "Keyed", "short": "KEYED", "category": "fun", "key": "zzdemo",
                       "url": "https://example.com/api?key={key}", "poll_s": 600, "template": "value",
                       "fields": {"big": "v"}}]
    screens = dict(SC.SCREENS)
    try:
        CATL._apply(CATL.validate(doc, builtin())[0], screens)
        f = screens["zz_keyed"].feed()
        assert f.needs_key and "<key>" in f.url
        asyncio.run(f.refresh(force=True))
        assert f.value is None and not any("example.com" in u for u, _ in net.calls)
        from orcpixels import live_adaptive as A
        img = A.render(screens["zz_keyed"], f, 0, T_FX, (64, 64))
        assert img.getbbox()
        assert {k["service"] for k in T.key_status()} >= {"nasa", "zzdemo"}
    finally:
        T.KEYS.pop("zzdemo", None)
        CATL.CATALOG_KEYS.discard("zz_keyed")


def test_new_sources_fall_back_when_offline(net):
    net.down = True
    for k in sorted(PY | CATALOG):
        f = fetched(k)
        assert f.value is None and f.error
        for size in ((64, 16), (64, 64)):
            assert SC.render(k, f, 0, T_FX, size=size).getbbox(), k


def test_settings_routes_catalog_and_keys(net, tmp_path, monkeypatch):
    from orcpixels import playlist as PL
    from tests.test_routes_http import asgi
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    from orcpixels.web import create_app
    app = create_app(address=None)
    st, r = asgi(app, "GET", "/api/livedata/sources-catalog")
    assert st == 200 and r["catalog"]["count"] >= len(CATALOG) and any(k["service"] == "nasa" for k in r["keys"])
    st, r = asgi(app, "POST", "/api/livedata/keys", {"values": {"nasa": "my-key_123"}})
    assert st == 200 and {k["service"]: k for k in r["keys"]}["nasa"]["set"] and "my-key_123" not in json.dumps(r)
    assert json.loads((tmp_path / "settings.json").read_text())["key_nasa"] == "my-key_123"
    assert asgi(app, "POST", "/api/livedata/keys", {"values": {"nasa": "bad key; rm -rf"}})[0] == 400
    assert asgi(app, "POST", "/api/livedata/keys", {"values": {"nope": "x"}})[0] == 400
    net.extra["raw.githubusercontent.com"] = b"not json"
    st, r = asgi(app, "POST", "/api/livedata/sources-catalog/refresh", {})
    assert st == 200 and not r["result"]["ok"] and r["result"]["error"] and r["screens"]
    keys = {s["key"] for s in r["screens"]}
    assert PY | CATALOG <= keys
