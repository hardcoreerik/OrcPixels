"""Milestone 2: category registry, Sports (ESPN) + Entertainment (TVMaze, iTunes, Wikipedia)
screens with mocked fetches: parsing, caching, fallbacks, seamless loops at the native
(64x16, 64x64) and adaptive (16x16, 64x32, 256x16, 96x24) sizes."""
import asyncio
import time

import pytest

from orcpixels import categories as CAT
from orcpixels import live_adaptive as A
from orcpixels import livedata_screens as SC
from orcpixels import place as PLACE
from orcpixels.live_plugins import entertainment as ENT
from orcpixels.live_plugins import sports as SP
from orcpixels.loopmetric import loop_metric_frames
from orcpixels.sources import livefeeds as LF
from tests.test_livedata import FX, ROUTES

SIZES = [(64, 16), (64, 64), (16, 16), (64, 32), (256, 16), (96, 24)]
NEW = [k for k, s in SC.SCREENS.items() if getattr(s, "adaptive", False)]
# fixture day: 2026-10-10, 20:30 PT
T_FX = 1791689400.0


class Net:
    def __init__(self):
        self.calls, self.down = [], False

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if self.down:
            raise TimeoutError("timed out")
        for k, f in ROUTES.items():
            if k in url:
                return (FX / f).read_bytes()
        raise AssertionError("unexpected URL " + url)


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(LF, "HTTP", n)
    SP._CACHE.clear()
    ENT._CACHE.clear()
    monkeypatch.setattr(time, "time", lambda: T_FX)
    return n


def fetched(key):
    f = SC.SCREENS[key].feed()
    asyncio.run(f.refresh(force=True))
    return f


def test_every_screen_has_a_category():
    cats = {k: CAT.of(k, s) for k, s in SC.SCREENS.items()}
    assert "other" not in cats.values()
    assert {cats[k] for k in ("wx_now", "forecast", "alerts", "aqi")} == {"weather"}
    assert {cats[k] for k in ("spacewx", "xray", "iss", "quake")} == {"space"}
    assert cats["hf_bands"] == "ham" and all(cats[k] == "ham" for k in cats if k.startswith("ft8_"))
    assert cats["github"] == "apps" and cats["clock"] == "local" and cats["stocks"] == "markets"
    order = [c.id for c in CAT.ordered()]
    assert order[:9] == ["apps", "weather", "markets", "news", "space", "ham", "sports", "entertainment", "local"]
    js = CAT.as_json(SC.SCREENS)
    assert sum(len(c["screens"]) for c in js) == len(SC.SCREENS)
    assert any(l["title"] == "OrcSDR Live" for l in js[0]["links"])


def test_new_screen_counts():
    sports = [k for k in NEW if CAT.of(k) == "sports"]
    ent = [k for k in NEW if CAT.of(k) == "entertainment"]
    assert len(sports) >= 8 and len(ent) >= 8
    assert {"sports_nfl", "sports_nba", "sports_mlb", "sports_nhl", "sports_mls", "sports_ncaaf", "sports_ncaab",
            "sports_live", "sports_fav"} <= set(sports)
    assert {"tv_now", "tv_tonight", "tv_streaming", "top_movies", "wiki_trending"} <= set(ent)


def test_registry_is_cheap_to_extend():
    CAT.category("zz_test", "Test category", 5000)
    CAT.assign("zz_screen", "zz_test")
    assert CAT.of("zz_screen") == "zz_test"
    CAT.CATEGORIES.pop("zz_test")
    CAT.ASSIGN.pop("zz_screen")


def test_broken_plugin_does_not_break_live(monkeypatch):
    from orcpixels import live_plugins as P
    monkeypatch.setattr(P, "BUILTIN", ("does_not_exist",))
    assert P.load({}) == []


def test_espn_parse_states(net):
    v = fetched("sports_nfl").value
    g = v["games"]
    post = [x for x in g if x["state"] == "post"][0]
    assert {post["a"], post["h"]} == {"TB", "DAL"} and post["detail"] == "FINAL" and post["as"] == "24"
    pre = [x for x in g if x["state"] == "pre"][0]
    assert pre["as"] == "" and ("A" in pre["detail"] or "P" in pre["detail"])
    nhl = fetched("sports_nhl").value
    live = [x for x in nhl["games"] if x["state"] == "in"][0]
    assert live["detail"] == "1ST 6:59" and nhl["live"] >= 1
    assert nhl["games"][0]["state"] == "in"                         # live games first


def test_polling_follows_live_games(net):
    assert fetched("sports_nhl").poll_s == 120.0                     # game in progress
    assert fetched("sports_wnba").poll_s == 900.0


def test_shared_cache_no_duplicate_requests(net):
    fetched("sports_nfl")
    n = len(net.calls)
    fetched("sports_live")                                            # 9 leagues, NFL already cached
    fetched("sports_fav")
    assert len(net.calls) - n == len(SP.LEAGUES) - 1


def test_favourites(net, monkeypatch):
    v = fetched("sports_fav").value
    assert all("DAL" in (g["a"], g["h"]) or (g["league"] == "NHL" and "SEA" in (g["a"], g["h"])) for g in v["games"])
    monkeypatch.setitem(PLACE.OVERRIDE, "sports_favorites", "")
    f = fetched("sports_fav")
    assert f.value is None and "sports_favorites" in f.error
    img = SC.render("sports_fav", f, 0, T_FX, size=(64, 64))
    assert img.getbbox()


def test_tv_feeds(net):
    now = fetched("tv_now").value
    assert all(e["start"] <= T_FX < e["end"] and not e["web"] for e in now["on"])
    stream = SC.SCREENS["tv_streaming"].items(fetched("tv_streaming").value)
    assert stream and all(m.isascii() for m, _s, _r in stream)
    tonight = SC.SCREENS["tv_tonight"].items(fetched("tv_tonight").value)
    assert tonight and all(r.endswith("P") for _m, _s, r in tonight)
    fav = fetched("tv_favorites").value["shows"]
    assert fav[0]["show"].upper() == "SEVERANCE"


def test_charts_and_wiki(net):
    songs = SC.SCREENS["top_songs"].items(fetched("top_songs").value)
    assert len(songs) == 10 and songs[0][0]
    wiki = fetched("wiki_trending").value["rows"]
    assert wiki and not any(r["name"].startswith(("Main Page", "Special:")) for r in wiki)
    assert A.short_num(1234567) == "1.2M" and A.clean("Pok\u00e9mon \u2013 \u0422\u0440\u043e\u0435") == "Pokemon"


def test_network_down_falls_back(net):
    net.down = True
    SP._CACHE.clear()
    ENT._CACHE.clear()
    for k in NEW:
        f = fetched(k)
        assert f.value is None and f.error
        for size in ((64, 16), (64, 64)):
            assert SC.render(k, f, 0, T_FX, size=size).getbbox(), k
    net.down = False
    f = fetched("sports_nba")
    net.down = True
    SP._CACHE.clear()                                                 # (the 45 s share cache would hide it)
    asyncio.run(f.refresh(force=True))
    assert f.value is not None and f.error                            # last good value kept (stale)


@pytest.mark.parametrize("key", NEW)
def test_new_screens_render_and_loop_at_every_size(net, key):
    f = fetched(key)
    assert f.error is None, f.error
    for size in SIZES:
        n = SC.loop_len(key, f, size)
        fr = [SC.render(key, f, i, T_FX, size=size) for i in range(n)]
        assert all(im.size == size for im in fr)
        lit = sum(1 for p in fr[0].getdata() if sum(p) > 60)
        assert lit >= 12, (key, size, lit)
        if n > 1:
            assert loop_metric_frames(fr)["ok"], (key, size)
        assert SC.render(key, f, n, T_FX, size=size).tobytes() == fr[0].tobytes()
