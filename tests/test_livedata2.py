"""Live Data screens 16-30 (markets + news) and the optional X screen: fetchers parsed from
trimmed real responses (mocked HTTP only), fallbacks, configurable symbols / RSS URLs,
NYSE hours, polite polling, seamless marquee, the feed-config route."""
import asyncio
import datetime as dt
import json
import time

import pytest

from orcpixels import livedata as LD
from orcpixels import livedata_screens as SC
from orcpixels import livedata_screens2 as S2
from orcpixels import settings as S
from orcpixels.loopmetric import loop_metric_frames
from orcpixels.sources import livefeeds as LF
from orcpixels.sources import ft8feed as FT
from orcpixels.sources import marketnews as MN
from tests.test_livedata import FX, fetch, net  # noqa: F401  (autouse: mocked HTTP + temp settings)


def down(net, *keys):
    """Make URLs containing any of keys fail like a dead host."""
    real = LF.HTTP

    def h(url, headers, timeout):
        if any(k in url for k in keys):
            net.calls.append((url, dict(headers)))
            raise TimeoutError("timed out")
        return real(url, headers, timeout)
    return h


# ------------------------------------------------------------------ markets
def test_stocks_one_spark_request_for_all_symbols(net):
    f = fetch(MN.Stocks)
    q = {x["sym"]: x for x in f.value["quotes"]}
    assert f.value["via"] == "spark" and f.requests == 1 and len(net.calls) == 1
    assert "symbols=AAPL,MSFT,NVDA,GOOGL,AMZN,TSLA" in net.calls[0][0] and "range=1d" in net.calls[0][0]
    assert set(q) == {"AAPL", "MSFT", "NVDA"} and f.value["missing"] == ["GOOGL", "AMZN", "TSLA"]
    a = q["AAPL"]
    assert a["price"] > 0 and a["prev"] and abs(a["pct"] - (a["price"] - a["prev"]) / a["prev"] * 100) < 1e-9
    assert len(a["series"]) > 5


def test_stocks_fall_back_to_v8_chart_per_symbol(net, monkeypatch):
    monkeypatch.setattr(LF, "HTTP", down(net, "/v7/finance/spark"))
    S.save({"livedata_stock_symbols": "aapl, msft"})
    f = fetch(MN.Stocks)
    assert f.value["via"] == "chart" and [q["sym"] for q in f.value["quotes"]] == ["AAPL", "MSFT"]
    assert [u for u, _ in net.calls if "v8/finance/chart/" in u and "query2" in u]


def test_indexes_commodities_rates_names_and_yield(net):
    i = fetch(MN.Indexes).value["quotes"]
    assert [q["name"] for q in i] == ["S&P 500", "DOW", "NASDAQ"]
    c = fetch(MN.Commodities).value["quotes"]
    assert {q["name"] for q in c} >= {"GOLD", "OIL WTI"}
    r = fetch(MN.Rates).value["quotes"]
    assert r[0]["name"] == "10Y" and r[0]["yield"] is True
    assert S2.fmt_chg({"chg": 0.04, "yield": True}) == "+4BP"
    assert S2.fmt_price(4.123, True) == "4.12%" and S2.fmt_price(7722.72) == "7722.7" and S2.fmt_price(48123.4) == "48123"


def test_spark_symbol_configurable_chart_then_spark_fallback(net, monkeypatch):
    S.save({"livedata_spark_symbol": "qqq"})
    f = fetch(MN.Spark)
    assert f.value["via"] == "chart" and "/chart/QQQ?" in net.calls[-1][0] and "interval=5m" in net.calls[-1][0]
    monkeypatch.setattr(LF, "HTTP", down(net, "/v8/finance/chart"))
    S.save({"livedata_spark_symbol": "AAPL"})
    f2 = fetch(MN.Spark)
    assert f2.value["via"] == "spark" and f2.value["quotes"][0]["sym"] == "AAPL"


def test_crypto_coingecko_then_coinbase(net, monkeypatch):
    f = fetch(MN.Crypto)
    assert f.value["via"] == "coingecko" and [c["sym"] for c in f.value["coins"]] == ["BTC", "ETH"]
    assert f.value["coins"][0]["pct"] is not None and "ids=bitcoin,ethereum" in net.calls[0][0]
    monkeypatch.setattr(LF, "HTTP", down(net, "coingecko"))
    f2 = fetch(MN.Crypto)
    assert f2.value["via"] == "coinbase" and f2.value["coins"][0]["price"] > 1000 and f2.value["coins"][0]["pct"] is None
    assert any("/v2/prices/BTC-USD/spot" in u for u, _ in net.calls)


def test_forex_pairs_conventions_and_fallback(net, monkeypatch):
    f = fetch(MN.Forex)
    p = {x["pair"]: x["rate"] for x in f.value["pairs"]}
    raw = json.loads((FX / "frankfurter.json").read_bytes())["rates"]
    assert abs(p["EUR/USD"] - 1 / raw["EUR"]) < 1e-9 and p["USD/JPY"] == raw["JPY"]
    assert f.value["date"] == "2026-10-02" and "base=USD&symbols=EUR,GBP,JPY,CAD,MXN" in net.calls[0][0]
    monkeypatch.setattr(LF, "HTTP", down(net, "frankfurter.dev"))
    assert fetch(MN.Forex).value["via"] == "frankfurter.app"


def _ts(s):
    d = dt.datetime.strptime(s, "%Y-%m-%d %H:%M")
    return MN._pt_ts(d.date(), d.hour, d.minute)


@pytest.mark.parametrize("when,state,nxt", [
    ("2026-10-02 06:00", "pre", "2026-10-02 06:30"), ("2026-10-02 06:30", "open", "2026-10-02 13:00"),
    ("2026-10-02 13:30", "after", "2026-10-05 06:30"), ("2026-10-03 10:00", "closed", "2026-10-05 06:30"),
    ("2026-11-26 08:00", "closed", "2026-11-27 06:30"), ("2026-11-27 09:59", "open", "2026-11-27 10:00"),
    ("2026-12-24 12:00", "after", "2026-12-28 06:30"), ("2027-03-26 08:00", "closed", "2027-03-29 06:30"),
    ("2026-03-09 06:31", "open", "2026-03-09 13:00"),        # first weekday after DST starts
])
def test_nyse_hours_holidays_early_closes(when, state, nxt):
    st = MN.market_state(_ts(when))
    assert st["state"] == state and st["next"] == _ts(nxt)
    if when.startswith("2026-11-26"):
        assert st["holiday"] == "THANKSGIV"


def test_market_polls_fast_only_while_us_markets_trade(monkeypatch):
    f = MN.Stocks()
    monkeypatch.setattr(MN, "us_market_active", lambda ts=None: True)
    assert f.poll_s == 120
    monkeypatch.setattr(MN, "us_market_active", lambda ts=None: False)
    assert f.poll_s == 1800 and MN.Commodities().poll_s == 1800
    assert MN.Crypto.poll_s >= 120 and MN.Forex.poll_s >= 3600


def test_market_feed_never_refetches_before_due(net):
    f = fetch(MN.Indexes)
    for _ in range(5):
        assert asyncio.run(f.refresh()) is False
    assert len(net.calls) == 1


# ------------------------------------------------------------------ news
@pytest.mark.parametrize("cls,tag,host", [
    (MN.NprNews, "NPR", "feeds.npr.org"), (MN.WorldNews, "BBC", "feeds.bbci.co.uk"),
    (MN.OregonNews, "OPB", "www.opb.org"), (MN.TechNews, "ARS", "feeds.arstechnica.com"),
    (MN.ArrlNews, "ARRL", "www.arrl.org"), (MN.RedditHam, "R/HAM", "www.reddit.com"),
])
def test_news_feeds_parse_newest_first(cls, tag, host, net):
    f = fetch(cls)
    it = f.value["items"]
    assert f.value["tag"] == tag and f.value["via"] == host and 1 <= len(it) <= cls.limit
    assert all(i["title"] and i["title"] == i["title"].upper() for i in it)
    ats = [i["at"] for i in it]
    assert all(ats) and ats == sorted(ats, reverse=True)
    assert f.requests == 1


def test_news_fallback_and_url_override(net, monkeypatch):
    monkeypatch.setattr(LF, "HTTP", down(net, "opb.org"))
    f = fetch(MN.OregonNews)
    assert f.value["tag"] == "OREGONLIVE" and f.value["via"] == "www.oregonlive.com"
    S.save({"livedata_rss_tech": "https://www.theverge.com/rss/index.xml"})
    t = fetch(MN.TechNews)
    assert t.value["tag"] == "VERGE" and net.calls[-1][0].startswith("https://www.theverge.com/")


def test_clean_headlines_for_the_3x5_font():
    assert MN.clean("Trump\u2019s \u201cdeal\u201d &amp; Caf\u00e9 \u2014 <b>now</b> $5 https://x.co/a") == "TRUMP'S 'DEAL' & CAFE - NOW 5"
    from orcpixels.tinyfont import _G
    assert all(c in _G for c in MN.clean("\u65e5\u672c ~{ok}| \U0001F600 50\u20ac"))


def test_hacker_news_top_then_hnrss_fallback(net, monkeypatch):
    f = fetch(MN.HackerNews)
    assert len(f.value["items"]) == 5 and f.requests == 6 and f.value["items"][0]["score"] == 142
    monkeypatch.setattr(LF, "HTTP", down(net, "firebaseio.com"))
    net.override = {"hnrss.org": "ars.xml"}
    f2 = fetch(MN.HackerNews)
    assert f2.value["via"] == "hnrss" and f2.value["items"]


def test_rate_limited_news_backs_off_a_full_interval(net, monkeypatch):
    import urllib.error

    def h(url, headers, timeout):
        net.calls.append((url, headers))
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
    monkeypatch.setattr(LF, "HTTP", h)
    f = MN.RedditHam()
    asyncio.run(f.refresh())
    assert "429" in f.error and len(net.calls) == 1                  # no second Reddit URL
    assert f.retry_s() == f.poll_s == 1800 and not f.due(time.time() + 600)


def test_news_polite_intervals():
    for cls in (MN.NprNews, MN.WorldNews, MN.HackerNews, MN.OregonNews, MN.TechNews, MN.ArrlNews, MN.RedditHam):
        assert cls.poll_s >= 600, cls
    assert MN.OregonNews.poll_s >= 1800 and MN.RedditHam.poll_s >= 1800


# ------------------------------------------------------------------ X (optional)
def test_x_without_token_never_touches_network(net):
    f = MN.XFeed()
    assert f.needs_key
    asyncio.run(f.refresh())
    assert net.calls == [] and f.value is None and "NeedsToken" in f.error
    img = SC.render("x_feed", f, 0)
    assert img.size == (64, 16) and img.getbbox()


def test_x_with_env_token_uses_bearer(net, monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token-not-real")
    seen = []

    def h(url, headers, timeout):
        seen.append((url, headers.get("Authorization")))
        if "/users/by/username/" in url:
            return b'{"data":{"id":"123","username":"NWSPortland"}}'
        return b'{"data":[{"text":"Wind advisory \\u2014 gusts to 50 mph","created_at":"2026-10-02T20:00:00Z"}]}'
    monkeypatch.setattr(LF, "HTTP", h)
    f = fetch(MN.XFeed)
    assert not f.needs_key and f.value["items"][0]["title"] == "WIND ADVISORY - GUSTS TO 50 MPH"
    assert all(a == "Bearer test-token-not-real" for _, a in seen) and len(seen) == 2
    assert asyncio.run(f.refresh(force=True)) and len(seen) == 3              # user id cached


# ------------------------------------------------------------------ screens
def test_marquee_is_a_seamless_constant_step_scroll(net):
    f = fetch(MN.Stocks)
    now = time.time()
    n = SC.loop_len("stocks", f)
    fr = [SC.render("stocks", f, i, now) for i in range(n + 1)]
    assert fr[n].tobytes() == fr[0].tobytes()
    s = S2.strip(SC.SCREENS["stocks"].segs(f.value, now))
    assert s.width % S2.STEP == 0 and s.width // S2.STEP == n
    for a, b in zip(fr, fr[1:]):                    # every step (incl. the seam) = shift by STEP
        assert a.crop((S2.STEP, 0, 64, 16)).tobytes() == b.crop((0, 0, 64 - S2.STEP, 16)).tobytes()
    assert loop_metric_frames(fr[:n])["ok"]


def test_marquee_length_does_not_depend_on_market_state(net):
    f = fetch(MN.Stocks)
    lens = {S2.strip(SC.SCREENS["stocks"].segs(f.value, _ts(t))).width
            for t in ("2026-10-02 06:00", "2026-10-02 07:00", "2026-10-02 14:00", "2026-10-03 12:00")}
    assert len(lens) == 1


def test_headline_pages_cover_every_word(net):
    f = fetch(MN.NprNews)
    scr = SC.SCREENS["news_npr"]
    ents = scr.entries(f.value)
    first = f.value["items"][0]["title"]
    assert " ".join(scr._lines(first)) == first                 # wrapped, nothing lost
    assert sum(1 for e in ents if e[0] == 0) == -(-len(scr._lines(first)) // 2)
    assert SC.loop_len("news_npr", f) == scr.per * len(ents)


def test_market_clock_renders_every_state():
    f = MN.MarketClock()
    asyncio.run(f.refresh())
    for t in ("2026-10-02 06:00", "2026-10-02 08:00", "2026-10-02 13:30", "2026-10-03 12:00", "2026-11-26 08:00"):
        fr = [SC.render("market_clock", f, i, _ts(t)) for i in range(SC.loop_len("market_clock", f))]
        assert all(im.getbbox() for im in fr) and loop_metric_frames(fr)["ok"]
    assert S2.countdown(2 * 86400 + 7 * 3600) == "2D07" and S2.countdown(3725) == "1:02" and S2.countdown(65) == "1:05"


def test_new_screens_failure_frames(net):
    net.down = True
    for k in ("stocks", "news_npr", "crypto", "hn"):
        f = SC.SCREENS[k].feed()
        asyncio.run(f.refresh())
        assert f.value is None and f.error
        assert SC.render(k, f, 0).getbbox()                               # NO DATA frame


def test_stale_market_value_keeps_last_quote_with_red_dot(net):
    f = fetch(MN.Indexes)
    f.error = "TimeoutError: timed out"
    img = SC.render("indexes", f, 0)
    assert img.getpixel((63, 0)) == SC.STALE


# ------------------------------------------------------------------ config
def test_validate_settings():
    assert MN.validate("livedata_stock_symbols", " aapl, brk-b ,^gspc,gc=f,AAPL") == "AAPL,BRK-B,^GSPC,GC=F"
    assert MN.validate("livedata_stock_symbols", "") == ""
    assert MN.validate("livedata_x_user", "@NWSPortland") == "NWSPortland"
    for k, v in (("livedata_stock_symbols", "AAPL;rm"), ("livedata_spark_symbol", "A,B"),
                 ("livedata_forex", "EURO"), ("livedata_rss_npr", "file:///etc/passwd"),
                 ("livedata_stock_symbols", ",".join(f"S{i}" for i in range(13))), ("nope", "x")):
        with pytest.raises(ValueError):
            MN.validate(k, v)


@pytest.fixture
def app(tmp_path, monkeypatch):
    from orcpixels import playlist as PL
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    from orcpixels.web import create_app
    return create_app(address=None)


def test_feed_config_route(app):
    from tests.test_routes_http import asgi
    from orcpixels import place as PLACE
    live = app.state.livedata_live
    st, c = asgi(app, "GET", "/api/livedata/feed-config")
    assert st == 200 and {f["key"] for f in c["fields"]} == set(MN.CONFIG) | set(FT.CONFIG) | set(PLACE.CONFIG)
    assert c["secrets"] == {"livedata_x_bearer_token": False}
    asyncio.run(live.feeds["stocks"].refresh())
    assert live.feeds["stocks"].attempt_at
    st, r = asgi(app, "POST", "/api/livedata/feed-config",
                 {"values": {"livedata_stock_symbols": "amd, intc", "livedata_x_bearer_token": "fake-not-a-token"}})
    assert st == 200 and S.load()["livedata_stock_symbols"] == "AMD,INTC"
    assert live.feeds["stocks"].attempt_at is None                  # due again: refetch once
    assert r["secrets"]["livedata_x_bearer_token"] is True and "fake-not-a-token" not in json.dumps(r)
    assert live.feeds["stocks"].symbols() == ["AMD", "INTC"]
    st, _ = asgi(app, "POST", "/api/livedata/feed-config", {"values": {"livedata_rss_npr": "ftp://x"}})
    assert st == 400
    st, r = asgi(app, "POST", "/api/livedata/feed-config", {"values": {"livedata_stock_symbols": ""}})
    assert live.feeds["stocks"].symbols() == MN.CONFIG["livedata_stock_symbols"][0].split(",")


def test_rotation_resumes_each_screen_where_it_left_off():
    class Sign:
        pass
    live = LD.DataLive(Sign(), persist=False)
    live.set_selection(["news_npr", "stocks"], rotate=True)
    live._k = 17
    live._switch("stocks")
    assert live._k == 0
    live._k = 5
    live._switch("news_npr")
    assert live._k == 17


def test_place_settings_validate_and_unset_place_means_not_configured(monkeypatch):
    from orcpixels import place as PLACE
    assert PLACE.validate("home_lat", "45.5") == 45.5 and PLACE.validate("home_label", "pdx") == "PDX"
    assert PLACE.validate("timezone", "US-Pacific") == "us-pacific" and PLACE.validate("home_lon", "") == ""
    for k, bad in (("home_lat", "95"), ("home_lon", "east"), ("timezone", "Mars/Base"), ("home_label", "a;b")):
        with pytest.raises(ValueError):
            PLACE.validate(k, bad)
    monkeypatch.setattr(PLACE, "OVERRIDE", {})
    monkeypatch.setattr(PLACE, "get", lambda k: PLACE.DEFAULTS.get(k))
    assert PLACE.home() is None
    with pytest.raises(PLACE.NotConfigured):
        LF.Tides().url
    with pytest.raises(PLACE.NotConfigured):
        LF.AirQuality().url
    assert PLACE.home_tag() == "HOME" and LF.HOME is None


def test_timezone_rules():
    from orcpixels import place as PLACE
    jul, dec = LF.parse_iso("2026-07-01T12:00:00Z"), LF.parse_iso("2026-12-01T12:00:00Z")
    assert PLACE.utc_offset_h(jul) == -7 and PLACE.utc_offset_h(dec) == -8 and PLACE.tz_label() == "PT"
    import os
    os.environ["ORCPIXELS_TZ"] = "us-eastern"
    try:
        assert PLACE.utc_offset_h(jul) == -4 and PLACE.utc_offset_h(dec) == -5 and PLACE.tz_label() == "ET"
    finally:
        os.environ["ORCPIXELS_TZ"] = "us-pacific"
