"""Tiny JSON-over-HTTP helper for drivers (stdlib only, run in a worker thread)."""
from __future__ import annotations

import json
import urllib.request


def request(url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 5.0,
            method: str | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    h = {"User-Agent": "OrcPixels", **({"Content-Type": "application/json"} if body is not None else {}),
         **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=method or ("POST" if body is not None else "GET"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"raw": raw.decode("utf-8", "replace")}
