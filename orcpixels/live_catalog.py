"""Declarative free-source catalog ("Get more free sources", 2026-10-14).

A catalog is JSON DATA only: URL, field paths, a layout template, poll interval and
category. Nothing from the network is ever executed: there is no eval, no import, no
format-spec language, only dotted field paths and a fixed set of filters. Sources come
from two places, merged by id:

  bundled  orcpixels/data/free_sources.json (ships with every install / update)
  fetched  <data>/config/free_sources.json, written by refresh() from CATALOG_URL
           (catalog/free_sources.json in the public OrcPixels repo), only after the whole
           file validated; a bad download is rejected and the previous catalog stays.

Schema (version 1):
  {"schema": 1, "version": "2026.10.14", "categories": [{"id", "title", "order", "color", "blurb"}],
   "keys": [{"service", "label", "signup", "fallback", "note"}],
   "sources": [{"id", "title", "short", "category", "color", "url", "headers", "poll_s",
                "template": "text" | "value" | "list", "fields": {...}, "attribution",
                "key": "<service>", "provider", "listed"}]}

URL placeholders: {lat} {lon} {country} {yyyy} {mm} {dd} {key}. Location placeholders use
the home location (or FT8 home grid) automatically.
Field values: "a.b.0.c" (a path) or "text {a.b|filter} text"; filters: upper, int, num,
num1, short, pct, html, date, ago, trim40.
  text:  title, body, attribution
  value: big, unit, label, sub, gauge_max, extras: [[label, value], ...]
  list:  items (path to the array, "" = the response), main, sub, right, limit
"""
from __future__ import annotations

import datetime as dt
import html
import ipaddress
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path

from . import categories as CAT
from . import live_adaptive as A
from . import live_templates as T
from . import paths as P
from . import place as PLACE
from .sources import livefeeds as LF

log = logging.getLogger("orcpixels.catalog")

CATALOG_URL = "https://raw.githubusercontent.com/hardcoreerik/OrcPixels/main/catalog/free_sources.json"
BUNDLED = Path(__file__).resolve().parent / "data" / "free_sources.json"
SCHEMA = 1
MAX_BYTES = 256 * 1024
MAX_SOURCES = 200
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
PLACEHOLDERS = {"lat", "lon", "country", "yyyy", "mm", "dd", "key"}
TEMPLATES = {"text": ("body",), "value": ("big",), "list": ("main",)}
FILTERS = {"upper", "int", "num", "num1", "short", "pct", "html", "date", "ago", "trim40"}
ALLOWED_HEADERS = {"accept", "accept-language"}
TOKEN = re.compile(r"\{([A-Za-z0-9_.\-]*)(?:\|([a-z0-9]+))?\}")

STATE: dict = {"loaded": [], "errors": [], "version": None, "fetched_at": None, "source": "bundled", "last": None}
CATALOG_KEYS: set[str] = set()


def user_file() -> Path:
    return P.CONFIG / "free_sources.json"


# ------------------------------------------------------------------ validation
class Invalid(ValueError):
    pass


def _color(s, default=(255, 236, 190)):
    if isinstance(s, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))
    return default


def _check_url(u: str) -> None:
    if not isinstance(u, str) or len(u) > 500:
        raise Invalid("url missing or too long")
    p = urllib.parse.urlsplit(u.replace("{", "").replace("}", ""))
    if p.scheme != "https" or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise Invalid("url must be plain https://host/...")
    host = p.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".internal", ".lan", ".home")) or "." not in host:
        raise Invalid("url must be a public host")
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        raise Invalid("url must use a host name, not an IP address")
    for name in re.findall(r"\{([^}]*)\}", u):
        if name not in PLACEHOLDERS:
            raise Invalid(f"unknown placeholder {{{name}}}")
    if re.match(r"https://[^/]*\{", u):
        raise Invalid("placeholders are not allowed in the host")


def _check_field(v, what: str) -> None:
    if isinstance(v, list):
        for x in v:
            _check_field(x, what)
        return
    if not isinstance(v, str) or len(v) > 300:
        raise Invalid(f"{what}: must be a short string")
    for path, flt in TOKEN.findall(v):
        if flt and flt not in FILTERS:
            raise Invalid(f"{what}: unknown filter {flt}")


def validate_source(s: dict, builtin: set[str]) -> dict:
    if not isinstance(s, dict):
        raise Invalid("source must be an object")
    sid = s.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise Invalid(f"bad id {sid!r}")
    if sid in builtin:
        raise Invalid(f"{sid}: id is used by a built-in screen")
    tpl = s.get("template")
    if tpl not in TEMPLATES:
        raise Invalid(f"{sid}: template must be one of {', '.join(TEMPLATES)}")
    _check_url(s.get("url"))
    f = s.get("fields")
    if not isinstance(f, dict) or not all(k in f for k in TEMPLATES[tpl]):
        raise Invalid(f"{sid}: fields must include {', '.join(TEMPLATES[tpl])}")
    for k, v in f.items():
        if k in ("limit", "gauge_max"):
            if not isinstance(v, (int, float)) or not 0 < v <= 1e15:
                raise Invalid(f"{sid}: {k} must be a positive number")
        elif k == "extras":
            if not isinstance(v, list) or len(v) > 8 or not all(isinstance(x, list) and len(x) == 2 for x in v):
                raise Invalid(f"{sid}: extras must be up to 8 [label, value] pairs")
            _check_field(v, f"{sid}.extras")
        elif k == "items":
            if not isinstance(v, str) or "{" in v:
                raise Invalid(f"{sid}: items must be a path")
        else:
            _check_field(v, f"{sid}.{k}")
    poll = s.get("poll_s", 3600)
    if not isinstance(poll, (int, float)) or not 300 <= poll <= 7 * 86400:
        raise Invalid(f"{sid}: poll_s must be 300..604800 seconds")
    hd = s.get("headers") or {}
    if not isinstance(hd, dict) or any(k.lower() not in ALLOWED_HEADERS or not isinstance(v, str) or len(v) > 100
                                       for k, v in hd.items()):
        raise Invalid(f"{sid}: only Accept / Accept-Language headers are allowed")
    for k in ("title", "short", "attribution", "provider", "listed", "category", "key", "docs"):
        if k in s and (not isinstance(s[k], str) or len(s[k]) > 120):
            raise Invalid(f"{sid}: {k} must be a short string")
    return {"id": sid, "title": s.get("title") or sid, "short": (s.get("short") or sid).upper()[:14],
            "category": s.get("category") or "other", "color": _color(s.get("color")), "url": s["url"],
            "headers": dict(hd), "poll_s": float(poll), "template": tpl, "fields": dict(f),
            "attribution": s.get("attribution") or "", "provider": s.get("provider") or "",
            "key": s.get("key") or "", "listed": s.get("listed") or "", "docs": s.get("docs") or ""}


def validate(doc, builtin: set[str]) -> tuple[dict, list[str]]:
    """Whole-document check (raises Invalid) + per-source check (bad sources are skipped
    and reported). Returns (clean doc, errors)."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise Invalid(f"not a schema-{SCHEMA} catalog")
    srcs = doc.get("sources")
    if not isinstance(srcs, list) or not srcs or len(srcs) > MAX_SOURCES:
        raise Invalid(f"sources must be a list of 1..{MAX_SOURCES}")
    errors, out, seen = [], [], set()
    cats = []
    for c in doc.get("categories") or []:
        if isinstance(c, dict) and isinstance(c.get("id"), str) and ID_RE.match(c["id"]) and isinstance(c.get("title"), str):
            cats.append({"id": c["id"], "title": c["title"][:40], "order": int(c.get("order") or 500) if str(c.get("order", "")).lstrip("-").isdigit() else 500,
                         "color": _color(c.get("color"), (150, 156, 170)), "blurb": str(c.get("blurb") or "")[:120]})
        else:
            errors.append(f"bad category {c!r}"[:120])
    keys = []
    for k in doc.get("keys") or []:
        try:
            if not (isinstance(k, dict) and ID_RE.match(str(k.get("service"))) and isinstance(k.get("label"), str)):
                raise Invalid("bad key entry")
            _check_url(k.get("signup"))
            keys.append({"service": k["service"], "label": k["label"][:60], "signup": k["signup"],
                         "fallback": str(k.get("fallback") or "")[:40], "note": str(k.get("note") or "")[:200]})
        except Invalid as e:
            errors.append(str(e))
    known_cats = set(CAT.CATEGORIES) | {c["id"] for c in cats}
    known_keys = set(T.KEYS) | {k["service"] for k in keys}
    for s in srcs:
        try:
            c = validate_source(s, builtin)
            if c["id"] in seen:
                raise Invalid(f"{c['id']}: duplicate id")
            if c["category"] not in known_cats:
                raise Invalid(f"{c['id']}: unknown category {c['category']}")
            if c["key"] and c["key"] not in known_keys:
                raise Invalid(f"{c['id']}: unknown key service {c['key']}")
            seen.add(c["id"])
            out.append(c)
        except Invalid as e:
            errors.append(str(e))
    if not out:
        raise Invalid("no valid sources: " + "; ".join(errors[:3]))
    return {"schema": SCHEMA, "version": str(doc.get("version") or "")[:20], "categories": cats, "keys": keys,
            "sources": out}, errors


# ------------------------------------------------------------------ field extraction (no code, paths only)
def path_get(obj, path: str):
    if path in ("", "."):
        return obj
    for part in path.split("."):
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
        if obj is None:
            return None
    return obj


def _filter(v, flt: str):
    if flt == "html":
        return html.unescape(str(v))
    if flt == "upper":
        return str(v).upper()
    if flt == "trim40":
        s = str(v)
        return s if len(s) <= 40 else s[:39] + "."
    if flt in ("int", "num", "num1", "short", "pct"):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return str(v)
        return {"int": f"{f:.0f}", "num": f"{f:,.0f}", "num1": f"{f:,.1f}", "short": A.short_num(f), "pct": f"{f:.0f}%"}[flt]
    if flt in ("date", "ago"):
        try:
            ts = float(v) if isinstance(v, (int, float)) or str(v).isdigit() else LF.parse_iso(str(v))
            if ts > 1e11:
                ts /= 1000.0
        except (TypeError, ValueError):
            return str(v)
        return LF.pt(ts).strftime("%b %d").upper() if flt == "date" else LF.ago(ts)
    return str(v)


def render_field(spec, obj) -> str:
    if not spec:
        return ""
    if "{" not in spec:
        v = path_get(obj, spec)
        return "" if v is None else A.clean(html.unescape(str(v)))

    def sub(m):
        v = path_get(obj, m.group(1))
        if v is None:
            return ""
        return _filter(v, m.group(2)) if m.group(2) else str(v)
    return A.clean(TOKEN.sub(sub, spec))


def fill_url(u: str, key: str = "") -> str:
    needs = set(re.findall(r"\{([^}]*)\}", u))
    vals = {}
    if needs & {"lat", "lon"}:
        lat, lon = T.home_latlon()
        vals.update(lat=f"{lat:.3f}", lon=f"{lon:.3f}")
    if "country" in needs:
        vals["country"] = T.country()
    if needs & {"yyyy", "mm", "dd"}:
        d = LF.pt(time.time()).date()
        vals.update(yyyy=f"{d.year:04d}", mm=f"{d.month:02d}", dd=f"{d.day:02d}")
    if "key" in needs:
        vals["key"] = urllib.parse.quote(key, safe="")
    return re.sub(r"\{([^}]*)\}", lambda m: vals.get(m.group(1), ""), u)


# ------------------------------------------------------------------ feed + screens
class CatalogFeed(T.KeyedFeed):
    def __init__(self, spec: dict):
        super().__init__()
        self.spec = spec
        self.key, self.title, self.poll_s = spec["id"], spec["title"], spec["poll_s"]
        self.service = spec.get("key") or ""
        self.source = (spec.get("provider") or urllib.parse.urlsplit(spec["url"]).hostname or "") + " (catalog" + (", key)" if self.service else ")")
        self.url = spec["url"].replace("{key}", "<key>")

    @property
    def needs_key(self) -> bool:  # type: ignore[override]
        return bool(self.service) and not T.key_for(self.service)

    def fetch(self) -> dict:
        if self.needs_key:
            raise RuntimeError("NeedsKey: paste a free key in Settings > Free sources")
        sp = self.spec
        data = T.cached(self, fill_url(sp["url"], T.key_for(self.service) if self.service else ""), sp["poll_s"] * 0.8, sp["headers"])
        f = sp["fields"]
        if sp["template"] == "list":
            arr = path_get(data, f.get("items", ""))
            if not isinstance(arr, list):
                raise ValueError("items path is not a list")
            rows = [(render_field(f.get("main"), it), render_field(f.get("sub"), it), render_field(f.get("right"), it))
                    for it in arr[:int(f.get("limit") or 12)]]
            rows = [r for r in rows if r[0]]
            if not rows:
                raise ValueError("empty list")
            return {"rows": rows}
        if sp["template"] == "text":
            body = render_field(f.get("body"), data)
            if not body:
                raise ValueError("empty text")
            return {"title": render_field(f.get("title"), data) or sp["short"], "body": body,
                    "attr": render_field(f.get("attribution"), data) or sp.get("attribution", "")}
        big = render_field(f.get("big"), data)
        if not big:
            raise ValueError("no value")
        raw = path_get(data, f["big"]) if "{" not in f["big"] else None
        g = None
        if f.get("gauge_max"):
            try:
                g = float(str(raw if raw is not None else big).replace(",", "").rstrip("%")) / float(f["gauge_max"])
            except ValueError:
                g = None
        return {"big": big, "unit": render_field(f.get("unit"), data), "label": render_field(f.get("label"), data) or sp["short"],
                "sub": render_field(f.get("sub"), data), "gauge": g,
                "extras": [(render_field(a, data) if "{" in a else a.upper(), render_field(b, data)) for a, b in f.get("extras") or []]}


class CatalogText(T.TextScreen):
    def text(self, v):
        return (v.get("title") or self.short, v.get("body") or "", v.get("attr") or "")


class CatalogValue(T.ValueScreen):
    def value(self, v, now):
        return {"big": v["big"], "unit": v.get("unit"), "label": v.get("label"), "sub": v.get("sub"), "col": self.color,
                "gauge": v.get("gauge"), "extras": [(a, b, None) for a, b in v.get("extras") or []]}


class CatalogList(A.ListScreen):
    def items(self, v):
        return [tuple(r) for r in (v or {}).get("rows") or []]


def _feed_factory(spec):
    def make():
        return CatalogFeed(spec)
    make.title = spec["title"]
    return make


def make_screen(spec: dict):
    cls = {"text": CatalogText, "value": CatalogValue, "list": CatalogList}[spec["template"]]
    s = cls()
    s.key, s.short, s.category, s.color = spec["id"], spec["short"], spec["category"], spec["color"]
    s.tag = spec["short"][:6]
    s.feed = _feed_factory(spec)
    s.catalog = True
    s.spec = spec
    return s


# ------------------------------------------------------------------ load / merge / refresh
def _read(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("catalog %s unreadable: %s", p, e)
        return None


def _apply(doc: dict, screens: dict) -> list[str]:
    for c in doc["categories"]:
        CAT.category(c["id"], c["title"], c["order"], c["color"], c["blurb"])
    for k in doc["keys"]:
        T.register_key(k["service"], k["label"], k["signup"], k["fallback"], k["note"])
    added = []
    for spec in doc["sources"]:
        old = screens.get(spec["id"])
        if old is not None and not getattr(old, "catalog", False):
            continue
        screens[spec["id"]] = make_screen(spec)
        CATALOG_KEYS.add(spec["id"])
        CAT.assign(spec["id"], spec["category"])
        if spec["key"]:
            T.KEYS.get(spec["key"], {}).setdefault("used_by", [])
            if spec["id"] not in T.KEYS[spec["key"]]["used_by"]:
                T.KEYS[spec["key"]]["used_by"].append(spec["id"])
        if old is None:
            added.append(spec["id"])
    return added


def builtin_keys(screens: dict) -> set[str]:
    return {k for k, s in screens.items() if not getattr(s, "catalog", False)}


def load_into(screens: dict) -> list[str]:
    """Startup: bundled catalog, then the fetched one on top (same id = fetched wins)."""
    builtin = builtin_keys(screens)
    added, errors = [], []
    for name, p in (("bundled", BUNDLED), ("fetched", user_file())):
        doc = _read(p)
        if doc is None:
            continue
        try:
            clean, errs = validate(doc, builtin)
        except Invalid as e:
            errors.append(f"{name}: {e}")
            continue
        added += _apply(clean, screens)
        errors += [f"{name}: {x}" for x in errs]
        STATE["version"], STATE["source"] = clean["version"], name
    STATE["loaded"] = sorted(CATALOG_KEYS)
    STATE["errors"] = errors[:20]
    return added


def refresh(screens: dict, feeds: dict | None = None, url: str | None = None) -> dict:
    """'Get more free sources': download, validate, save, register. Never raises for a bad
    download: the result says what happened and the current catalog stays."""
    url = url or str(LF._setting("free_sources_url") or "") or CATALOG_URL
    res = {"ok": False, "url": url, "added": [], "updated": 0, "skipped": [], "error": None}
    try:
        if not url.startswith("https://"):
            raise Invalid("catalog URL must be https")
        raw = LF.HTTP(url, {"Accept": "application/json"}, LF.HTTP_TIMEOUT_S)
        if len(raw) > MAX_BYTES:
            raise Invalid("catalog too large")
        doc = json.loads(raw.decode("utf-8"))
        clean, errs = validate(doc, builtin_keys(screens))
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"[:200]
        res["catalog"] = status(screens)
        STATE["last"] = {"at": time.time(), "ok": False, "error": res["error"]}
        return res
    before = set(CATALOG_KEYS)
    try:
        user_file().parent.mkdir(parents=True, exist_ok=True)
        user_file().write_text(json.dumps(doc, indent=1), encoding="utf-8")
    except OSError as e:
        res["error"] = f"could not save: {e}"
    res["added"] = _apply(clean, screens)
    res["updated"] = len([s for s in clean["sources"] if s["id"] in before])
    res["skipped"] = errs
    if feeds is not None:
        for s in clean["sources"]:
            if s["id"] in screens and (s["id"] not in feeds or getattr(feeds[s["id"]], "spec", None) != s):
                feeds[s["id"]] = screens[s["id"]].feed()
    STATE.update(version=clean["version"], source="fetched", fetched_at=time.time(), loaded=sorted(CATALOG_KEYS))
    STATE["last"] = {"at": time.time(), "ok": True, "added": len(res["added"])}
    res["ok"] = res["error"] is None
    res["catalog"] = status(screens)
    return res


def status(screens: dict | None = None) -> dict:
    return {"version": STATE["version"], "source": STATE["source"], "fetched_at": STATE["fetched_at"],
            "count": len(CATALOG_KEYS), "errors": STATE["errors"], "url": str(LF._setting("free_sources_url") or "") or CATALOG_URL,
            "last": STATE["last"], "sources": sorted(CATALOG_KEYS)}


_ = (dt, PLACE)
