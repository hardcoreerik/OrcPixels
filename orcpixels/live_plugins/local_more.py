"""Time & Local additions (2026-10-14), keyless, zero setup:

  Nager.Date (date.nager.at): upcoming public holidays for your country (from the home location)
  CityBikes (api.citybik.es v2): the bike-share network nearest your home and its closest stations
"""
from __future__ import annotations

import datetime as dt
import time

from .. import categories as CAT
from .. import live_adaptive as A
from .. import live_templates as T
from .. import place as PLACE
from ..sources import livefeeds as LF

COLOR = (90, 170, 255)
PLACE.PLACE_FEEDS.update({"holidays", "bikes"})


class HolidayFeed(LF.Feed):
    key, title, source, poll_s = "holidays", "Next public holidays", "Nager.Date (keyless)", 12 * 3600.0
    url = "https://date.nager.at/api/v3/NextPublicHolidays/<country>"

    def fetch(self) -> dict:
        cc = T.country()
        d = T.cached(self, f"https://date.nager.at/api/v3/NextPublicHolidays/{cc}", 6 * 3600.0)
        rows = [{"date": h["date"], "name": A.clean(h.get("name")).upper()} for h in d or [] if h.get("global", True)]
        if not rows:
            raise ValueError("no holidays")
        return {"rows": rows[:10], "cc": cc}


def days_to(date: str, now: float) -> int:
    today = LF.pt(now).date()
    return (dt.date.fromisoformat(date) - today).days


class Holidays(A.ListScreen):
    key, short, feed, category, color, tag = "holidays", "HOLIDAYS", HolidayFeed, "local", COLOR, "NEXT"

    def head_right(self, v):
        return v.get("cc", "")

    def items(self, v):
        now = time.time()
        out = []
        for h in (v or {}).get("rows", []):
            n = days_to(h["date"], now)
            if n < 0:
                continue
            out.append((h["name"], dt.date.fromisoformat(h["date"]).strftime("%a %b %d").upper(),
                        "TODAY" if n == 0 else f"{n}D"))
        return out


class BikesFeed(LF.Feed):
    key, title, source, poll_s = "bikes", "Bike share near home", "CityBikes API (keyless)", 300.0
    url = "https://api.citybik.es/v2/networks/<nearest>"

    def fetch(self) -> dict:
        home = T.home_latlon()
        nets = T.cached(self, "https://api.citybik.es/v2/networks?fields=id,name,location", 7 * 86400.0)
        best = min(nets.get("networks") or [], key=lambda n: T.km(home, (n["location"]["latitude"], n["location"]["longitude"])), default=None)
        if not best:
            raise ValueError("no networks")
        far = T.km(home, (best["location"]["latitude"], best["location"]["longitude"]))
        if far > 60:
            raise PLACE.NotConfigured(f"no bike share within 60 km (nearest {A.clean(best['location'].get('city'))})")
        net = T.cached(self, f"https://api.citybik.es/v2/networks/{best['id']}?fields=id,name,stations", 240.0)["network"]
        st = []
        for s in net.get("stations") or []:
            st.append({"name": A.clean(s.get("name")).upper(), "bikes": s.get("free_bikes") or 0,
                       "docks": s.get("empty_slots") or 0, "ebikes": (s.get("extra") or {}).get("ebikes"),
                       "km": T.km(home, (s["latitude"], s["longitude"]))})
        st.sort(key=lambda s: s["km"])
        return {"net": A.clean(net.get("name") or best.get("name")).upper(), "rows": st[:8],
                "total": sum(s.get("free_bikes") or 0 for s in net.get("stations") or [])}


class Bikes(A.ListScreen):
    key, short, feed, category, color, tag = "bikes", "BIKES", BikesFeed, "local", COLOR, "BIKES"

    def head_right(self, v):
        return f"{v.get('total', 0)} FREE"

    def items(self, v):
        return [(s["name"], f"{s['docks']} DOCKS {s['km']:.1f}KM", f"{s['bikes']} BIKE") for s in (v or {}).get("rows", [])]


SCREENS = [Holidays(), Bikes()]
for _s in SCREENS:
    CAT.assign(_s.key, "local")
