"""Parks on the Air spots (api.pota.app/spot/activator: keyless, public, the feed the POTA
site itself uses; polled every 5 min, shared by the three screens). Distances use the home
location / FT8 home grid. SOTA is not included: its old spots endpoint is deprecated and
the new API asks clients to read and accept its terms first."""
from __future__ import annotations

from .. import categories as CAT
from .. import live_adaptive as A
from .. import live_templates as T
from ..sources import livefeeds as LF

COLOR = (255, 168, 20)
GREEN = (60, 225, 90)
BANDS = ((1800, 2000, "160"), (3500, 4000, "80"), (5330, 5410, "60"), (7000, 7300, "40"), (10100, 10150, "30"),
         (14000, 14350, "20"), (18068, 18168, "17"), (21000, 21450, "15"), (24890, 24990, "12"), (28000, 29700, "10"),
         (50000, 54000, "6"), (144000, 148000, "2"), (420000, 450000, "70CM"))
BAND_ORDER = [b[2] for b in BANDS]


def band(khz) -> str:
    try:
        f = float(khz)
    except (TypeError, ValueError):
        return "?"
    for lo, hi, n in BANDS:
        if lo <= f <= hi:
            return n
    return "?"


class PotaFeed(LF.Feed):
    source, poll_s = "POTA spots api.pota.app (keyless)", 300.0
    url = "https://api.pota.app/spot/activator"

    def fetch(self) -> dict:
        d = T.cached(self, self.url, 240.0)
        try:
            home = T.home_latlon()
        except Exception:  # noqa: BLE001
            home = None
        rows = []
        for s in d or []:
            if s.get("invalid"):
                continue
            lat, lon = s.get("latitude"), s.get("longitude")
            rows.append({"call": A.clean(s.get("activator")).upper(), "ref": A.clean(s.get("reference")).upper(),
                         "khz": s.get("frequency"), "band": band(s.get("frequency")), "mode": A.clean(s.get("mode")).upper(),
                         "park": A.clean(s.get("name")).upper(), "loc": A.clean(s.get("locationDesc")).upper(),
                         "at": LF.parse_iso(s["spotTime"]) if s.get("spotTime") else None,
                         "km": T.km(home, (lat, lon)) if home and lat is not None and lon is not None else None})
        rows.sort(key=lambda r: -(r["at"] or 0))
        if not rows:
            raise ValueError("no spots")
        return {"rows": rows, "home": bool(home)}


class PotaAllFeed(PotaFeed):
    key, title = "pota_spots", "POTA: latest activator spots"


class PotaNearFeed(PotaFeed):
    key, title = "pota_near", "POTA: nearest activators"


class PotaBandFeed(PotaFeed):
    key, title = "pota_bands", "POTA: activity by band"


def mhz(khz) -> str:
    try:
        return f"{float(khz) / 1000:.3f}"
    except (TypeError, ValueError):
        return "?"


class PotaSpots(A.ListScreen):
    key, short, feed, category, color, tag = "pota_spots", "POTA SPOTS", PotaAllFeed, "ham", COLOR, "POTA"

    def head_right(self, v):
        return f"{len(v.get('rows') or [])} ON"

    def items(self, v):
        return [(f"{r['call']} {r['ref']}", f"{mhz(r['khz'])} {r['mode']}", r["loc"][:6]) for r in (v or {}).get("rows", [])[:12]]


class PotaNear(A.ListScreen):
    key, short, feed, category, color, tag = "pota_near", "POTA NEAR", PotaNearFeed, "ham", COLOR, "NEAR"

    def items(self, v):
        rows = [r for r in (v or {}).get("rows", []) if r["km"] is not None]
        rows.sort(key=lambda r: r["km"])
        return [(f"{r['call']} {r['ref']}", f"{mhz(r['khz'])} {r['mode']}", f"{r['km']:,.0f}KM") for r in rows[:10]]

    def empty_msg(self, v):
        return "SET HOME LOCATION" if v and not v.get("home") else "NO SPOTS"


class PotaBands(T.BarsScreen):
    key, short, feed, category, color = "pota_bands", "POTA BANDS", PotaBandFeed, "ham", COLOR

    def bars(self, v):
        cnt: dict[str, int] = {}
        for r in (v or {}).get("rows", []):
            cnt[r["band"]] = cnt.get(r["band"], 0) + 1
        bars = [(b, cnt.get(b, 0), GREEN if cnt.get(b, 0) == max(cnt.values() or [0]) else COLOR)
                for b in ("80", "60", "40", "30", "20", "17", "15", "12", "10", "6", "2")]
        return ("POTA BANDS", bars, f"{sum(cnt.values())} SPOTS")


SCREENS = [PotaSpots(), PotaNear(), PotaBands()]
for _s in SCREENS:
    CAT.assign(_s.key, "ham")
