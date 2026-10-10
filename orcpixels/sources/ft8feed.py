"""FT8 / FT4 decodes from OrcSDR (Tab5) for the "FT8 / FT4" Live Data screens.

Read-only, two transports (settings `livedata_ft8_*`, edited in the Live tab):
  * HTTP  GET {url}/api/ft8  -> JSON (needs the proposed OrcSDR `claude/ft8-ticker-feed`
          firmware; stock 62afeed answers 404, which `auto` remembers for 10 minutes)
  * serial (USB CDC, set livedata_ft8_port, @115200): the existing read-only console queries
          `FT8 STATUS` and `FT8 DECODES 64` (ORC_FT8_STATUS / ORC_FT8_DECODE lines).
          The port is opened per poll (~0.3 s) with DTR/RTS low (no board reset) and
          closed again, so OrcSDR's own tools can still use it between polls.
Nothing is ever written to the radio except those two query lines; no PAIR/AUTH, no
/api/action.

All eight FT8 screens share ONE Ft8Source (one device query per poll however many
screens refresh). It merges every poll into a session history: dedupes the same decode
seen in overlapping replies, counts repeats of the same message across cycles, finds
place / distance / bearing of each sender (sources/hamgeo.py, offline) and raises alerts
(my call heard, new grid, new DXCC, far DX).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
from collections import OrderedDict, deque

from . import hamgeo as G
from . import livefeeds as LF
from .livefeeds import Feed

# ------------------------------------------------------------------ configuration
CONFIG = {        # settings key -> (default, kind, label)
    "livedata_ft8_source": ("auto", "ft8src", "FT8 decoder source: auto | http | serial | off"),
    "livedata_ft8_url": ("", "url", "FT8 OrcSDR URL (blank = OrcSDR URL setting)"),
    "livedata_ft8_port": ("", "port", "FT8 OrcSDR serial port (e.g. COM5, /dev/ttyACM0)"),
    "livedata_ft8_baud": ("115200", "baud", "FT8 serial baud"),
    "livedata_ft8_mycall": ("", "call", "FT8 my callsign (highlight + alerts)"),
    "livedata_ft8_home_grid": ("", "grid", "FT8 home grid (blank = from home location)"),
    "livedata_ft8_units": ("mi", "units", "FT8 distance units: mi | km"),
}
SOURCES = ("auto", "http", "serial", "off")
PORT_RE = re.compile(r"^(COM\d{1,3}|/dev/[A-Za-z0-9_./-]{1,40})$", re.I)
BAUDS = ("9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600")
DEFAULT_URL = ""        # no OrcSDR unless configured (settings orcsdr_url / livedata_ft8_url)
HTTP_OFF_S = 600.0            # stock firmware 404 -> don't ask again for 10 min
HISTORY_S = 3600.0            # session history kept for top callers / maps
MAX_HISTORY = 1500
FAR_KM = 8000.0               # "far DX" alert threshold
NEW_AFTER_S = 600.0           # new-grid alerts start 10 min into the session (else every grid is new)


def home_grid() -> str:
    """FT8 home grid: the setting, else the 6-char grid of the home location, else ''."""
    g = cfg("livedata_ft8_home_grid")
    if g:
        return g
    h = LF.PLACE.home()
    return G.latlon_grid(*h)[:6] if h else ""


def cfg(key: str) -> str:
    v = LF._setting(key)
    return str(v).strip() if v else CONFIG[key][0]


def validate(key: str, value) -> str:
    """Normalised settings value, or ValueError. Empty = back to the default."""
    if key not in CONFIG:
        raise ValueError(f"unknown setting {key}")
    v = ("" if value is None else str(value)).strip()
    if not v:
        return v
    kind = CONFIG[key][1]
    if kind == "ft8src":
        v = v.lower()
        if v not in SOURCES:
            raise ValueError(f"{key}: one of {', '.join(SOURCES)}")
    elif kind == "url":
        u = urllib.parse.urlparse(v if "://" in v else "http://" + v)
        if u.scheme not in ("http", "https") or not u.netloc or len(v) > 200:
            raise ValueError(f"{key}: must be an http(s) URL or host")
        v = f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"
    elif kind == "port":
        if not PORT_RE.match(v):
            raise ValueError(f"{key}: e.g. COM5 or /dev/ttyACM0")
        v = v.upper() if v.upper().startswith("COM") else v
    elif kind == "baud":
        if v not in BAUDS:
            raise ValueError(f"{key}: one of {', '.join(BAUDS)}")
    elif kind == "call":
        v = v.upper()
        if not G.CALL_RE.match(v):
            raise ValueError(f"{key}: bad callsign")
    elif kind == "grid":
        v = v[:2].upper() + v[2:4] + v[4:6].lower()
        if not G.valid_grid(v):
            raise ValueError(f"{key}: 4 or 6 character Maidenhead grid, e.g. CN85")
    elif kind == "units":
        v = v.lower()
        if v not in ("mi", "km"):
            raise ValueError(f"{key}: mi or km")
    return v


def base_url() -> str:
    u = cfg("livedata_ft8_url") or (LF._setting("orcsdr_url") or DEFAULT_URL)
    return str(u).rstrip("/")


# ------------------------------------------------------------------ parsing
KV_RE = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S*)')
SLOT_S = {"FT8": 15.0, "FT4": 7.5}


def _int(s, default=None):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return default


def _kv(line: str) -> dict:
    out = {}
    for k, v in KV_RE.findall(line):
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out[k] = v
    return out


def norm_decode(d: dict) -> dict | None:
    """Device fields (serial k=v or JSON) -> {utc, mode, hz, dt_ms, snr, sync, kind, call, grid, msg}."""
    msg = " ".join(str(d.get("msg") or d.get("message") or "").upper().split())
    utc = _int(d.get("utc"))
    if not msg or utc is None:
        return None
    snr = d.get("snr")
    snr = None if snr in (None, "-", "", "null") else _int(snr)
    grid = str(d.get("grid") or "").strip().upper()
    call = str(d.get("call") or "").strip().upper().strip("<>")
    if call in ("-", "?"):
        call = ""
    kind = str(d.get("kind") or "").upper() or ("CQ" if msg.startswith("CQ ") else "?")
    return {"utc": utc, "mode": str(d.get("mode") or "FT8").upper(), "hz": _int(d.get("hz"), 0),
            "dt_ms": _int(d.get("dt_ms"), 0), "snr": snr, "sync": _int(d.get("sync"), 0), "kind": kind,
            "call": call, "grid": grid if G.valid_grid(grid) else "", "msg": msg[:48]}


def parse_decode_line(line: str) -> dict | None:
    """One `ORC_FT8_DECODE utc=.. mode=FT8 hz=.. snr=.. kind=CQ call=.. grid=.. msg="..."` line."""
    line = line.strip()
    if not line.startswith("ORC_FT8_DECODE "):
        return None
    return norm_decode(_kv(line[len("ORC_FT8_DECODE "):]))


def parse_status_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("ORC_FT8_STATUS"):
        return None
    kv = _kv(line[len("ORC_FT8_STATUS"):])
    mode = (kv.get("mode") or "FT8").upper()
    return {"mode": mode, "band": kv.get("band") or "", "dial_hz": _int(kv.get("dial_hz")),
            "runtime": kv.get("runtime") or "", "clock_ok": kv.get("clock") == "1",
            "rx_running": kv.get("rx_running") == "1", "slots": _int(kv.get("slots")),
            "last_decodes": _int(kv.get("last_decodes")), "store": _int(kv.get("store")),
            "slot_s": SLOT_S.get(mode, 15.0)}


def parse_serial_reply(text: str) -> tuple[dict | None, list[dict]]:
    """Whole console capture -> (status, decodes newest first). Other log lines are ignored."""
    status, decs = None, []
    for line in text.splitlines():
        if "ORC_FT8_STATUS" in line:
            status = parse_status_line(line[line.index("ORC_FT8_STATUS"):]) or status
        elif "ORC_FT8_DECODE " in line:
            d = parse_decode_line(line[line.index("ORC_FT8_DECODE "):])
            if d:
                decs.append(d)
    return status, decs


def parse_http_json(data) -> tuple[dict, list[dict]]:
    """GET /api/ft8 body (proposed firmware) -> (status, decodes newest first)."""
    j = json.loads(data) if isinstance(data, (bytes, str)) else data
    if not isinstance(j, dict) or not isinstance(j.get("decodes"), list):
        raise ValueError("not an OrcSDR /api/ft8 reply")
    mode = str(j.get("mode") or "FT8").upper()
    slot_ms = _int(j.get("slot_ms"))
    status = {"mode": mode, "band": str(j.get("band") or ""), "dial_hz": _int(j.get("dial_hz")),
              "runtime": str(j.get("state") or ""), "clock_ok": bool(j.get("clock_valid")),
              "rx_running": bool(j.get("rx_running", True)), "slots": _int(j.get("slots")),
              "last_decodes": _int(j.get("last_slot_decodes")), "store": _int(j.get("total")),
              "slot_s": slot_ms / 1000.0 if slot_ms else SLOT_S.get(mode, 15.0)}
    decs = [d for d in (norm_decode(x) for x in j["decodes"] if isinstance(x, dict)) if d]
    decs.sort(key=lambda d: d["utc"], reverse=True)
    return status, decs


# ------------------------------------------------------------------ transports
def _serial_open(port: str, baud: int):
    import serial            # pyserial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, baud, 0.2, 1.0
    s.dtr, s.rts = False, False            # set BEFORE open: no ESP32 auto-reset
    s.open()
    return s


SERIAL_OPEN = _serial_open     # tests replace this


def serial_query(port: str, baud: int, n: int = 64, timeout: float = 6.0) -> str:
    """Ask the console `FT8 STATUS` then `FT8 DECODES n`; return the raw reply text."""
    s = SERIAL_OPEN(port, baud)
    out: list[str] = []
    try:
        try:
            s.reset_input_buffer()
        except Exception:  # noqa: BLE001
            pass
        for cmd, end in ((b"FT8 STATUS\n", "ORC_FT8_STATUS"), (f"FT8 DECODES {n}\n".encode(), "ORC_FT8_DECODES_END")):
            s.write(cmd)
            buf, t0 = b"", time.monotonic()
            while time.monotonic() - t0 < timeout:
                chunk = s.read(4096)
                if chunk:
                    buf += chunk
                    i = buf.rfind(end.encode())
                    if i >= 0 and b"\n" in buf[i:]:          # marker line complete
                        break
            text = buf.decode("utf-8", "replace")
            if end not in text:
                raise TimeoutError(f"{port}: no {end} reply (is it OrcSDR?)")
            out.append(text)
    finally:
        try:
            s.close()
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(out)


# ------------------------------------------------------------------ shared source
def decode_key(d: dict) -> tuple:
    return (d["utc"], d["hz"] // 4, d["msg"])


class Ft8Source:
    """Session store shared by every FT8 screen feed."""

    MIN_GAP_S = 5.0

    def __init__(self):
        self.lock = threading.Lock()
        self.history: "OrderedDict[tuple, dict]" = OrderedDict()
        self.msg_reps: dict[str, int] = {}
        self.alerts: deque = deque(maxlen=16)
        self.seen_grids: set[str] = set()
        self.seen_dxcc: set[str] = set()
        self.http_off_until = 0.0
        self.snapshot: dict | None = None
        self.polled_at = 0.0
        self.status: dict = {}
        self.via = ""
        self.cfg_sig = None
        self.primed = False
        self.started = None                 # first merge (session start)

    def reset(self):
        self.__init__()

    # transport -------------------------------------------------------------
    def query(self, feed: Feed | None = None) -> tuple[dict | None, list[dict], str]:
        src = cfg("livedata_ft8_source").lower()
        if src == "off":
            raise RuntimeError("FT8 source is off (Live tab settings)")
        errs = []
        if src in ("auto", "http") and not base_url():
            errs.append("http: no OrcSDR URL set")
            if src == "http":
                raise RuntimeError(errs[0])
        elif src in ("auto", "http") and (src == "http" or time.time() >= self.http_off_until):
            url = base_url() + "/api/ft8?n=64"
            try:
                if feed is not None:
                    feed.requests += 1
                st, decs = parse_http_json(LF.HTTP(url, {"Accept": "application/json"}, 4.0))
                return st, decs, "http"
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    self.http_off_until = time.time() + HTTP_OFF_S
                errs.append(f"http {e.code}")
            except Exception as e:  # noqa: BLE001
                errs.append(f"http {type(e).__name__}")
                if src == "auto":
                    self.http_off_until = time.time() + 120.0     # unreachable: serial for 2 min
            if src == "http":
                raise RuntimeError("; ".join(errs))
        port = cfg("livedata_ft8_port")
        if not port:
            errs.append("serial: no port set")
            raise RuntimeError("; ".join(errs))
        try:
            text = serial_query(port, int(cfg("livedata_ft8_baud")))
        except Exception as e:  # noqa: BLE001
            errs.append(f"serial {port}: {type(e).__name__}: {e}"[:160])
            raise RuntimeError("; ".join(errs)) from None
        st, decs = parse_serial_reply(text)
        return st, decs, "serial"

    # merge -----------------------------------------------------------------
    def merge(self, status: dict | None, decs: list[dict], via: str, now: float | None = None) -> dict:
        now = now or time.time()
        home = home_grid()
        miles = cfg("livedata_ft8_units") != "km"
        mycall = cfg("livedata_ft8_mycall").upper()
        sig = (home, miles, mycall)
        if sig != self.cfg_sig:                 # settings changed: re-locate everything
            self.cfg_sig = sig
            for d in self.history.values():
                d["loc"] = G.locate(d["call"], d["grid"], home, miles)
                d["mine"] = bool(mycall) and mycall in d["msg"].split()
        if status:
            self.status = status
        self.via = via
        if self.started is None:
            self.started = now
        warm = now - self.started >= NEW_AFTER_S     # "new grid" only means something after a while
        fresh = []
        for d in sorted(decs, key=lambda x: x["utc"]):
            k = decode_key(d)
            if k in self.history:
                continue
            d = dict(d)
            toks = d["msg"].split()
            d["to"] = "" if d["kind"] == "CQ" or not toks else toks[0]
            d["loc"] = G.locate(d["call"], d["grid"], home, miles)
            d["mine"] = bool(mycall) and mycall in toks
            self.msg_reps[d["msg"]] = self.msg_reps.get(d["msg"], 0) + 1
            d["rep"] = self.msg_reps[d["msg"]]
            self.history[k] = d
            fresh.append(d)
        # alerts (not for the very first batch: everything would be "new")
        for d in fresh:
            loc = d["loc"]
            new_grid = bool(d["grid"]) and d["grid"][:4] not in self.seen_grids
            new_dx = bool(loc.get("dxcc")) and loc["dxcc"] not in self.seen_dxcc
            if d["grid"]:
                self.seen_grids.add(d["grid"][:4])
            if loc.get("dxcc"):
                self.seen_dxcc.add(loc["dxcc"])
            if d["rep"] > 1 or (not self.primed and not d["mine"]):
                continue                          # repeats / the first batch never alert
            kind = ("MYCALL" if d["mine"] else "NEWDXCC" if new_dx else
                    "FARDX" if (loc.get("km") or 0) >= FAR_KM and loc.get("src") == "grid" else
                    "NEWGRID" if new_grid and warm else None)
            if kind:
                self.alerts.appendleft({"kind": kind, "at": d["utc"], "d": d})
        self.primed = True
        # trim
        cut = now - HISTORY_S
        for k in [k for k, d in self.history.items() if d["utc"] < cut]:
            del self.history[k]
        while len(self.history) > MAX_HISTORY:
            self.history.popitem(last=False)
        self.snapshot = self.build(now)
        return self.snapshot

    def build(self, now: float) -> dict:
        st = dict(self.status) if self.status else {"mode": "FT8", "band": "", "slot_s": 15.0}
        slot = st.get("slot_s") or SLOT_S.get(st.get("mode", "FT8"), 15.0)
        decs = sorted(self.history.values(), key=lambda d: (d["utc"], d["snr"] if d["snr"] is not None else -99),
                      reverse=True)          # newest cycle first, strongest first within it
        latest = decs[0]["utc"] if decs else None
        # per-cycle counts, zero-filled back from the latest slot (or now)
        counts: dict[int, int] = {}
        for d in decs:
            counts[d["utc"]] = counts.get(d["utc"], 0) + 1
        top_slot = int(max(latest or 0, (now - slot) // slot * slot))
        cycles = [(int(top_slot - i * slot), counts.get(int(top_slot - i * slot), 0)) for i in range(20)]
        last_slot = [d for d in decs if d["utc"] == latest] if latest is not None else []
        # ticker: newest first, same message only once (latest copy), last 4 cycles
        tick, seen = [], set()
        for d in decs:
            if latest is None or d["utc"] < latest - 4 * slot:
                break
            if d["msg"] in seen:
                continue
            seen.add(d["msg"])
            tick.append(d)
        idle = latest is None or now - (latest + slot) > 3 * slot
        calls: dict[str, dict] = {}
        for d in decs:
            if not d["call"]:
                continue
            c = calls.setdefault(d["call"], {"call": d["call"], "n": 0, "best": None, "last": d["utc"], "d": d})
            c["n"] += 1
            if d["snr"] is not None and (c["best"] is None or d["snr"] > c["best"]):
                c["best"] = d["snr"]
        located = {}
        for d in decs:
            if d["call"] and d["loc"].get("lat") is not None and d["call"] not in located:
                located[d["call"]] = d
        far = sorted(located.values(), key=lambda d: d["loc"].get("km") or 0, reverse=True)
        cq, seen_cq = [], set()
        for d in decs:
            if d["kind"] == "CQ" and d["call"] not in seen_cq and d["utc"] >= (latest or 0) - 600:
                seen_cq.add(d["call"])
                cq.append(d)
        return {"status": st, "slot_s": slot, "via": self.via, "fetched": now, "latest": latest,
                "idle": idle, "ticker": tick[:14], "last_slot": last_slot, "cycles": cycles,
                "decodes": decs[:400], "cq": cq[:12], "calls": sorted(calls.values(), key=lambda c: (-c["n"], -(c["best"] if c["best"] is not None else -99)))[:20],
                "far": far[:20], "located": list(located.values())[:120], "alerts": list(self.alerts)[:8],
                "grids": len(self.seen_grids), "dxcc": len(self.seen_dxcc), "unique": len(calls),
                "mycall": cfg("livedata_ft8_mycall").upper(), "home": home_grid()}

    # poll ------------------------------------------------------------------
    def poll(self, feed: Feed | None = None) -> dict:
        with self.lock:
            if self.snapshot is not None and time.time() - self.polled_at < self.MIN_GAP_S:
                return self.snapshot
            st, decs, via = self.query(feed)
            self.polled_at = time.time()
            return self.merge(st, decs, via)


SOURCE = Ft8Source()


class Ft8Feed(Feed):
    """One per FT8 screen; they all read the shared SOURCE (one device query per poll)."""
    key, title, source = "ft8", "FT8 / FT4 decodes", "OrcSDR Tab5 (USB serial / GET /api/ft8)"
    url = "serial FT8 DECODES / GET /api/ft8 (read-only)"
    poll_s = 15.0            # one FT8 cycle; FT4 cycles are faster but 15 s keeps the port free
    local = True             # LAN / USB device, no internet

    @property
    def stale_after(self) -> float:
        return 120.0

    @property
    def max_stale(self) -> float:
        return 1800.0

    def retry_s(self) -> float:
        return min(60.0, 15.0 * 2 ** max(0, self.fails - 1))

    def fetch(self) -> dict:
        return SOURCE.poll(self)


def feed_class(key: str, title: str) -> type:
    """Per-screen Ft8Feed subclass (own key / title in the Live tab; same shared SOURCE)."""
    return type(f"Ft8Feed_{key}", (Ft8Feed,), {"key": key, "title": title})
