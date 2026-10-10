"""Free live-data feeds for the Live Data screens (orcpixels/livedata.py).

Every feed is READ-ONLY HTTP GET (urllib in a worker thread), no API key needed
(GitHub takes an optional GITHUB_TOKEN env var for a higher rate limit). Each feed:
  * has its own poll interval (polite, per the provider's terms; see POLL notes),
  * never fetches before it is due, however often refresh() is called (preview,
    stream sampler and watchdog probes all go through the same cache),
  * backs off after a failure (30 s doubling, capped at the poll interval),
  * keeps the last good value: `stale` once a refresh failed or the value is older
    than stale_after; `usable` until max_stale (then Live Data falls back to the playlist).
HTTP is the module-level function `HTTP(url, headers, timeout) -> bytes` so tests can
monkeypatch it (tests/test_livedata.py never touches the network).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import http.client
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("orcpixels.livedata")

UA = "OrcPixels/1.0 (TheOrc.dev; Oregon Radio Company LLC; live data screens)"
HTTP_TIMEOUT_S = 8.0
from .. import place as PLACE  # noqa: E402  (home location, time zone, place-specific sources)


def __getattr__(name):          # LF.HOME / LF.HOME_NAME: read from settings each time
    if name == "HOME":
        return PLACE.home()
    if name == "HOME_NAME":
        return PLACE.label("home_label", "HOME")
    raise AttributeError(name)


def http_get(url: str, headers: dict | None = None, timeout: float = HTTP_TIMEOUT_S) -> bytes:
    """GET with a descriptive User-Agent, gzip, a hard timeout and ONE retry for a body
    cut off mid-transfer (seen from USGS). HTTP errors (403/429/5xx) are not retried."""
    h = {"User-Agent": UA, "Accept": "application/geo+json, application/json;q=0.9, */*;q=0.5",
         "Accept-Encoding": "gzip"}
    h.update(headers or {})
    for attempt in (1, 2):
        req = urllib.request.Request(url, method="GET", headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(4_000_000)
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    data = gzip.decompress(data)
                return data
        except (http.client.IncompleteRead, ConnectionResetError, EOFError):
            if attempt == 2:
                raise
            time.sleep(1.0)
    raise RuntimeError("unreachable")


HTTP = http_get          # tests replace this


# ------------------------------------------------------------------ Pacific time
def _nth_sunday(year: int, month: int, n: int) -> int:
    d = dt.date(year, month, 1)
    first = d + dt.timedelta(days=(6 - d.weekday()) % 7)
    return (first + dt.timedelta(days=7 * (n - 1))).day


def pt_offset_h(ts: float) -> int:
    """US Pacific: PDT (-7) from 2nd Sunday of March 02:00 PST to 1st Sunday of Nov 02:00 PDT.
    Computed here (the Windows venv has no tzdata for zoneinfo)."""
    y = dt.datetime.fromtimestamp(ts, dt.timezone.utc).year
    start = dt.datetime(y, 3, _nth_sunday(y, 3, 2), 10, tzinfo=dt.timezone.utc).timestamp()
    end = dt.datetime(y, 11, _nth_sunday(y, 11, 1), 9, tzinfo=dt.timezone.utc).timestamp()
    return -7 if start <= ts < end else -8


def pt(ts: float) -> dt.datetime:
    """Wall-clock time in the configured zone (settings 'timezone', default the OS zone).
    (Named pt for history: it was hard-wired to US Pacific.)"""
    return PLACE.local(ts)


def hm12(ts: float) -> str:
    """'4:43P' in the configured time zone."""
    d = pt(ts)
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d}{'A' if d.hour < 12 else 'P'}"


def parse_iso(s: str) -> float:
    s = s.strip().replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()


def ago(ts: float | None, now: float | None = None) -> str:
    if ts is None:
        return "?"
    s = max(0, (now or time.time()) - ts)
    if s < 90:
        return f"{int(s)}S"
    if s < 5400:
        return f"{int(s // 60)}M"
    if s < 172800:
        return f"{int(s // 3600)}H"
    return f"{int(s // 86400)}D"


COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(deg: float | None) -> str:
    if deg is None:
        return ""
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


# ------------------------------------------------------------------ feed base
class Feed:
    key = ""
    title = ""
    source = ""              # provider, shown in the UI / report
    url = ""                 # human-facing endpoint description
    poll_s = 600.0
    key_env: str | None = None      # optional API key env var (never required)
    local = False            # computed locally, no network

    def __init__(self):
        self.value: dict | None = None
        self.ok_at: float | None = None
        self.attempt_at: float | None = None
        self.error: str | None = None
        self.fails = 0
        self.fetches = 0
        self.requests = 0
        self._busy = False

    # politeness ------------------------------------------------------------
    @property
    def stale_after(self) -> float:
        return max(self.poll_s * 2.5, 180.0)

    @property
    def max_stale(self) -> float:
        return max(self.poll_s * 6, 3 * 3600.0)

    def retry_s(self) -> float:
        return min(self.poll_s, 30.0 * 2 ** max(0, self.fails - 1))

    def next_due(self) -> float:
        if self.attempt_at is None:
            return 0.0
        return self.attempt_at + (self.poll_s if self.error is None else self.retry_s())

    def due(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.next_due()

    # state -----------------------------------------------------------------
    def age(self, now: float | None = None) -> float | None:
        return None if self.ok_at is None else (now or time.time()) - self.ok_at

    @property
    def usable(self) -> bool:
        a = self.age()
        return self.value is not None and a is not None and a <= self.max_stale

    @property
    def stale(self) -> bool:
        a = self.age()
        return self.value is not None and (self.error is not None or (a is not None and a > self.stale_after))

    def state(self) -> dict:
        return {"key": self.key, "ok_at": self.ok_at, "attempt_at": self.attempt_at,
                "next_due": self.next_due() if self.attempt_at else None, "error": self.error,
                "fails": self.fails, "stale": self.stale, "usable": self.usable,
                "fetches": self.fetches, "requests": self.requests, "poll_s": self.poll_s}

    # fetching ----------------------------------------------------------------
    def get(self, url: str, headers: dict | None = None) -> bytes:
        self.requests += 1
        return HTTP(url, headers or {}, HTTP_TIMEOUT_S)

    def get_json(self, url: str, headers: dict | None = None):
        return json.loads(self.get(url, headers).decode("utf-8", "replace"))

    def fetch(self) -> dict:              # runs in a worker thread
        raise NotImplementedError

    async def refresh(self, force: bool = False) -> bool:
        """Fetch if due (or force). Returns True if a fetch was attempted."""
        if self._busy or (not force and not self.due()):
            return False
        self._busy = True
        self.attempt_at = time.time()
        try:
            v = await asyncio.wait_for(asyncio.to_thread(self.fetch), HTTP_TIMEOUT_S * 3 + 2)
            if not isinstance(v, dict):
                raise ValueError("feed returned no data")
            self.value, self.ok_at, self.error, self.fails = v, time.time(), None, 0
            self.fetches += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.fails += 1
            self.error = f"{type(e).__name__}: {e}"[:240]
            log.info("live data feed %s failed (#%d): %s", self.key, self.fails, self.error)
        finally:
            self._busy = False
        return True


def _setting(key: str):
    """Optional value from config/settings.json (keys are never required)."""
    try:
        from .. import settings as S
        return S.load().get(key) or None
    except Exception:  # noqa: BLE001
        return None


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


# =================================================================== the feeds
class NwsNow(Feed):
    key, title, source = "wx_now", "Weather now", "NWS api.weather.gov (nearest station, US only)"
    poll_s = 600.0           # stations report hourly (+specials); NWS asks for modest polling

    @property
    def url(self) -> str:  # type: ignore[override]
        st = str(PLACE.get("livedata_nws_station") or "").strip().upper() or "(nearest)"
        return f"https://api.weather.gov/stations/{st}/observations/latest"

    def fetch(self):
        url = f"https://api.weather.gov/stations/{nws_station(self)}/observations/latest"
        p = self.get_json(url)["properties"]
        c = _num((p.get("temperature") or {}).get("value"))
        ws = _num((p.get("windSpeed") or {}).get("value"))
        wg = _num((p.get("windGust") or {}).get("value"))
        pa = _num((p.get("barometricPressure") or {}).get("value"))
        icon = p.get("icon") or ""
        return {"temp_f": None if c is None else round(c * 9 / 5 + 32),
                "desc": (p.get("textDescription") or "").strip(),
                "wind_mph": None if ws is None else round(ws / 1.609344),
                "gust_mph": None if wg is None else round(wg / 1.609344),
                "wind_dir": compass(_num((p.get("windDirection") or {}).get("value"))),
                "rh": None if _num((p.get("relativeHumidity") or {}).get("value")) is None
                else round(p["relativeHumidity"]["value"]),
                "pressure_mb": None if pa is None else round(pa / 100),
                "night": "/night/" in icon, "at": parse_iso(p["timestamp"]) if p.get("timestamp") else None}


_POINTS: dict[tuple, dict] = {}


def nws_point(feed: "Feed") -> dict:
    """api.weather.gov /points for home (grid forecast URL, nearest stations, state); cached
    per location for the life of the process."""
    lat, lon = PLACE.require_home()
    k = (round(lat, 4), round(lon, 4))
    if k not in _POINTS:
        pr = feed.get_json(f"https://api.weather.gov/points/{k[0]},{k[1]}")["properties"]
        rel = ((pr.get("relativeLocation") or {}).get("properties") or {})
        _POINTS[k] = {"forecast": pr.get("forecast"), "stations": pr.get("observationStations"),
                      "state": rel.get("state") or "", "station": None}
    return _POINTS[k]


def nws_station(feed: "Feed") -> str:
    st = str(PLACE.get("livedata_nws_station") or "").strip().upper()
    if st:
        return st
    pt_ = nws_point(feed)
    if not pt_["station"]:
        feats = feed.get_json(pt_["stations"]).get("features") or []
        if not feats:
            raise ValueError("no NWS observation station near home")
        pt_["station"] = feats[0]["properties"]["stationIdentifier"]
    return pt_["station"]


class NwsForecast(Feed):
    key, title, source = "forecast", "Forecast", "NWS api.weather.gov (grid forecast, US only)"
    poll_s = 1800.0

    @property
    def url(self) -> str:  # type: ignore[override]
        h = PLACE.home()
        return f"https://api.weather.gov/points/{h[0]},{h[1]}" if h else "https://api.weather.gov/points/(set home)"

    def fetch(self):
        p = self.get_json(nws_point(self)["forecast"])["properties"]
        periods = [{"name": q["name"], "temp": q.get("temperature"), "unit": q.get("temperatureUnit", "F"),
                    "short": q.get("shortForecast", ""), "day": bool(q.get("isDaytime")),
                    "wind": f"{q.get('windDirection', '')} {q.get('windSpeed', '')}".strip()}
                   for q in p.get("periods", [])[:6]]
        if not periods:
            raise ValueError("forecast has no periods")
        return {"periods": periods, "updated": p.get("updateTime")}


class NwsAlerts(Feed):
    key, title, source = "alerts", "Weather alerts", "NWS api.weather.gov (active alerts for your state, US only)"
    poll_s = 300.0

    def area(self) -> str:
        a = str(PLACE.get("livedata_alerts_area") or "").strip().upper()
        return a or nws_point(self)["state"] or PLACE.require("livedata_alerts_area", "US state code, e.g. OR")

    @property
    def url(self) -> str:  # type: ignore[override]
        a = str(PLACE.get("livedata_alerts_area") or "").strip().upper() or "(home state)"
        return f"https://api.weather.gov/alerts/active?area={a}"

    def fetch(self):
        feats = self.get_json(f"https://api.weather.gov/alerts/active?area={self.area()}").get("features", [])
        order = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3}
        alerts = []
        for f in feats:
            p = f.get("properties") or {}
            exp = p.get("ends") or p.get("expires")
            alerts.append({"event": p.get("event", "Alert"), "severity": p.get("severity", "Unknown"),
                           "area": (p.get("areaDesc") or "").split(";")[0].strip(),
                           "ends": parse_iso(exp) if exp else None})
        alerts.sort(key=lambda a: order.get(a["severity"], 9))
        return {"alerts": alerts, "count": len(alerts)}


class AirQuality(Feed):
    key, title, source = "aqi", "Air quality", "Open-Meteo air-quality API (CAMS)"
    poll_s = 1800.0          # hourly model data; free tier is for non-commercial use

    @property
    def url(self) -> str:  # type: ignore[override]
        lat, lon = PLACE.require_home()
        return (f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}"
                "&current=us_aqi,pm2_5,pm10&timezone=auto")

    def fetch(self):
        c = self.get_json(self.url)["current"]
        if _num(c.get("us_aqi")) is None:
            raise ValueError("no us_aqi in response")
        return {"aqi": round(c["us_aqi"]), "pm25": _num(c.get("pm2_5")), "pm10": _num(c.get("pm10")),
                "time": c.get("time")}


class SpaceWx(Feed):
    key, title, source = "spacewx", "Kp index + solar wind", "NOAA SWPC (planetary K, RTSW summary)"
    url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    speed_url = "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json"
    mag_url = "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json"
    poll_s = 600.0

    def fetch(self):
        rows = self.get_json(self.url)
        kp = []
        for r in rows:
            if isinstance(r, dict):
                kp.append((r.get("time_tag"), float(r.get("Kp"))))
            elif isinstance(r, list) and r and r[0] != "time_tag":    # older array-of-arrays format
                kp.append((r[0], float(r[1])))
        if not kp:
            raise ValueError("no Kp values")
        out = {"kp": [v for _, v in kp[-8:]], "kp_time": kp[-1][0], "speed": None, "bz": None, "bt": None}
        try:
            out["speed"] = _num(self.get_json(self.speed_url)[0].get("proton_speed"))
            m = self.get_json(self.mag_url)[0]
            out["bz"], out["bt"] = _num(m.get("bz_gsm")), _num(m.get("bt"))
        except Exception as e:  # noqa: BLE001  (Kp alone is still worth showing)
            out["wind_error"] = f"{type(e).__name__}: {e}"[:120]
        return out


def xray_flux(cls: str) -> float | None:
    """'B1.9' -> 1.9e-7 W/m^2."""
    try:
        return float(cls[1:]) * {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}[cls[0].upper()]
    except (KeyError, ValueError, IndexError, TypeError):
        return None


class XRay(Feed):
    key, title, source = "xray", "Solar X-ray flares + F10.7", "NOAA SWPC (GOES X-ray flares, 10.7 cm flux)"
    url = "https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json"
    flux_url = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"
    poll_s = 600.0

    def __init__(self):
        super().__init__()
        self.history: list[tuple[float, float]] = []          # (t, flux) kept across polls

    def fetch(self):
        f = self.get_json(self.url)
        f = f[0] if isinstance(f, list) else f
        cur = f.get("current_class") or ""
        if xray_flux(cur) is None:
            raise ValueError(f"bad xray class {cur!r}")
        try:
            sfi = _num(self.get_json(self.flux_url)[0].get("flux"))
        except Exception:  # noqa: BLE001
            sfi = None
        self.history = (self.history + [(time.time(), xray_flux(cur))])[-24:]
        return {"current": cur, "max_class": f.get("max_class"),
                "max_time": parse_iso(f["max_time"]) if f.get("max_time") else None,
                "sfi": sfi, "history": [v for _, v in self.history]}


class HfBands(Feed):
    key, title, source = "hf_bands", "HF band conditions", "N0NBH hamqsl.com solarxml"
    url = "https://www.hamqsl.com/solarxml.php"
    poll_s = 3600.0          # N0NBH updates about hourly and asks people not to hammer it

    def fetch(self):
        root = ET.fromstring(self.get(self.url, {"Accept": "application/xml, text/xml"}))
        sd = root.find("solardata")
        if sd is None:
            raise ValueError("no solardata")
        t = lambda k: (sd.findtext(k) or "").strip()
        bands = {}
        for b in sd.iter("band"):
            bands.setdefault(b.get("name", "?"), {})[b.get("time", "?")] = (b.text or "").strip()
        if not bands:
            raise ValueError("no band conditions")
        return {"sfi": t("solarflux"), "sn": t("sunspots"), "a": t("aindex"), "k": t("kindex"),
                "xray": t("xray"), "geomag": t("geomagfield"), "noise": t("signalnoise"),
                "updated": t("updated"), "bands": bands}


# ------------------------------------------------------------- ISS propagator
MU, RE, J2 = 398600.4418, 6378.137, 1.08262668e-3


def tle_elements(j: dict) -> dict:
    return {"epoch": parse_iso(j["EPOCH"]), "n": float(j["MEAN_MOTION"]), "e": float(j["ECCENTRICITY"]),
            "i": math.radians(float(j["INCLINATION"])), "raan": math.radians(float(j["RA_OF_ASC_NODE"])),
            "argp": math.radians(float(j["ARG_OF_PERICENTER"])), "M": math.radians(float(j["MEAN_ANOMALY"])),
            "ndot": float(j.get("MEAN_MOTION_DOT") or 0.0)}


def sat_eci(el: dict, ts: float) -> tuple[float, float, float]:
    """Two-body + J2 secular drift (RAAN, perigee) + mean-motion decay. Good to about a
    minute of pass timing over a day or two for the ISS: an approximation, not SGP4."""
    t = ts - el["epoch"]
    n = el["n"] * 2 * math.pi / 86400.0
    a = (MU / n ** 2) ** (1 / 3)
    e, i = el["e"], el["i"]
    p = a * (1 - e * e)
    k = 1.5 * J2 * (RE / p) ** 2 * n
    raan = el["raan"] - k * math.cos(i) * t
    argp = el["argp"] + k * (2 - 2.5 * math.sin(i) ** 2) * t
    M = el["M"] + n * t + 2 * math.pi * el["ndot"] * (t / 86400.0) ** 2
    E = M
    for _ in range(8):
        E = E - (E - e * math.sin(E) - M) / (1 - e * math.cos(E))
    nu = 2 * math.atan2(math.sqrt(1 + e) * math.sin(E / 2), math.sqrt(1 - e) * math.cos(E / 2))
    r = a * (1 - e * math.cos(E))
    u = argp + nu
    co, so, ci, si = math.cos(raan), math.sin(raan), math.cos(i), math.sin(i)
    cu, su = math.cos(u), math.sin(u)
    return (r * (co * cu - so * su * ci), r * (so * cu + co * su * ci), r * su * si)


def gmst(ts: float) -> float:
    jd = ts / 86400.0 + 2440587.5
    d = jd - 2451545.0
    T = d / 36525.0
    g = 280.46061837 + 360.98564736629 * d + 0.000387933 * T * T - T ** 3 / 38710000.0
    return math.radians(g % 360.0)


def sat_ecef(el: dict, ts: float):
    x, y, z = sat_eci(el, ts)
    g = gmst(ts)
    c, s = math.cos(g), math.sin(g)
    return (c * x + s * y, -s * x + c * y, z)


def subpoint(el: dict, ts: float) -> tuple[float, float]:
    x, y, z = sat_ecef(el, ts)
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def elevation(el: dict, ts: float, lat: float, lon: float) -> float:
    sx, sy, sz = sat_ecef(el, ts)
    la, lo = math.radians(lat), math.radians(lon)
    up = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    dx, dy, dz = sx - RE * up[0], sy - RE * up[1], sz - RE * up[2]
    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
    return math.degrees(math.asin((dx * up[0] + dy * up[1] + dz * up[2]) / rng))


def next_pass(el: dict, t0: float, lat: float, lon: float, min_el: float = 10.0, hours: float = 36.0):
    """-> {"aos", "los", "max_el", "in_progress"} or None (no pass above min_el in `hours`)."""
    f = lambda t: elevation(el, t, lat, lon) - min_el
    step, t, end = 30.0, t0, t0 + hours * 3600
    in_prog = f(t0) >= 0
    if not in_prog:
        while t < end and f(t) < 0:
            t += step
        if t >= end:
            return None
        lo, hi = t - step, t
        for _ in range(12):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
        t = aos = hi
    else:
        aos = t0
    mx = -90.0
    while f(t) >= 0 and t < end + 3600:
        mx = max(mx, f(t) + min_el)
        t += 10.0
    return {"aos": aos, "los": t, "max_el": round(mx), "in_progress": in_prog}


class Iss(Feed):
    key, title, source = "iss", "ISS position + next pass", "wheretheiss.at + CelesTrak GP (pass computed here)"
    url = "https://api.wheretheiss.at/v1/satellites/25544"
    tle_url = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=json"
    poll_s = 30.0            # wheretheiss.at allows ~1 req/s; 30 s is plenty for a 64 px map
    TLE_S = 12 * 3600.0      # CelesTrak: elements change a few times a day, don't re-download < 2 h

    def __init__(self):
        super().__init__()
        self.tle: dict | None = None
        self.tle_at: float | None = None
        self.tle_error: str | None = None

    def _tle(self):
        if self.tle is None or time.time() - (self.tle_at or 0) > self.TLE_S:
            try:
                j = self.get_json(self.tle_url)
                self.tle = tle_elements(j[0] if isinstance(j, list) else j)
                self.tle_error = None
            except Exception as e:  # noqa: BLE001  (the live position still works)
                self.tle_error = f"{type(e).__name__}: {e}"[:120]
            self.tle_at = time.time()           # failed TLE fetches also wait TLE_S / 6
            if self.tle_error:
                self.tle_at -= self.TLE_S * 5 / 6
        return self.tle

    def fetch(self):
        j = self.get_json(self.url)
        now = time.time()
        out = {"lat": float(j["latitude"]), "lon": float(j["longitude"]), "alt_km": _num(j.get("altitude")),
               "vis": j.get("visibility"), "at": float(j.get("timestamp") or now), "pass": None, "track": []}
        el = self._tle()
        if el is not None:
            out["pass"] = next_pass(el, now, *PLACE.require_home())
            out["track"] = [subpoint(el, now + m * 60) for m in range(-45, 50, 3)]
        return out


class Quakes(Feed):
    key, title, source = "quake", "Earthquakes nearby", "USGS earthquake FDSN event service"
    poll_s = 300.0

    @property
    def url(self) -> str:  # type: ignore[override]
        lat, lon = PLACE.require_home()
        r = float(PLACE.get("livedata_quake_radius_km") or 500)
        return ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&eventtype=earthquake"
                f"&latitude={lat}&longitude={lon}&maxradiuskm={r:g}&minmagnitude=1.5&orderby=time&limit=5")

    def fetch(self):
        qs = []
        for f in self.get_json(self.url).get("features", []):
            p = f.get("properties") or {}
            if _num(p.get("mag")) is None:
                continue
            g = (f.get("geometry") or {}).get("coordinates") or [None, None, None]
            qs.append({"mag": float(p["mag"]), "place": p.get("place") or "?", "time": p["time"] / 1000.0,
                       "depth_km": _num(g[2]) if len(g) > 2 else None, "lat": g[1], "lon": g[0]})
        return {"quakes": qs, "biggest": max((q["mag"] for q in qs), default=None)}


def _km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


class Aircraft(Feed):
    key, title, source = "aircraft", "Aircraft nearby", "OpenSky Network (anonymous REST)"
    poll_s = 360.0           # anonymous: 400 credits/day -> 240/day leaves headroom

    @staticmethod
    def box() -> tuple[float, float, float, float]:
        """lamin, lomin, lamax, lomax around home (40 km radius ~ 0.7 sq deg = 1 credit)."""
        lat, lon = PLACE.require_home()
        r = float(PLACE.get("livedata_aircraft_radius_km") or 40)
        dlat = r / 111.0
        dlon = r / (111.0 * max(0.1, math.cos(math.radians(lat))))
        return (round(lat - dlat, 2), round(lon - dlon, 2), round(lat + dlat, 2), round(lon + dlon, 2))

    @property
    def url(self) -> str:  # type: ignore[override]
        return "https://opensky-network.org/api/states/all?lamin={}&lomin={}&lamax={}&lomax={}".format(*self.box())

    def fetch(self):
        HOME = PLACE.require_home()
        states = self.get_json(self.url).get("states") or []
        air = []
        for s in states:
            if len(s) < 14 or s[8] or s[5] is None or s[6] is None:     # on_ground / no position
                continue
            alt_m = s[7] if s[7] is not None else s[13]
            air.append({"call": (s[1] or s[0] or "?").strip().upper(), "alt_ft": None if alt_m is None else round(alt_m * 3.28084),
                        "km": round(_km(HOME[0], HOME[1], s[6], s[5]), 1), "track": _num(s[10])})
        air.sort(key=lambda a: a["km"])
        return {"count": len(air), "closest": air[:3]}


class Tides(Feed):
    key, title, source = "tides", "Tides", "NOAA CO-OPS predictions (set livedata_tide_station)"
    poll_s = 6 * 3600.0

    @property
    def station(self) -> str:
        return PLACE.require("livedata_tide_station", "NOAA CO-OPS station id")

    @property
    def url(self) -> str:  # type: ignore[override]
        day = pt(time.time() - 86400).strftime("%Y%m%d")         # from yesterday: curve has a past side
        return ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?product=predictions"
                f"&application=OrcPixels&station={self.station}&datum=MLLW&interval=hilo&units=english"
                f"&time_zone=gmt&format=json&begin_date={day}&range=96")

    def fetch(self):
        j = self.get_json(self.url)
        if "predictions" not in j:
            raise ValueError(str(j.get("error", j))[:120])
        ev = [{"t": parse_iso(p["t"].replace(" ", "T") + "+00:00"), "v": float(p["v"]), "type": p["type"]}
              for p in j["predictions"]]
        if len(ev) < 2:
            raise ValueError("not enough tide events")
        return {"events": ev}


def tide_height(events: list[dict], ts: float) -> float | None:
    """Cosine interpolation between successive high/low predictions."""
    for a, b in zip(events, events[1:]):
        if a["t"] <= ts <= b["t"]:
            f = (ts - a["t"]) / (b["t"] - a["t"])
            return a["v"] + (b["v"] - a["v"]) * (1 - math.cos(math.pi * f)) / 2
    return None


class River(Feed):
    key, title, source = "river", "River level", "USGS Water Data OGC API (gage height; set livedata_usgs_site)"

    @property
    def site(self) -> str:
        return PLACE.require("livedata_usgs_site", "USGS gauge id")

    @property
    def url(self) -> str:  # type: ignore[override]
        return ("https://api.waterdata.usgs.gov/ogcapi/v1/collections/continuous/items?f=json"
                f"&monitoring_location_id=USGS-{self.site}&parameter_code=00065&time=P1D&limit=400"
                "&skipGeometry=true&properties=time,value&sortby=time")

    # legacy NWIS IV service (being retired by USGS; answered 503 on 2026-10-02): fallback only
    @property
    def legacy_url(self) -> str:
        return (f"https://waterservices.usgs.gov/nwis/iv/?format=json&sites={self.site}"
                "&parameterCd=00065&period=P1D")

    poll_s = 900.0           # gauge reports every 5-15 min
    key_env = "USGS_API_KEY"  # optional (api.waterdata.usgs.gov works without; a key raises limits)

    def _ogc(self):
        h = {"Accept": "application/geo+json, application/json"}
        k = os.environ.get(self.key_env or "") or _setting("livedata_usgs_api_key")
        if k:
            h["X-Api-Key"] = k
        vals = []
        for f in self.get_json(self.url, h).get("features") or []:
            pr = f.get("properties") or {}
            try:
                vals.append((parse_iso(pr["time"]), float(pr["value"])))
            except (KeyError, TypeError, ValueError):
                continue
        return PLACE.label("livedata_river_label", "RIVER"), sorted(vals)

    def _legacy(self):
        ts = self.get_json(self.legacy_url)["value"]["timeSeries"]
        if not ts:
            raise ValueError("no time series")
        vals = []
        for v in ts[0]["values"][0]["value"]:
            try:
                x = float(v["value"])
            except (TypeError, ValueError):
                continue
            if x > -999:
                vals.append((parse_iso(v["dateTime"]), x))
        return ts[0]["sourceInfo"]["siteName"], vals

    def fetch(self):
        try:
            site, vals = self._ogc()
            if not vals:
                raise ValueError("no readings")
            via = "ogc"
        except Exception as e:  # noqa: BLE001
            log.info("river: OGC API failed (%s); trying legacy NWIS", e)
            site, vals = self._legacy()
            via = "nwis"
        vals = [(t, x) for t, x in vals if x > -999]
        if not vals:
            raise ValueError("no readings")
        t_last, last = vals[-1]
        prior = [x for t, x in vals if t <= t_last - 3 * 3600]
        return {"site": site, "ft": last, "at": t_last, "via": via,
                "trend": None if not prior else round(last - prior[-1], 2), "series": [x for _, x in vals]}


# ------------------------------------------------------------- sun / moon (local)
def sun_times(date: dt.date, lat: float, lon: float) -> tuple[float | None, float | None]:
    """Sunrise/sunset (UTC epoch) for a calendar date, standard sunrise equation (+-1 min)."""
    n = (date - dt.date(2000, 1, 1)).days
    js = n - lon / 360.0                                  # lon east-positive
    M = math.radians((357.5291 + 0.98560028 * js) % 360)
    C = 1.9148 * math.sin(M) + 0.02 * math.sin(2 * M) + 0.0003 * math.sin(3 * M)
    lam = math.radians((math.degrees(M) + C + 180 + 102.9372) % 360)
    jt = 2451545.0 + js + 0.0053 * math.sin(M) - 0.0069 * math.sin(2 * lam)
    dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cw = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(dec)) / (math.cos(phi) * math.cos(dec))
    if not -1 <= cw <= 1:
        return None, None
    w = math.degrees(math.acos(cw)) / 360.0
    to_ts = lambda jd: (jd - 2440587.5) * 86400.0
    return to_ts(jt - w), to_ts(jt + w)


SYNODIC = 29.530588853
NEW_MOON_JD = 2451550.1


def moon_phase(ts: float) -> dict:
    jd = ts / 86400.0 + 2440587.5
    age = (jd - NEW_MOON_JD) % SYNODIC
    illum = (1 - math.cos(2 * math.pi * age / SYNODIC)) / 2
    names = [(1.0, "NEW"), (6.4, "WAX CRES"), (8.4, "1ST QTR"), (13.8, "WAX GIBB"), (15.8, "FULL"),
             (21.1, "WAN GIBB"), (23.1, "LAST QTR"), (28.5, "WAN CRES"), (30.0, "NEW")]
    name = next(nm for lim, nm in names if age < lim)
    return {"age": age, "illum": illum, "name": name, "waxing": age < SYNODIC / 2}


class SunMoon(Feed):
    key, title, source = "sunmoon", "Sunrise, sunset + moon", "computed locally (no network)"
    url = "local"
    poll_s = 300.0
    local = True

    def fetch(self):
        now = time.time()
        today = pt(now).date()
        rise, sset = sun_times(today, *PLACE.require_home())
        if sset is not None and now > sset:                 # after sunset: tomorrow's sunrise next
            rise2, _ = sun_times(today + dt.timedelta(days=1), *PLACE.require_home())
        else:
            rise2 = None
        return {"rise": rise, "set": sset, "next_rise": rise2, "moon": moon_phase(now),
                "day_s": (sset - rise) if rise and sset else None}


class UtcClock(Feed):
    key, title, source = "clock", "UTC / local clock", "computed locally (no network)"
    url = "local"
    poll_s = 3600.0
    local = True

    def fetch(self):
        return {"ok": True}


class GitHub(Feed):
    key, title, source = "github", "GitHub repos", "GitHub REST API (public, read-only; set livedata_github_user)"

    @property
    def user(self) -> str:
        return PLACE.require("livedata_github_user", "GitHub user or org")

    @property
    def url(self) -> str:  # type: ignore[override]
        return f"https://api.github.com/users/{self.user}/repos?per_page=100&sort=pushed"
    poll_s = 1800.0          # unauthenticated: 60 requests/h; optional GITHUB_TOKEN env raises that
    key_env = "GITHUB_TOKEN"

    def fetch(self):
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        tok = os.environ.get(self.key_env or "") or _setting("livedata_github_token")
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        repos = [r for r in self.get_json(self.url, h) if not r.get("fork")]
        if not repos:
            raise ValueError("no public repos")
        pushed = max(repos, key=lambda r: r.get("pushed_at") or "")
        top = sorted(repos, key=lambda r: -(r.get("stargazers_count") or 0))
        return {"repos": len(repos), "stars": sum(r.get("stargazers_count") or 0 for r in repos),
                "forks": sum(r.get("forks_count") or 0 for r in repos),
                "last": pushed["name"], "last_at": parse_iso(pushed["pushed_at"]) if pushed.get("pushed_at") else None,
                "top": [(r["name"], r.get("stargazers_count") or 0) for r in top[:3]]}


FEEDS = [NwsNow, NwsForecast, NwsAlerts, AirQuality, SpaceWx, XRay, HfBands, Iss, Quakes, Aircraft,
         Tides, River, SunMoon, UtcClock, GitHub]
