"""Read-only client for the OrcSDR companion web console (Tab5 "Mission Control").

Discovery notes (firmware apps/orcsdr-tab5/ui/web_console.cpp, verified live 2026-09-29):
  GET /api/status    JSON, ~1.1 KB. Device refreshes its snapshot every 400 ms.
                     Fields used here: mode, frequency_hz, span_hz, signal_dbfs,
                     receiving, rtl_ready, program_service, spectrum[64] (uint8).
  GET /api/spectrum  64 raw bytes (uint8 0..255), fresher than status.spectrum
                     (~280 ms between distinct frames). Polling it tells the device
                     to keep computing its spectrum for 1 s (extra CPU, no state change).
  Spectrum scaling (device side): (level - (mean - 4 dB)) / 24 dB -> 0..1 -> 0..255,
  i.e. relative to the noise floor over a 24 dB window, centred on frequency_hz,
  covering span_hz. Tuned channel = bins 31/32.
  No auth. httpd max_open_sockets=5 (about 2 clients), LRU purge.

STRICTLY READ-ONLY: this module only ever issues HTTP GET. It never calls
/api/action (the only mutating route) or the audio endpoints.
Standard library only (urllib), run in a worker thread from async code.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from dataclasses import dataclass, field

DEFAULT_URL = ""        # set orcsdr_url in Settings (e.g. http://orcsdr.local)
BINS = 64


@dataclass
class OrcSDRStatus:
    mode: str = ""
    frequency_hz: int = 0
    span_hz: int = 0
    signal_dbfs: float | None = None
    receiving: bool = False
    rtl_ready: bool = False
    program_service: str = ""
    spectrum: list[int] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    fetched_at: float = 0.0

    @classmethod
    def from_json(cls, j: dict) -> "OrcSDRStatus":
        sig = j.get("signal_dbfs")
        return cls(
            mode=str(j.get("mode") or ""),
            frequency_hz=int(j.get("frequency_hz") or 0),
            span_hz=int(j.get("span_hz") or 0),
            signal_dbfs=float(sig) if isinstance(sig, (int, float)) else None,
            receiving=bool(j.get("receiving")),
            rtl_ready=bool(j.get("rtl_ready")),
            program_service=str(j.get("program_service") or "").strip(),
            spectrum=[int(v) for v in (j.get("spectrum") or [])],
            raw=j, fetched_at=time.time())

    def summary(self) -> dict:
        return {"mode": self.mode, "frequency_hz": self.frequency_hz, "span_hz": self.span_hz,
                "signal_dbfs": self.signal_dbfs, "receiving": self.receiving,
                "rtl_ready": self.rtl_ready, "program_service": self.program_service,
                "fetched_at": self.fetched_at}


def downsample(bins, n: int = BINS, vmax: float = 255.0) -> list[float]:
    """Max-pool (or nearest-upsample) any-length bin list to n values in 0..1."""
    m = len(bins)
    if m == 0:
        return [0.0] * n
    out = []
    for c in range(n):
        a = c * m // n
        b = max(a + 1, (c + 1) * m // n)
        v = max(bins[a:b])
        out.append(max(0.0, min(1.0, v / vmax)))
    return out


class OrcSDRClient:
    """GET-only HTTP client. All methods are async (urllib in a thread)."""

    def __init__(self, base_url: str = DEFAULT_URL, timeout: float = 1.5):
        # short: OrcSDR often drops off the network; Live's watchdog counts timeouts
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> bytes:
        if not self.base_url:
            raise ConnectionError("OrcSDR URL not set (Settings > OrcSDR)")
        req = urllib.request.Request(self.base_url + path, method="GET",
                                     headers={"Cache-Control": "no-store",
                                              "User-Agent": "OrcPixels/orcsdr-live"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    async def status(self) -> OrcSDRStatus:
        data = await asyncio.to_thread(self._get, "/api/status")
        return OrcSDRStatus.from_json(json.loads(data.decode("utf-8", "replace")))

    async def spectrum_raw(self) -> list[int]:
        """64 uint8 bins from /api/spectrum (0..255)."""
        return list(await asyncio.to_thread(self._get, "/api/spectrum"))

    async def spectrum(self, n: int = BINS) -> list[float]:
        return downsample(await self.spectrum_raw(), n)
