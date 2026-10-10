"""Offline ham-radio geography for the FT8/FT4 screens: Maidenhead grid <-> lat/lon,
grid square -> place name, callsign -> DXCC entity, distance + bearing from home.

No network: the tables live in orcpixels/data/ham (see its README for sources/licences).
  * grid_places.tsv.gz: largest city per 4-char square (GeoNames cities15000)
  * cty.dat.gz: AD1C country file (prefix -> DXCC, lat/lon; used when no grid was sent)
"""
from __future__ import annotations

import gzip
import math
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "ham"
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2})?$", re.I)
CALL_RE = re.compile(r"^(?=.*[0-9])(?=.*[A-Z])[A-Z0-9/]{3,12}$")
COMPASS8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
STATE_NAMES = {"US": "", "CA": "", "AU": ""}


def valid_grid(g: str | None) -> bool:
    return bool(g) and bool(GRID_RE.match(g)) and g.upper()[:4] != "RR73"


def grid_latlon(g: str) -> tuple[float, float]:
    """Centre of a 4- or 6-character Maidenhead locator."""
    g = g.strip().upper()
    if not valid_grid(g):
        raise ValueError(f"bad grid {g!r}")
    lon = (ord(g[0]) - 65) * 20 - 180 + int(g[2]) * 2
    lat = (ord(g[1]) - 65) * 10 - 90 + int(g[3])
    if len(g) >= 6:
        lon += (ord(g[4]) - 65) * (2 / 24) + 1 / 24
        lat += (ord(g[5]) - 65) * (1 / 24) + 0.5 / 24
    else:
        lon += 1.0
        lat += 0.5
    return lat, lon


def latlon_grid(lat: float, lon: float) -> str:
    lon, lat = lon + 180, lat + 90
    return f"{chr(65 + int(lon // 20))}{chr(65 + int(lat // 10))}{int(lon % 20 // 2)}{int(lat % 10)}"


def dist_bearing(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """Great-circle km and initial bearing (deg) from a to b."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return km, (math.degrees(math.atan2(y, x)) + 360) % 360


def compass8(deg: float) -> str:
    return COMPASS8[int((deg % 360) / 45 + 0.5) % 8]


# ------------------------------------------------------------------ places
@lru_cache(None)
def _places() -> dict[str, tuple[str, str, str, bool]]:
    out = {}
    for line in gzip.decompress((DATA / "grid_places.tsv.gz").read_bytes()).decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split("\t")
        out[p[0]] = (p[1], p[2], p[3], len(p) > 4 and p[4] == "~")
    return out


@lru_cache(None)
def _countries() -> dict[str, str]:
    out = {}
    for line in (DATA / "countries.tsv").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            iso, name = line.split("\t", 1)
            out[iso] = name
    return out


SHORT_COUNTRY = {"United States": "USA", "United Kingdom": "UK", "Russia": "RUSSIA", "European Russia": "RUSSIA",
                 "Asiatic Russia": "RUSSIA", "Federal Republic of Germany": "GERMANY", "Republic of Korea": "S KOREA",
                 "South Korea": "S KOREA", "New Zealand": "NZ", "South Africa": "S AFRICA", "Dominican Republic": "DOM REP",
                 "Czech Republic": "CZECHIA", "Hawaii": "HAWAII", "Alaska": "ALASKA", "Puerto Rico": "PUERTO RICO"}


def short_country(name: str) -> str:
    return SHORT_COUNTRY.get(name, name).upper()


def grid_place(g: str) -> dict | None:
    """{'city', 'state', 'cc', 'country', 'near'} for the 4-char square, or None (ocean / empty)."""
    if not valid_grid(g):
        return None
    p = _places().get(g.upper()[:4])
    if not p:
        return None
    city, st, cc, near = p
    return {"city": city, "state": st, "cc": cc, "country": _countries().get(cc, cc), "near": near}


def place_label(pl: dict | None, width_chars: int = 18) -> str:
    """'BOISE ID' (US/CA/AU), 'TOKYO JAPAN' elsewhere; ISO code if the name is too long."""
    if not pl:
        return ""
    city = pl["city"].upper()
    if pl.get("state"):
        return f"{city} {pl['state']}"
    country = short_country(pl.get("country") or pl.get("cc") or "")
    lab = f"{city} {country}".strip()
    return lab if len(lab) <= width_chars else f"{city} {pl.get('cc', '')}".strip()


# ------------------------------------------------------------------ DXCC (cty.dat)
@lru_cache(None)
def _cty():
    """-> (exact {call: entity}, prefixes {prefix: entity}); entity = (name, cont, lat, lon, primary)."""
    text = gzip.decompress((DATA / "cty.dat.gz").read_bytes()).decode("latin-1")
    exact, prefixes = {}, {}
    for rec in text.split(";"):
        rec = rec.strip()
        if not rec or ":" not in rec:
            continue
        head = rec.split(":")
        if len(head) < 9:
            continue
        name, cont = head[0].strip(), head[3].strip()
        lat, lon = float(head[4]), -float(head[5])          # cty.dat: west longitude positive
        primary = head[7].strip().lstrip("*")
        ent = (name, cont, lat, lon, primary)
        for tok in ":".join(head[8:]).replace("\n", " ").split(","):
            tok = re.sub(r"\(.*?\)|\[.*?\]|<.*?>|\{.*?\}|~.*?~", "", tok.strip()).strip()
            if not tok:
                continue
            if tok.startswith("="):
                exact[tok[1:]] = ent
            else:
                prefixes[tok] = ent
        prefixes.setdefault(primary, ent)
    return exact, prefixes


def base_call(call: str) -> str:
    """'KH6/K7ABC' -> 'KH6' (prefix wins), 'K7ABC/P' -> 'K7ABC', '<K7ABC>' -> 'K7ABC'."""
    c = call.strip().upper().strip("<>")
    if "/" not in c:
        return c
    parts = [p for p in c.split("/") if p and p not in ("P", "M", "MM", "AM", "QRP", "R", "A", "B")]
    if not parts:
        return c.replace("/", "")
    if len(parts) == 1:
        return parts[0]
    return min(parts, key=len)          # the portable prefix is normally the shorter part


def dxcc(call: str) -> dict | None:
    if not call:
        return None
    exact, prefixes = _cty()
    c = call.strip().upper().strip("<>")
    ent = exact.get(c)
    if ent is None:
        b = base_call(c)
        ent = exact.get(b)
        for n in range(len(b), 0, -1):
            if ent is not None:
                break
            ent = prefixes.get(b[:n])
    if ent is None:
        return None
    return {"name": ent[0], "cont": ent[1], "lat": ent[2], "lon": ent[3], "prefix": ent[4]}


# ------------------------------------------------------------------ one call
BIG_ENTITIES = {"United States", "Canada", "European Russia", "Asiatic Russia", "Australia", "Brazil", "China",
                "Argentina", "India", "Kazakhstan", "Mexico", "Indonesia", "Chile", "Alaska"}
def locate(call: str | None, grid: str | None, home: str = "", miles: bool = True) -> dict:
    """Where a decoded station is: place, distance and bearing from `home`.
    Grid first (city of the square); without a grid, the DXCC entity of the callsign."""
    out = {"place": "", "lat": None, "lon": None, "km": None, "dist": "", "dir": "", "src": None, "country": "",
           "cont": "", "dxcc": ""}
    d = dxcc(call) if call else None
    if d:
        out.update(country=d["name"], cont=d["cont"], dxcc=d["prefix"])
    if valid_grid(grid):
        lat, lon = grid_latlon(grid)
        pl = grid_place(grid)
        out.update(lat=lat, lon=lon, src="grid", place=place_label(pl) or (short_country(d["name"]) if d else grid.upper()))
        if pl and pl.get("country"):
            out["country"] = out["country"] or pl["country"]
    elif d:
        out.update(lat=d["lat"], lon=d["lon"], src="dxcc", place=short_country(d["name"]))
    if out["src"] == "dxcc" and out["country"] in BIG_ENTITIES:
        return out                      # a country centroid says nothing about distance here
    if out["lat"] is not None and valid_grid(home):
        km, brg = dist_bearing(grid_latlon(home), (out["lat"], out["lon"]))
        out["km"] = km
        v = km / 1.609344 if miles else km
        unit = "MI" if miles else "KM"
        out["dist"] = f"{v:.0f}{unit}" if v >= 1 else f"<1{unit}"
        out["dir"] = compass8(brg)
        out["bearing"] = brg
    return out
