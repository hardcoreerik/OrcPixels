"""Where this install is: home location, time zone and the place-specific Live Data
sources. Everything is a setting (config/settings.json, see config/settings.example.json);
nothing about any one person's setup is built in.

Screens that need a location stay on their "not configured" screen until home_lat /
home_lon are set. NWS station / alert area are derived from the location (api.weather.gov
points lookup) unless set explicitly; tide station, river gauge and GitHub user have no
sensible automatic default and are simply off until set.
"""
from __future__ import annotations

import datetime as dt
import os
import time

DEFAULTS: dict[str, object] = {
    "home_lat": None,                 # decimal degrees, e.g. 45.52
    "home_lon": None,                 # decimal degrees, e.g. -122.68 (west negative)
    "home_label": "HOME",             # short place tag on screens (3-6 chars), e.g. PDX
    "timezone": "local",              # local | utc | us-pacific | us-mountain | us-central | us-eastern | +05:30
    "timezone_label": "",             # clock tag; blank = derived (PT, ET, CET, UTC, ...)
    "livedata_nws_station": "",       # NWS observation station (US only); blank = nearest to home
    "livedata_alerts_area": "",       # NWS alert area (US state code); blank = state of home
    "livedata_alerts_label": "",      # screen tag, blank = the area code
    "livedata_quake_radius_km": 500,  # earthquakes within this radius of home
    "livedata_quake_label": "",       # screen tag, e.g. PNW; blank = QUAKES
    "livedata_aircraft_radius_km": 40,
    "livedata_tide_station": "",      # NOAA CO-OPS station id, e.g. 9435380
    "livedata_tide_label": "",        # e.g. NEWPORT
    "livedata_usgs_site": "",         # USGS gauge id, e.g. 14211720
    "livedata_river_label": "",       # e.g. WILLAMETTE
    "livedata_github_user": "",       # GitHub user/org for the GitHub screen
}
OVERRIDE: dict[str, object] = {}      # tests


class NotConfigured(ValueError):
    """A screen's place setting is missing: the screen shows its no-data screen."""


def get(key: str):
    if key in OVERRIDE:
        return OVERRIDE[key]
    try:
        from . import settings as S
        v = S.load().get(key)
    except Exception:  # noqa: BLE001
        v = None
    return DEFAULTS.get(key) if v in (None, "") else v


def home() -> tuple[float, float] | None:
    try:
        lat, lon = get("home_lat"), get("home_lon")
        if lat is None or lon is None:
            return None
        lat, lon = float(lat), float(lon)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError):
        pass
    return None


def require_home() -> tuple[float, float]:
    h = home()
    if h is None:
        raise NotConfigured("set home_lat / home_lon (Settings > Location)")
    return h


def require(key: str, what: str) -> str:
    v = str(get(key) or "").strip()
    if not v:
        raise NotConfigured(f"set {key} ({what})")
    return v


def label(key: str, default: str) -> str:
    return (str(get(key) or "").strip() or default).upper()


# ------------------------------------------------------------------ time zone
def _nth_sunday(y: int, m: int, n: int) -> int:
    d = dt.date(y, m, 1)
    first = (6 - d.weekday()) % 7 + 1
    return first + 7 * (n - 1)


_US = {"us-eastern": (-5, "ET"), "us-central": (-6, "CT"), "us-mountain": (-7, "MT"), "us-pacific": (-8, "PT")}


def _tz_setting() -> str:
    return str(os.environ.get("ORCPIXELS_TZ") or get("timezone") or "local").strip().lower()


def utc_offset_h(ts: float) -> float:
    """Offset of the configured zone at ts (hours). US rules are computed here because a
    Windows venv has no tzdata for zoneinfo; 'local' uses the OS zone."""
    z = _tz_setting()
    if z == "utc":
        return 0.0
    if z in _US:
        std = _US[z][0]
        y = dt.datetime.fromtimestamp(ts, dt.timezone.utc).year
        start = dt.datetime(y, 3, _nth_sunday(y, 3, 2), 2 - std, tzinfo=dt.timezone.utc).timestamp()
        end = dt.datetime(y, 11, _nth_sunday(y, 11, 1), 1 - std, tzinfo=dt.timezone.utc).timestamp()
        return std + (1 if start <= ts < end else 0)
    if z[:1] in "+-" and ":" in z:
        sign = -1 if z[0] == "-" else 1
        h, m = z[1:].split(":", 1)
        return sign * (int(h) + int(m) / 60)
    off = dt.datetime.fromtimestamp(ts).astimezone().utcoffset()
    return off.total_seconds() / 3600 if off is not None else 0.0


def tz_label(ts: float | None = None) -> str:
    lab = str(get("timezone_label") or "").strip()
    if lab:
        return lab.upper()[:4]
    z = _tz_setting()
    if z == "utc":
        return "UTC"
    if z in _US:
        return _US[z][1]
    if z[:1] in "+-":
        return "UTC" + z
    name = time.strftime("%Z", time.localtime(ts if ts is not None else time.time())) or "LT"
    if " " in name:                                  # Windows: "Pacific Daylight Time"
        name = "".join(w[0] for w in name.split() if w)
    if len(name) == 3 and name[1] in "DS" and name.isupper():   # PDT/PST -> PT, EDT -> ET
        return name[0] + name[2]
    return name.upper()[:4]


def local(ts: float) -> dt.datetime:
    off = utc_offset_h(ts)
    return dt.datetime.fromtimestamp(ts, dt.timezone(dt.timedelta(hours=off), tz_label(ts)))


# ------------------------------------------------------------------ screen labels
def home_tag() -> str:
    return label("home_label", "HOME")[:6]


def alerts_tag() -> str:
    return label("livedata_alerts_label", str(get("livedata_alerts_area") or ""))[:8]


def quake_tag() -> str:
    return label("livedata_quake_label", "")[:6]


def tide_tag() -> str:
    return label("livedata_tide_label", "TIDES")[:10]


def river_tag() -> str:
    return label("livedata_river_label", "RIVER")[:10]


class Lbl:
    """Class attribute whose value is computed on access (screen titles from settings)."""

    def __init__(self, fn):
        self.fn = fn

    def __get__(self, obj, owner=None) -> str:
        return self.fn()


# ------------------------------------------------------------------ UI (Live tab > feed settings)
CONFIG = {        # settings key -> (default, kind, label); shown first in the feed settings
    "home_lat": ("", "lat", "Home latitude (decimal, e.g. 45.52)"),
    "home_lon": ("", "lon", "Home longitude (decimal, west negative)"),
    "home_label": ("HOME", "tag", "Home tag on screens (e.g. PDX)"),
    "timezone": ("local", "tz", "Time zone: local | utc | us-pacific | us-mountain | us-central | us-eastern | +05:30"),
    "livedata_nws_station": ("", "tag", "NWS station (US; blank = nearest)"),
    "livedata_alerts_area": ("", "tag", "NWS alert area (US state; blank = home state)"),
    "livedata_alerts_label": ("", "tag", "Alerts screen tag (e.g. OREGON)"),
    "livedata_quake_radius_km": ("500", "num", "Earthquake radius (km)"),
    "livedata_quake_label": ("", "tag", "Earthquake screen tag (e.g. PNW)"),
    "livedata_tide_station": ("", "id", "NOAA tide station id"),
    "livedata_tide_label": ("", "tag", "Tide screen tag"),
    "livedata_usgs_site": ("", "id", "USGS river gauge id"),
    "livedata_river_label": ("", "tag", "River screen tag"),
    "livedata_github_user": ("", "id", "GitHub user / org"),
}
PLACE_FEEDS = {"wx_now", "forecast", "alerts", "aqi", "quake", "aircraft", "tides", "river", "github",
               "iss", "sunmoon", "clock"}


def validate(key: str, value):
    """Normalised value ('' = back to default) or ValueError."""
    if key not in CONFIG:
        raise ValueError(f"unknown setting {key}")
    v = ("" if value is None else str(value)).strip()
    if not v:
        return ""
    kind = CONFIG[key][1]
    if kind in ("lat", "lon", "num"):
        try:
            f = float(v)
        except ValueError:
            raise ValueError(f"{key}: must be a number") from None
        lim = 90 if kind == "lat" else 180 if kind == "lon" else 20000
        if not (-lim <= f <= lim) or (kind == "num" and f <= 0):
            raise ValueError(f"{key}: out of range")
        return f
    if kind == "tz":
        z = v.lower()
        ok = z in ("local", "utc") or z in _US or (z[:1] in "+-" and ":" in z)
        if not ok:
            raise ValueError(f"{key}: local, utc, us-pacific/mountain/central/eastern or +HH:MM")
        return z
    if len(v) > 40 or not all(c.isalnum() or c in " -_./" for c in v):
        raise ValueError(f"{key}: letters, digits and - _ . / only")
    return v.upper() if kind == "tag" else v
