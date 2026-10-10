"""Send/activity ring buffer for the web UI's send log + toasts (Task B, 2026-09-30).

Every BLE command that goes through SignClient.run() / send_live_png() becomes one entry
(id, wall time, what, bytes, status). Consecutive OrcSDR Live frames are folded into one
updating "live" entry so the log isn't flooded at ~1 fps. Poll with since=<last id>;
updated entries get a new `rev` so pollers can pick up changed rows too.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import itertools
import threading
import time
from collections import deque

LABEL: contextvars.ContextVar[str | None] = contextvars.ContextVar("orcpixels_send_label", default=None)

# housekeeping commands (protocol.Command.name): hidden from the send log by default, no toast
CONTROL = {"device_info", "orientation", "set_time"}


@contextlib.contextmanager
def labelled(what: str):
    """Name the sends made inside this block (e.g. 'preset p_rf_adsb')."""
    tok = LABEL.set(what)
    try:
        yield
    finally:
        LABEL.reset(tok)


def _pt(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


class Activity:
    def __init__(self, maxlen: int = 200):
        self.items: deque[dict] = deque(maxlen=maxlen)
        self._ids = itertools.count(1)
        self._rev = itertools.count(1)
        self._lock = threading.Lock()
        self.rev = 0

    def _bump(self, e: dict):
        self.rev = e["rev"] = next(self._rev)

    def start(self, cmd: str, nbytes: int | None = None, what: str | None = None,
              kind: str | None = None) -> dict:
        now = time.time()
        what = what or LABEL.get() or cmd
        e = {"id": next(self._ids), "t": now, "pt": _pt(now), "cmd": cmd, "what": what,
             "kind": kind or ("control" if cmd in CONTROL else "content"),
             "bytes": nbytes, "status": "sending", "reply": None, "error": None,
             "ms": None, "count": 1, "last_t": now}
        with self._lock:
            self._bump(e)
            self.items.append(e)
        return e

    def finish(self, e: dict, status: str, reply: bytes | None = None, error: str | None = None):
        now = time.time()
        with self._lock:
            e["status"] = status
            e["reply"] = reply.hex() if reply else None
            e["error"] = error
            e["ms"] = round((now - e["t"]) * 1000)
            e["last_t"], e["last_pt"] = now, _pt(now)
            self._bump(e)

    def mark(self, e: dict, status: str):
        with self._lock:
            e["status"] = status
            self._bump(e)

    def live_frame(self, nbytes: int, ok: bool, reply: bytes | None = None,
                   error: str | None = None, fold_s: float = 10.0):
        """Fold successful consecutive Live frames into one entry."""
        now = time.time()
        with self._lock:
            last = self.items[-1] if self.items else None
            if (ok and last is not None and last["kind"] == "live" and last["status"] == "acked"
                    and now - last["last_t"] < fold_s):
                last["count"] += 1
                last["bytes"] = (last["bytes"] or 0) + nbytes
                last["last_t"], last["last_pt"] = now, _pt(now)
                last["reply"] = reply.hex() if reply else last["reply"]
                last["what"] = f"OrcSDR Live x{last['count']} frames"
                self._bump(last)
                return last
        e = self.start("send_png(live)", nbytes, what="OrcSDR Live frame", kind="live")
        self.finish(e, "acked" if ok else "failed", reply, error)
        return e

    def since(self, rev: int = 0, limit: int = 50) -> list[dict]:
        with self._lock:
            out = [dict(e) for e in self.items if e["rev"] > rev]
        return out[-limit:]

    def recent(self, n: int = 20, content_only: bool = False) -> list[dict]:
        with self._lock:
            items = [dict(e) for e in self.items if not content_only or e["kind"] != "control"]
        return items[-n:]
