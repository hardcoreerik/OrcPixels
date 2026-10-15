"""World weather from Open-Meteo (open-meteo.com: free, keyless, non-commercial use,
< 10,000 calls/day). Works anywhere; the NWS screens stay the US source. One request feeds
all four screens (shared 10 min cache), polled every 15 min. Location = the home location
(or FT8 home grid); units follow the country (US: F / mph, elsewhere C / km/h)."""
from __future__ import annotations

import datetime as dt

from .. import categories as CAT
from .. import live_adaptive as A
from .. import live_templates as T
from .. import place as PLACE
from ..sources import livefeeds as LF

COLOR = (120, 200, 255)
URL = "https://api.open-meteo.com/v1/forecast"
SKY, SUN, RAINC, SNOWC = (120, 200, 255), (255, 200, 40), (60, 140, 255), (235, 240, 255)
PLACE.PLACE_FEEDS.update({"wxw_now", "wxw_hourly", "wxw_week", "wxw_uv"})

# WMO weather code -> (text, icon condition used by livedata_screens.wx_icon)
WMO = {0: ("CLEAR", "clear"), 1: ("MOSTLY CLR", "pcloudy"), 2: ("PT CLOUDY", "pcloudy"), 3: ("CLOUDY", "cloudy"),
       45: ("FOG", "fog"), 48: ("FOG", "fog"), 51: ("DRIZZLE", "rain"), 53: ("DRIZZLE", "rain"), 55: ("DRIZZLE", "rain"),
       56: ("FRZ DRZL", "rain"), 57: ("FRZ DRZL", "rain"), 61: ("LT RAIN", "rain"), 63: ("RAIN", "rain"),
       65: ("HVY RAIN", "rain"), 66: ("FRZ RAIN", "rain"), 67: ("FRZ RAIN", "rain"), 71: ("LT SNOW", "snow"),
       73: ("SNOW", "snow"), 75: ("HVY SNOW", "snow"), 77: ("SNOW GRN", "snow"), 80: ("SHOWERS", "rain"),
       81: ("SHOWERS", "rain"), 82: ("HVY SHWR", "rain"), 85: ("SNOW SHWR", "snow"), 86: ("SNOW SHWR", "snow"),
       95: ("T-STORM", "storm"), 96: ("T-STORM", "storm"), 99: ("T-STORM", "storm")}


def wmo(code):
    try:
        return WMO.get(int(code), ("?", "cloudy"))
    except (TypeError, ValueError):
        return ("?", "cloudy")


def metric() -> bool:
    return T.country() != "US"


def temp_col(t, metric_):
    f = t * 9 / 5 + 32 if metric_ else t
    for lim, c in ((32, (180, 220, 255)), (50, (120, 200, 255)), (65, (140, 245, 160)), (80, (255, 200, 40)), (95, (255, 120, 40))):
        if f < lim:
            return c
    return (255, 50, 40)


class OpenMeteoFeed(LF.Feed):
    source, poll_s = "Open-Meteo forecast API (keyless)", 900.0
    url = URL + "?latitude=<home>&longitude=<home>&current=...&hourly=...&daily=..."

    def fetch(self) -> dict:
        lat, lon = T.home_latlon()
        m = metric()
        u = (f"{URL}?latitude={lat:.3f}&longitude={lon:.3f}"
             "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,"
             "wind_direction_10m,uv_index,is_day"
             "&hourly=temperature_2m,precipitation_probability,weather_code"
             "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset,uv_index_max"
             + ("" if m else "&temperature_unit=fahrenheit&wind_speed_unit=mph")
             + "&timezone=auto&forecast_days=7&forecast_hours=24")
        d = T.cached(self, u, 600.0)
        cur = d.get("current") or {}
        if cur.get("temperature_2m") is None:
            raise ValueError("no current conditions")
        hr = d.get("hourly") or {}
        hours = [{"t": t[11:16], "temp": tp, "pop": pp or 0, "code": c}
                 for t, tp, pp, c in zip(hr.get("time") or [], hr.get("temperature_2m") or [],
                                         hr.get("precipitation_probability") or [], hr.get("weather_code") or [])
                 if tp is not None]
        dy = d.get("daily") or {}
        days = [{"date": t, "code": c, "hi": hi, "lo": lo, "pop": pp or 0, "uv": uv}
                for t, c, hi, lo, pp, uv in zip(dy.get("time") or [], dy.get("weather_code") or [],
                                                dy.get("temperature_2m_max") or [], dy.get("temperature_2m_min") or [],
                                                dy.get("precipitation_probability_max") or [], dy.get("uv_index_max") or [])]
        return {"cur": cur, "hours": hours, "days": days, "metric": m, "tag": PLACE.home_tag(),
                "sunrise": (dy.get("sunrise") or [""])[0][11:16], "sunset": (dy.get("sunset") or [""])[0][11:16]}


class NowFeed(OpenMeteoFeed):
    key, title = "wxw_now", "World weather: now (Open-Meteo)"


class HourlyFeed(OpenMeteoFeed):
    key, title = "wxw_hourly", "World weather: next 12 hours"


class WeekFeed(OpenMeteoFeed):
    key, title = "wxw_week", "World weather: 7 days"


class UvFeed(OpenMeteoFeed):
    key, title = "wxw_uv", "UV index"


def _icon(code, night):
    cond = wmo(code)[1]

    def fn(img, x, y, k):
        from .. import livedata_screens as LS
        LS.wx_icon(img, x, y, cond, night, k)
    return fn


class WxNow(T.ValueScreen):
    key, short, feed, category, color = "wxw_now", "WEATHER", NowFeed, "weather", COLOR

    def value(self, v, now):
        c, m = v["cur"], v["metric"]
        t = c["temperature_2m"]
        txt = wmo(c.get("weather_code"))[0]
        wind = f"{LF.compass(c.get('wind_direction_10m'))} {T.num(c.get('wind_speed_10m'))}{'KMH' if m else 'MPH'}"
        return {"big": f"{t:.0f}", "unit": "C" if m else "F", "label": v.get("tag") or "HOME", "sub": txt,
                "col": temp_col(t, m), "icon": _icon(c.get("weather_code"), not c.get("is_day", 1)),
                "right": txt[:9],
                "extras": [("FEELS", f"{c.get('apparent_temperature', t):.0f}" + ("C" if m else "F"), None),
                           ("WIND", wind, None), ("HUMID", f"{c.get('relative_humidity_2m', 0):.0f}%", None),
                           ("UV", f"{c.get('uv_index') or 0:.0f}", None)]}


class WxHourly(T.BarsScreen):
    key, short, feed, category, color = "wxw_hourly", "NEXT 12H", HourlyFeed, "weather", COLOR

    def bars(self, v):
        hs = (v or {}).get("hours", [])[:12]
        m = (v or {}).get("metric")
        bars = [(h["t"][:2], h["temp"], RAINC if h["pop"] >= 50 else temp_col(h["temp"], m)) for h in hs]
        rain = max((h["pop"] for h in hs), default=0)
        return ("NEXT 12H", bars, f"RAIN {rain}%" if rain else "DRY")

    def fmt(self, val):
        return f"{val:.0f}"

    def floor(self, vals):
        return min(vals) - 2

    def strip_parts(self, v):
        hs = (v or {}).get("hours", [])[:12]
        out = [("NEXT 12H", self.color)]
        for h in hs[::2]:
            out += [(h["t"][:2] + "H", GREY), (f"{h['temp']:.0f}", temp_col(h["temp"], v.get("metric"))),
                    (f"{h['pop']}%" if h["pop"] else "", RAINC)]
        return out


GREY = A.GREY


class WxWeek(A.ListScreen):
    key, short, feed, category, color, tag, per = "wxw_week", "7 DAYS", WeekFeed, "weather", COLOR, "7 DAY", 8

    def items(self, v):
        out = []
        for d in (v or {}).get("days") or []:
            try:
                day = dt.date.fromisoformat(d["date"]).strftime("%a").upper()
            except ValueError:
                day = "?"
            out.append((f"{day} {wmo(d['code'])[0]}", f"RAIN {d['pop']}%" if d["pop"] else "DRY",
                        f"{d['hi']:.0f}/{d['lo']:.0f}"))
        return out

    def per_page(self, ctx):
        if ctx.h < 24:
            return 1
        return max(1, (ctx.h - (5 * A.scale_for(ctx) + 5)) // (7 * A.scale_for(ctx)))

    def row_h(self, ctx):
        return 7

    def draw_ctx(self, ctx, img, v, k, now):
        if ctx.h < 24 or A.is_strip(ctx):
            return super().draw_ctx(ctx, img, v, k, now)
        items = self.items(v)
        days = v.get("days") or []
        sc = A.scale_for(ctx)
        pp = self.per_page(ctx)
        pages = self.pages_at(v, ctx)
        p = A.page_of(k, pages, self.per)
        y = A.header(img, ctx, f"{v.get('tag') or ''} 7 DAYS".strip(), self.color, f"{p + 1}/{pages}" if pages > 1 else "")
        lo = min(d["lo"] for d in days)
        hi = max(d["hi"] for d in days)
        for i, d in enumerate(days[p * pp:(p + 1) * pp]):
            day = items[p * pp + i][0].split()[0]
            A.T(img, 1, y, day[:2], A.CREAM, sc)
            x0, x1 = 10 * sc, ctx.w - 2
            span = (hi - lo) or 1
            a = x0 + round((d["lo"] - lo) / span * (x1 - x0 - 8 * sc))
            b = x0 + round((d["hi"] - lo) / span * (x1 - x0 - 8 * sc))
            from PIL import ImageDraw
            ImageDraw.Draw(img).rectangle((a, y + 1, max(a + 1, b), y + 3 * sc), fill=temp_col(d["hi"], v["metric"]))
            if d["pop"] >= 30:
                T.put(img, b + 2, y + 1, RAINC)
            if b + 12 * sc < ctx.w:
                A.RT(img, ctx.w - 2, y, f"{d['hi']:.0f}", A.CREAM, sc)
            y += 7 * sc


UV_COL = ((3, (60, 225, 90), "LOW"), (6, (240, 220, 40), "MODERATE"), (8, (255, 140, 20), "HIGH"),
          (11, (255, 50, 40), "VERY HIGH"), (99, (190, 60, 220), "EXTREME"))


def uv_band(u):
    for lim, c, n in UV_COL:
        if u < lim:
            return c, n
    return UV_COL[-1][1], UV_COL[-1][2]


class WxUv(T.ValueScreen):
    key, short, feed, category, color = "wxw_uv", "UV INDEX", UvFeed, "weather", SUN

    def value(self, v, now):
        u = float(v["cur"].get("uv_index") or 0)
        mx = float((v.get("days") or [{}])[0].get("uv") or 0)
        c, name = uv_band(max(u, 0))
        cm, nm = uv_band(mx)
        return {"big": f"{u:.0f}", "label": "UV NOW", "sub": name, "col": c, "gauge": min(1.0, u / 11),
                "right": f"MAX {mx:.0f}",
                "extras": [("TODAY MAX", f"{mx:.0f} {nm}", cm), ("SUNRISE", v.get("sunrise") or "?", None),
                           ("SUNSET", v.get("sunset") or "?", None)]}


SCREENS = [WxNow(), WxHourly(), WxWeek(), WxUv()]
for _s in SCREENS:
    CAT.assign(_s.key, "weather")
