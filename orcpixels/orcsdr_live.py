"""OrcSDR Live: turn read-only OrcSDR telemetry into 64x16 frames on the sign.

Layout (upright; SignClient applies rotate_180 from config/settings.json at send time):
  rows 0-4    tiny readout: "<freq> <MODE>" in lime, signal dBFS right-aligned in cyan
  rows 6-15   panadapter: 64 bars (cyan body, lime tip), tuned-centre tick
  waterfall=True: rows 6-10 panadapter, rows 11-15 scrolling waterfall (newest on top)

Data comes from orcpixels.sources.orcsdr (HTTP GET only). A sampler task polls
the device (status ~1 Hz, spectrum ~2 Hz) and keeps smoothed bins + waterfall
history; a sender task pushes the latest render to the sign as a small static PNG
as fast as BLE allows but no faster than `interval` seconds. A watchdog (see
"controller" below) falls back to the on-device playlist when OrcSDR drops and resumes
Live by itself when it answers again.

Frame rate (real sign, 2026-09-30; tools/soak_live.py, tools/bench_live_ble.py):
  User-approved look (08:47 PT): spectrum + waterfall layout above, frame = indexed PNG
  (<=16 colours, zlib 9, ~470 B), sent back-to-back from tools/bench_live_ble.py with
  360-500 B chunks, write-WITH-response (~1.7-1.9 fps, fetch-bound in that bench loop).
  Default now ("fast"): same frames, 244-B chunks write-WITHOUT-response, wait only for
  the final 05 00 02 00 03 ack, sampler/sender pipelined, interval floor 0:
  waterfall 3.8-4.1 fps (~230 ms/frame, 490 frames / 130 s, 0 failures, 0 reconnects),
  spectrum-only ~116 ms/frame (4.3 fps, capped by the ~4 Hz spectrum source).
  "legacy" (RGB PNG, 244-B write-with-response): waterfall 1.0 fps, spectrum 2.4 fps.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
from collections import deque

from PIL import Image

from . import settings as S
from .sources.orcsdr import BINS, DEFAULT_URL, OrcSDRClient, OrcSDRStatus, downsample
from .live_codec import png_palette, png_rgb
from .tinyfont import draw_text, text_width

log = logging.getLogger("orcpixels.orcsdr")
W, H = 64, 16

# brand colours (OrcSDR Mission Control page / projects.json accent)
LIME = (140, 252, 40)
LIME_DIM = (40, 90, 10)
LIME_MID = (90, 170, 30)
CYAN = (0, 188, 255)
CYAN_DIM = (0, 55, 85)
BLACK = (0, 0, 0)
GREY = (70, 80, 90)

SETTING_URL = "orcsdr_url"
SETTING_WATERFALL = "orcsdr_waterfall"
SETTING_INTERVAL = "orcsdr_interval"
SETTING_TRANSPORT = "orcsdr_transport"
SETTING_ARMED = "orcsdr_live_armed"     # Live comes back on its own after a server restart
# 0 = as fast as the sign acks (the interval is only a floor between frame starts)
DEFAULT_INTERVAL = 0.0
DEFAULT_WATERFALL = True          # spectrum + waterfall combo (user's pick 2026-09-30)
# "fast": indexed PNG (<=16 colours, zlib 9) written in 244-B chunks WITHOUT response,
#         then wait only for the final 05 00 02 00 03 ack (see SignClient.send_live_png).
# "legacy": SignClient.send_image (RGB PNG, 244-B write-with-response chunks).
# 2026-10-02 22:02-22:17 PT real-sign finding: "fast" frames are acked (05 00 02 00 03) but
# this sign freezes the running program and ends on its Bluetooth screen without ever
# drawing them (OrcSDR Live and Live Data alike); "legacy" frames display. Default legacy;
# the fast variants stay selectable for diagnosis.
DEFAULT_TRANSPORT = "legacy"
TRANSPORTS = {
    "legacy": "legacy: RGB PNG, every chunk acked (~1 fps, displays on this sign)",
    "fast": "fast: experimental, may freeze on this sign (palette PNG, unacked chunks)",
    "fast-rgb": "fast-rgb: experimental, may freeze (RGB PNG, unacked chunks)",
    "fast-acked": "fast-acked: experimental, may freeze (palette PNG, acked chunks)",
    # 22:26-22:38 PT eyes-on: fast-rgb AND fast-acked froze the sign too (power cycle
    # needed). tools/live_wire_capture.py: legacy = RGB PNG + 244-B write-WITH-response;
    # fast-rgb differs from legacy only in write type, fast-acked only in PNG flavour, so
    # on this sign today BOTH the palette PNG and unacked writes break it. fast-v2 puts
    # byte-identical writes on the air (RGB PNG, 244-B chunks, all acked); it only skips
    # legacy's double PNG encode/decode and uses the race-free Live frame path.
    "fast-v2": "fast-v2: experimental (legacy bytes on air, leaner path); untested on the sign",
}
# transports whose PNG is RGB (Pillow defaults, = SignClient.send_image) / acked chunks
RGB_TRANSPORTS = {"fast-rgb", "fast-v2"}
ACKED_TRANSPORTS = {"fast-acked", "fast-v2"}
RESTORE_SETTLE_S = 1.0            # let the last live frame finish drawing before 0x8008
RESTORE_REPEAT_S = 1.5            # ... and send 0x8008 once more (7 bytes) in case it was eaten
FAST_CHUNK = 244
SPECTRUM_POLL_S = 0.25            # OrcSDR produces a new /api/spectrum frame every ~280 ms
HISTORY_ROW_S = 0.5               # waterfall scroll speed (rows/s = 2), unchanged look
STATUS_EVERY = 4                  # status GET every 4th spectrum poll (~1 s)


def norm_transport(t) -> str:
    return t if t in TRANSPORTS else DEFAULT_TRANSPORT


def device_url() -> str:
    return str(S.load().get(SETTING_URL) or DEFAULT_URL).rstrip("/")


# ----------------------------------------------------------------- rendering
def fmt_freq(hz: int) -> str:
    """96100000 -> '96.1', 162550000 -> '162.55', 1090000000 -> '1090', 7200000 -> '7.2'."""
    if not hz:
        return "---"
    if hz < 1_000_000:
        return f"{hz / 1e3:.0f}K"
    mhz = hz / 1e6
    for dec in (3, 2, 1, 0):
        s = f"{mhz:.{dec}f}"
        if "." in s:
            s = s.rstrip("0")
            if s.endswith("."):
                s += "0"
        if text_width(s) <= 30:
            return s
    return f"{mhz:.0f}"


def short_mode(mode: str) -> str:
    m = (mode or "").lower()
    return {"shortwave": "SW", "weather": "WX", "airband": "AIR", "adsb": "ADSB",
            "rf_lab": "LAB", "satellite": "SAT", "marine": "MAR"}.get(m, (mode or "").upper()[:4])


def _pal(v: float):
    """Waterfall palette: black -> deep blue -> cyan -> lime."""
    v = max(0.0, min(1.0, v))
    stops = [(0.0, (0, 0, 0)), (0.25, (0, 20, 60)), (0.55, CYAN), (1.0, LIME)]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if v <= t1:
            f = (v - t0) / (t1 - t0)
            return tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
    return LIME


def render_frame(status: OrcSDRStatus | None, bins, peaks=None, history=None,
                 waterfall: bool = False, error: str | None = None) -> Image.Image:
    img = Image.new("RGB", (W, H), BLACK)
    px = img.load()
    if status is None:
        draw_text(img, 1, 0, "ORCSDR", LIME)
        draw_text(img, 1, 8, "OFFLINE" if error else "WAIT", GREY)
        return img
    # readout
    live = status.receiving and status.rtl_ready
    x = draw_text(img, 0, 0, fmt_freq(status.frequency_hz), LIME if live else GREY)
    draw_text(img, x + 2, 0, short_mode(status.mode), LIME_MID if live else GREY)
    if status.signal_dbfs is not None:
        sig = f"{status.signal_dbfs:.0f}"
        draw_text(img, W - text_width(sig), 0, sig, CYAN)
    # panadapter
    top = 6
    bottom = 10 if waterfall else 15
    rows = bottom - top + 1
    b = list(bins or [0.0] * BINS)[:W]
    for i, v in enumerate(b):
        h = int(round(max(0.0, min(1.0, v)) * rows))
        for r in range(h):
            px[i, bottom - r] = CYAN_DIM if r < h - 1 else LIME
        if peaks is not None and i < len(peaks):
            ph = int(round(max(0.0, min(1.0, peaks[i])) * rows))
            if ph > h and ph >= 1:
                px[i, bottom - ph + 1] = CYAN
    # tuned-centre tick (bins 31/32 straddle the tuned frequency)
    px[31, top - 1] = LIME_DIM
    px[32, top - 1] = LIME_DIM
    if waterfall:
        hist = list(history or [])
        for r in range(5):
            if r >= len(hist):
                break
            row = hist[-1 - r]
            for i in range(min(W, len(row))):
                px[i, 11 + r] = _pal(row[i])
    return img


def render_frame64(status: OrcSDRStatus | None, bins, peaks=None, history=None,
                   waterfall: bool = True, error: str | None = None) -> Image.Image:
    """OrcSDR Live on the 64x64 matrix: frequency in 2x type, mode + signal, a 64-bin
    panadapter (rows 26-43) with peak hold and the waterfall below it (rows 45-63, newest
    on top). waterfall=False gives the panadapter the whole lower half."""
    from .live64 import RT, T, U, U2, header, u2w
    img = Image.new("RGB", (64, 64), BLACK)
    px = img.load()
    if status is None:
        header(img, "ORCSDR", LIME)
        U(img, 4, 20, "OFFLINE" if error else "WAIT", GREY)
        if error:
            T(img, 0, 44, str(error).upper()[:16], GREY)
        return img
    live = status.receiving and status.rtl_ready
    header(img, "ORCSDR " + short_mode(status.mode), LIME if live else GREY,
           f"{status.signal_dbfs:.0f}DB" if status.signal_dbfs is not None else None, CYAN)
    f = fmt_freq(status.frequency_hz)
    fc = LIME if live else GREY
    if u2w(f) <= 64:
        x = U2(img, 0, 6, f, fc)
    else:
        x = U(img, 0, 10, f, fc)
    if x <= 52:
        T(img, x + 1, 26 - 6, "MHZ" if status.frequency_hz >= 1_000_000 else "", LIME_DIM)
    # panadapter
    top = 27
    bottom = 43 if waterfall else 62
    rows = bottom - top + 1
    b = list(bins or [0.0] * BINS)[:64]
    for i, v in enumerate(b):
        h = int(round(max(0.0, min(1.0, v)) * rows))
        for r in range(h):
            px[i, bottom - r] = CYAN_DIM if r < h - 1 else LIME
        if peaks is not None and i < len(peaks):
            ph = int(round(max(0.0, min(1.0, peaks[i])) * rows))
            if ph > h and ph >= 1:
                px[i, bottom - ph + 1] = CYAN
    px[31, top - 1] = LIME_DIM
    px[32, top - 1] = LIME_DIM
    if waterfall:
        hist = list(history or [])
        for r in range(19):
            if r >= len(hist):
                break
            row = hist[-1 - r]
            for i in range(min(64, len(row))):
                px[i, 45 + r] = _pal(row[i])
    _ = RT
    return img


def png_bytes(img: Image.Image, scale: int = 1) -> bytes:
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------- controller
# Watchdog (2026-09-30). OrcSDR is under active development and drops off the network
# often. Before this, an outage left the sign parked on the last ("ORCSDR OFFLINE")
# frame: Live stayed enabled, nothing re-started the on-device playlist, and the only
# thing bounding a stuck BLE write was luck. Now, while Live is ARMED, a supervisor
# task owns the streaming tasks and runs this state machine:
#   streaming -> LOSS_FAILS consecutive failed/timed-out polls (and >= LOSS_MIN_S since
#                the last good poll) -> stop streaming (the in-flight frame is allowed to
#                finish), restart the last on-device playlist (0x8008 play order;
#                fallback: OrcSDR default preset) -> waiting
#   waiting   -> probe GET /api/status every PROBE_S..PROBE_MAX_S (backoff); after
#                RESUME_OKS successes in a row -> streaming again
# Live stays armed until the user stops it (which leaves the playlist playing) or
# another display mode takes over the sign (preset/text/image, playlist, Auto status).
# Still read-only: the only requests are GET /api/status and GET /api/spectrum.
HTTP_TIMEOUT_S = 1.5              # per GET (was 3 s)
LOSS_FAILS = 3
LOSS_MIN_S = 2.5
PROBE_S = 3.0
PROBE_MAX_S = 5.0
CONFIRM_S = 1.0                   # spacing of the confirming probe(s) once one succeeds
RESUME_OKS = 2
LIVE_ACK_TIMEOUT_S = 3.0          # a live frame normally acks in ~0.1-0.25 s
LIVE_SEND_TIMEOUT_S = 6.0         # write + final ack of one frame (reconnect excluded)
LIVE_CONNECT_TIMEOUT_S = 45.0     # BLE (re)connect before a frame
FALLBACK_TIMEOUT_S = 60.0         # playlist / preset restore
TEST_OUTAGE_URL = "http://192.0.2.1"   # RFC 5737 TEST-NET-1, never answers (test hook)


class OrcSDRLive:
    """Owns the watchdog (supervisor) + sampler + sender tasks. Only one per web server.
    orcpixels.livedata.DataLive reuses this engine for the Live Data screens; `peers` are
    the other live engines (one live source at a time: starting one stops the others)."""

    NAME = "OrcSDR Live"
    TRANSPORT_KEY = SETTING_TRANSPORT

    def __init__(self, sign, auto=None, client=None, fallback=None, persist: bool = False):
        self.sign, self.auto = sign, auto
        self.peers: list = []
        self.persist = persist           # save armed state + settings to config/settings.json
        self.ble_paused = False          # sign link down: sender idles until it is back
        self.client = client or OrcSDRClient(device_url(), timeout=HTTP_TIMEOUT_S)
        # async () -> str ("playlist test3" / "preset x"); None -> restore_fallback()
        self.fallback = fallback
        self.send_failures = 0
        self.status: OrcSDRStatus | None = None
        self.bins = [0.0] * BINS
        self.peaks = [0.0] * BINS
        self.history: deque = deque(maxlen=24)        # 64x64 waterfall shows 19 rows
        cfg = S.load()
        self.waterfall = bool(cfg.get(SETTING_WATERFALL, DEFAULT_WATERFALL))
        self.interval = float(cfg.get(SETTING_INTERVAL, DEFAULT_INTERVAL))
        self.transport = norm_transport(cfg.get(self.TRANSPORT_KEY))
        self.restore_settle_s, self.restore_repeat_s = RESTORE_SETTLE_S, RESTORE_REPEAT_S
        self._last_row_t = 0.0
        self.bytes_sent: deque = deque(maxlen=20)
        self.device_error: str | None = None
        self.send_error: str | None = None
        self.stopped_reason: str | None = None
        self.stopped_at: float | None = None      # wall clock of the last disarm
        self.started_wall: float | None = None
        self._tasks: list[asyncio.Task] = []
        self._supervisor: asyncio.Task | None = None
        self._sent_frame = None
        self._last_bytes: bytes | None = None
        self.frames_sent = 0
        self.send_ms: deque = deque(maxlen=20)
        self.sent_at: deque = deque(maxlen=20)
        self.started_at: float | None = None
        # watchdog state
        self.state = "off"                   # off | streaming | waiting
        self.url_override: str | None = None  # test hook (not persisted)
        self.fail_streak = 0
        self.ok_streak = 0
        self.last_ok_t: float | None = None
        self.outages = 0
        self.resumes = 0
        self.showing: str | None = None       # what the sign shows while waiting
        self.fallback_error: str | None = None
        self.lost_at: float | None = None     # wall clock
        self.last_probe_at: float | None = None
        self.supervisor_error: str | None = None
        # True while the sign shows the fallback (not Live frames): a loss right after a
        # resume (before any Live frame went out) must not restart the playlist again
        self._fallback_active = False
        self._lost = asyncio.Event()
        self._takeover = asyncio.Event()
        self._halt = asyncio.Event()
        # tunables (unit tests shrink them)
        self.poll_s = SPECTRUM_POLL_S
        self.loss_fails, self.loss_min_s = LOSS_FAILS, LOSS_MIN_S
        self.probe_s, self.probe_max_s = PROBE_S, PROBE_MAX_S
        self.confirm_s, self.resume_oks = CONFIRM_S, RESUME_OKS
        self.http_timeout_s = HTTP_TIMEOUT_S
        self.ack_timeout_s = LIVE_ACK_TIMEOUT_S
        self.send_timeout_s = LIVE_SEND_TIMEOUT_S
        self.connect_timeout_s = LIVE_CONNECT_TIMEOUT_S
        self.fallback_timeout_s = FALLBACK_TIMEOUT_S

    def url(self) -> str:
        return self.url_override or device_url()

    # ---- sampling
    def _push_bins(self, raw):
        new = downsample(raw, BINS)
        self.bins = [o * 0.55 + n * 0.45 for o, n in zip(self.bins, new)]
        self.peaks = [max(p * 0.9, b) for p, b in zip(self.peaks, self.bins)]
        now = time.monotonic()
        if now - self._last_row_t >= HISTORY_ROW_S * 0.95:
            self._last_row_t = now
            self.history.append(new)

    async def _get(self, coro):
        """Bound every GET, even if the worker thread's socket timeout misbehaves."""
        return await asyncio.wait_for(coro, self.http_timeout_s * 2 + 0.5)

    def _poll_ok(self):
        self.fail_streak = 0
        self.last_ok_t = time.monotonic()
        self.device_error = None

    def _poll_failed(self, e: BaseException):
        self.fail_streak += 1
        self.device_error = f"{type(e).__name__}: {e}"
        quiet = self.last_ok_t is None or time.monotonic() - self.last_ok_t >= self.loss_min_s
        if self.state == "streaming" and self.fail_streak >= self.loss_fails and quiet:
            self._lost.set()

    async def sample_once(self, spectrum: bool = True):
        self.client.base_url = self.url()
        try:
            self.status = await self._get(self.client.status())
            if spectrum:
                self._push_bins(await self._get(self.client.spectrum_raw()))
            elif self.status.spectrum:
                self._push_bins(self.status.spectrum)
            self._poll_ok()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self._poll_failed(e)
            self.status = None
        return self.status

    async def _sampler(self):
        """Runs concurrently with the sender (pipelining): the sender always renders the
        newest state and never queues stale frames. Every poll feeds the watchdog."""
        n = 0
        while True:
            t0 = time.monotonic()
            self.client.base_url = self.url()
            try:
                if n % STATUS_EVERY == 0:
                    self.status = await self._get(self.client.status())
                self._push_bins(await self._get(self.client.spectrum_raw()))
                self._poll_ok()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._poll_failed(e)
                if n % STATUS_EVERY == 0:
                    self.status = None
            n += 1
            await asyncio.sleep(max(0.02, self.poll_s - (time.monotonic() - t0)))

    def size(self) -> tuple[int, int]:
        st = getattr(self.sign, "state", None)
        try:
            return (int(st.width), int(st.height)) if st is not None else (W, H)
        except Exception:  # noqa: BLE001
            return (W, H)

    def render(self) -> Image.Image:
        fn = render_frame64 if self.size() == (64, 64) else render_frame
        return fn(self.status, self.bins, self.peaks, self.history,
                  self.waterfall, self.device_error)

    # ---- sending
    async def _sleep_or_halt(self, s: float):
        try:
            await asyncio.wait_for(self._halt.wait(), max(0.0, s))
        except (TimeoutError, asyncio.TimeoutError):
            pass

    async def _drop_link(self, reason: str):
        """Force a fresh link WITHOUT telling the sign we want to stay disconnected
        (SignClient.disconnect() is a user intent and would stop the keeper)."""
        if hasattr(self.sign, "abort_link"):
            if self.sign.connected:
                self.sign.abort_link(reason)
            return
        try:
            await asyncio.wait_for(self.sign.disconnect(), 10)
        except Exception:  # noqa: BLE001
            pass

    async def _hard(self, coro, timeout: float, what: str):
        """Deadline that doesn't wait for cancellation to be honoured (SignClient.bounded)."""
        if hasattr(self.sign, "bounded"):
            return await self.sign.bounded(coro, timeout, what)
        return await asyncio.wait_for(coro, timeout)

    async def _send_frame(self, img: Image.Image):
        """One frame, every step time-bounded so a lost ack / dead link can't wedge us.
        SignClient drops the link on any send error; the next frame reconnects here."""
        if not self.sign.connected:           # unsupervised sign (CLI/tests): connect here
            await asyncio.wait_for(self.sign.connect(), self.connect_timeout_s)
        if self.transport == "legacy":
            from .activity import labelled
            with labelled("OrcSDR Live frame (legacy)"):
                await self._hard(self.sign.send_image(img),
                                 self.send_timeout_s + self.connect_timeout_s, "live send")
            self.bytes_sent.append(None)
            if hasattr(self.sign, "display_seq"):      # our own send_image bumped it
                self._seq = self.sign.display_seq
            self._mark_showing()
            return
        panel = img.rotate(180) if self.sign.rotate_180 else img
        png = png_rgb(panel) if self.transport in RGB_TRANSPORTS else png_palette(panel)
        # outer bound also covers a rare orientation command / reconnect inside
        seq_kw = {"expect_seq": self._seq} if hasattr(self.sign, "display_seq") else {}
        await self._hard(
            self.sign.send_live_png(png, img, chunk=FAST_CHUNK, response=self.transport in ACKED_TRANSPORTS,
                                    ack_timeout=self.ack_timeout_s, timeout=self.send_timeout_s,
                                    **seq_kw),
            self.send_timeout_s + self.connect_timeout_s, "live send")
        if seq_kw:
            self._seq = self.sign.live_seq
        self.bytes_sent.append(len(png))
        self._mark_showing()

    def showing_name(self) -> str:
        return self.NAME

    def _mark_showing(self):
        """Keep sign.showing naming the live engine that owns the sign (the dashboard's
        "now showing" and takeover reasons read it)."""
        sh = getattr(self.sign, "showing", None) or {}
        name = self.showing_name()
        if hasattr(self.sign, "set_showing") and (sh.get("kind") != "live" or sh.get("name") != name):
            self.sign.set_showing("live", name)

    async def _sender(self):
        fails = 0
        while not self._halt.is_set():
            t0 = time.monotonic()
            # sign link down and the SignClient keeper is reconnecting: idle, don't spin
            if getattr(self.sign, "supervised", False) and not self.sign.connected:
                if not self.ble_paused:
                    log.info("orcsdr live: sign link down; pausing until it is back")
                self.ble_paused = True
                await self._sleep_or_halt(0.25)
                continue
            if self.ble_paused:
                log.info("orcsdr live: sign link back; resuming frames")
                self.ble_paused = False
                self._last_bytes = None                  # re-send even an unchanged frame
            # stop if someone else (preset/text/image button) took over the sign
            if self._taken_over():
                self.stopped_reason = self._takeover_reason()
                log.info("orcsdr live: %s; stopping", self.stopped_reason)
                self._takeover.set()
                return
            img = self.render()
            key = img.tobytes()
            if key != self._last_bytes:
                try:
                    try:
                        await self._send_frame(img)
                    except Exception as e:  # noqa: BLE001
                        if type(e).__name__ != "TakenOver":
                            raise
                        self.stopped_reason = self._takeover_reason()
                        log.info("orcsdr live: %s (frame dropped); stopping", self.stopped_reason)
                        self._takeover.set()
                        return
                    self._sent_frame = self.sign.last_frame
                    self._last_bytes = key
                    self.frames_sent += 1
                    self.send_ms.append((time.monotonic() - t0) * 1000)
                    self.sent_at.append(time.monotonic())
                    self.send_error = None
                    self._fallback_active = False
                    fails = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    fails += 1
                    self.send_error = f"{type(e).__name__}: {e}"
                    self.send_failures += 1
                    log.warning("orcsdr live send (#%d in a row): %s", fails, self.send_error)
                    if isinstance(e, (TimeoutError, asyncio.TimeoutError)):
                        await self._drop_link("live send timed out")
                    await self._sleep_or_halt(min(5.0, 0.5 * fails))
                    continue
            await self._sleep_or_halt(max(0.02, self.interval - (time.monotonic() - t0)))

    # ---- watchdog
    async def restore_fallback(self) -> str:
        """Put the sign back on the last active on-device playlist (0x8008), or the
        OrcSDR default preset if nothing was ever uploaded."""
        from . import playlist as PL
        if PL.slot_record().get("active"):
            # 22:15 PT finding: an immediate 0x8008 after the last legacy frame was acked but
            # the frame stayed on the panel. Settle first, then repeat the (tiny) command once.
            await asyncio.sleep(self.restore_settle_s)
            r = await PL.play(self.sign)
            if self.restore_repeat_s:
                await asyncio.sleep(self.restore_repeat_s)
                r = await PL.play(self.sign)
            return f"playlist {r['playlist']}"
        from . import branding as B
        from . import presets as PR
        name = B.default_preset("OrcSDR") or "orcsdr_wave_small_anim"
        await PR.play(self.sign, name)
        return f"preset {name}"

    async def _do_fallback(self) -> bool:
        fb = self.fallback or self.restore_fallback
        try:
            self.showing = await asyncio.wait_for(fb(), self.fallback_timeout_s)
            self.fallback_error = None
            self._fallback_active = True
            log.info("orcsdr live: sign back on %s", self.showing)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            self.showing = None
            self.fallback_error = f"{type(e).__name__}: {e}"
            log.warning("orcsdr live: fallback failed: %s", self.fallback_error)
            return False

    async def _stream(self) -> str:
        """Stream until OrcSDR is lost ('lost') or someone else takes the sign ('takeover')."""
        self._lost, self._takeover, self._halt = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.state = "streaming"
        self.fail_streak, self.last_ok_t = 0, time.monotonic()
        self._sent_frame, self._last_bytes = None, None
        self._seq = None                                # display_seq baseline (first frame sets it)
        for _ in range(self.loss_fails):                # prime (avoids a blank WAIT frame)
            if await self.sample_once() is not None:
                break
            await asyncio.sleep(self.poll_s)
        if self.status is None:
            return "lost"
        sampler = asyncio.create_task(self._sampler())
        sender = asyncio.create_task(self._sender())
        self._tasks = [sampler, sender]
        waits = [asyncio.create_task(self._lost.wait()), asyncio.create_task(self._takeover.wait())]
        try:
            done, _ = await asyncio.wait([*waits, sampler, sender],
                                         return_when=asyncio.FIRST_COMPLETED)
            if self._takeover.is_set():
                return "takeover"
            for t in (sampler, sender):
                if t in done and not t.cancelled() and t.exception():
                    log.error("orcsdr live task died: %r", t.exception())
            return "lost"
        finally:
            for w in waits:
                w.cancel()
            sampler.cancel()
            # let an in-flight frame finish so a half-written PNG never precedes 0x8008
            self._halt.set()
            if not sender.done():
                _, pending = await asyncio.wait([sender], timeout=self.send_timeout_s + 2)
                if pending:
                    sender.cancel()
                    log.warning("orcsdr live: sender did not finish; dropping BLE link")
                    await self._drop_link("live sender did not finish")
            await asyncio.gather(*waits, sampler, sender, return_exceptions=True)
            self._tasks = []

    async def _wait_for_device(self) -> bool:
        """Probe GET /api/status with backoff; True once it answers RESUME_OKS times in a
        row, False if another display command takes the sign meanwhile."""
        baseline = self.sign.last_frame
        delay, self.ok_streak = self.probe_s, 0
        while True:
            await asyncio.sleep(self.confirm_s if self.ok_streak else delay)
            if self.sign.last_frame is not baseline:
                self.stopped_reason = self._takeover_reason()
                return False
            if self.showing is None:                    # fallback failed earlier: retry
                await self._do_fallback()
                baseline = self.sign.last_frame
            self.client.base_url = self.url()
            self.last_probe_at = time.time()
            try:
                self.status = await self._get(self.client.status())
                self.ok_streak += 1
                self.device_error = None
                if self.ok_streak >= self.resume_oks:
                    return True
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.ok_streak = 0
                self.status = None
                self.device_error = f"{type(e).__name__}: {e}"
                delay = min(self.probe_max_s, delay + 1.0)

    async def _supervise(self):
        while True:
            try:
                why = await self._stream()
                if why == "takeover":
                    self.state = "off"
                    self.stopped_at = time.time()
                    self._save_armed(False)
                    return
                self.state = "waiting"
                self.outages += 1
                self.lost_at = time.time()
                log.info("orcsdr live: OrcSDR lost (%s); falling back, will resume when it's back",
                         self.device_error)
                if not self._fallback_active:           # Live frames on the sign -> replace
                    await self._do_fallback()
                if not await self._wait_for_device():
                    log.info("orcsdr live: %s while waiting; disarming", self.stopped_reason)
                    self.state = "off"
                    self.stopped_at = time.time()
                    self._save_armed(False)
                    return
                self.resumes += 1
                log.info("orcsdr live: OrcSDR is back; resuming (resume #%d)", self.resumes)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001  (never let the watchdog die silently)
                self.supervisor_error = f"{type(e).__name__}: {e}"
                log.exception("orcsdr live watchdog error; retrying")
                await asyncio.sleep(2.0)

    def _save_armed(self, armed: bool):
        if not self.persist:
            return
        try:
            upd = {SETTING_ARMED: bool(armed)}
            if armed:
                upd.update({SETTING_WATERFALL: self.waterfall, SETTING_INTERVAL: self.interval,
                            self.TRANSPORT_KEY: self.transport})
            S.save(upd)
        except Exception as e:  # noqa: BLE001
            log.warning("orcsdr live: could not save armed state: %s", e)

    async def on_sign_reconnect(self) -> bool:
        """SignClient keeper hook. True if Live owns the sign (caller resumes nothing else).
        Streaming: the sender picks up by itself. Waiting: the sign may have rebooted, so
        put the fallback (playlist / preset) back on it."""
        if not self.enabled:
            return False
        self._last_bytes = None
        if self.state == "waiting":          # streaming: the next Live frame is the resume
            await self._do_fallback()
        return True

    @property
    def enabled(self) -> bool:
        """True while Live is armed (streaming OR waiting for OrcSDR)."""
        return bool(self._supervisor and not self._supervisor.done())

    async def start(self, waterfall: bool | None = None, interval: float | None = None):
        if waterfall is not None:
            self.waterfall = bool(waterfall)
        if interval is not None:
            self.interval = max(0.0, min(60.0, float(interval)))
        if self.enabled:
            return
        for p in self.peers:                           # one live source at a time
            if p is not self and p.enabled:
                await p.stop()
                p.stopped_reason = f"{self.NAME} started"
        if self.auto is not None and getattr(self.auto, "enabled", False):
            await self.auto.stop()
        dropped = 0
        if hasattr(self.sign, "supersede_pending"):     # queued preset/image sends are stale now
            dropped = self.sign.supersede_pending(f"{self.NAME} was started")
        log.info("orcsdr live: armed (waterfall=%s, interval=%.1f s%s)", self.waterfall, self.interval,
                 f", dropped {dropped} queued send(s)" if dropped else "")
        self.started_wall = time.time()
        self.stopped_at = None
        self.stopped_reason = None
        self._sent_frame = None
        self._last_bytes = None
        self.frames_sent = 0
        self.send_failures = 0
        self.outages = self.resumes = 0
        self.fallback_error = self.supervisor_error = None
        self._fallback_active = False
        self.showing = None
        self.bytes_sent.clear()
        self.transport = norm_transport(self.transport)   # set at init / via the config route
        self.send_ms.clear()
        self.sent_at.clear()
        self.started_at = time.monotonic()
        self.state = "streaming"
        self.ble_paused = False
        self._supervisor = asyncio.create_task(self._supervise())
        self._save_armed(True)

    async def stop(self, restore: bool = False) -> str | None:
        """Disarm. restore=True (user pressed stop): if the sign was showing Live frames,
        put the playlist back; while waiting it is already playing and is left alone."""
        sup, self._supervisor = self._supervisor, None
        was, showing, live_on_sign = self.state, self.showing, not self._fallback_active
        me = asyncio.current_task()
        # cancel only the supervisor: its _stream() cleanup lets an in-flight frame finish
        for group in ([sup], list(self._tasks)):
            for t in group:
                if t is not None and t is not me and not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
        self._tasks = []
        self.state = "off"
        self.showing = None
        self.ble_paused = False
        if sup is not None:
            self._save_armed(False)
            self.stopped_at = time.time()
            log.info("orcsdr live: disarmed (restore=%s)", restore)
        if restore and was != "off" and (live_on_sign or showing is None):
            if await self._do_fallback():
                return self.showing
        return showing if restore else None

    def _taken_over(self) -> bool:
        if getattr(self, "_seq", None) is not None and hasattr(self.sign, "display_seq"):
            return self.sign.display_seq != self._seq
        return self._sent_frame is not None and self.sign.last_frame is not self._sent_frame

    def _takeover_reason(self) -> str:
        sh = getattr(self.sign, "showing", None) or {}
        what = " ".join(str(x) for x in (sh.get("kind"), sh.get("name")) if x)
        return f"{what} sent" if what else "another display command took over the sign"

    def label(self) -> str:
        if self.state != "off" and self.ble_paused:
            return "paused: sign reconnecting"
        if self.state == "streaming":
            return "streaming" if not self._fallback_active else "reconnecting to OrcSDR"
        if self.state == "waiting":
            what = self.showing or ("fallback failed: " + (self.fallback_error or "?"))
            return f"waiting for OrcSDR (showing {what})"
        return "off"

    def info(self) -> dict:
        fps = None
        if len(self.sent_at) >= 2:
            span = self.sent_at[-1] - self.sent_at[0]
            fps = (len(self.sent_at) - 1) / span if span > 0 else None
        avg = sum(self.send_ms) / len(self.send_ms) if self.send_ms else None
        sizes = [b for b in self.bytes_sent if b]
        run_s = (time.monotonic() - self.started_at) if self.started_at and self.enabled else None
        return {"enabled": self.enabled, "armed": self.enabled, "state": self.state,
                "label": self.label(), "showing": self.showing,
                "waterfall": self.waterfall, "interval_s": self.interval,
                "transport": self.transport, "frames_sent": self.frames_sent,
                "send_failures": getattr(self, "send_failures", 0),
                "bytes_avg": round(sum(sizes) / len(sizes)) if sizes else None,
                "fps_since_start": round(self.frames_sent / run_s, 2) if run_s else None,
                "avg_send_ms": round(avg) if avg else None,
                "frames_per_s": round(fps, 3) if fps else None,
                "send_error": self.send_error, "stopped_reason": self.stopped_reason,
                "stopped_at": self.stopped_at, "started_at": self.started_wall,
                "outages": self.outages, "resumes": self.resumes,
                "fail_streak": self.fail_streak, "ok_streak": self.ok_streak,
                "lost_at": self.lost_at, "last_probe_at": self.last_probe_at,
                "fallback_error": self.fallback_error, "supervisor_error": self.supervisor_error,
                "test_outage": self.url_override is not None, "ble_paused": self.ble_paused}


# ------------------------------------------------------------------ web hook
# request models live at module level so FastAPI can resolve the (postponed)
# annotations and treat them as JSON bodies
from typing import Optional  # noqa: E402

from pydantic import BaseModel  # noqa: E402


class LiveReq(BaseModel):
    action: str = "start"            # start | stop
    waterfall: Optional[bool] = None
    interval: Optional[float] = None


class OutageReq(BaseModel):
    outage: bool = True                  # test hook: simulate an OrcSDR outage


class CfgReq(BaseModel):
    transport: Optional[str] = None      # legacy | fast | fast-rgb | fast-acked
    url: Optional[str] = None
    waterfall: Optional[bool] = None
    interval: Optional[float] = None


def install(app, sign, auto=None) -> OrcSDRLive:
    """Register /api/orcsdr/* routes on the FastAPI app. Called from web.create_app."""
    from fastapi import HTTPException
    from fastapi.responses import Response

    live = OrcSDRLive(sign, auto, persist=True)
    app.state.orcsdr_live = live

    def _payload():
        st = live.status
        return {"url": live.url(), "reachable": st is not None and live.device_error is None,
                "device_error": live.device_error,
                "device": st.summary() if st else None, "live": live.info()}

    @app.get("/api/orcsdr/status")
    async def orcsdr_status():
        if not live.enabled:
            await live.sample_once(spectrum=False)
        return _payload()

    @app.get("/api/orcsdr/preview.png")
    async def orcsdr_preview(scale: int = 8, waterfall: Optional[bool] = None):
        if not live.enabled:
            await live.sample_once()
        fn = render_frame64 if live.size() == (64, 64) else render_frame
        img = fn(live.status, live.bins, live.peaks, live.history,
                 live.waterfall if waterfall is None else waterfall, live.device_error)
        return Response(png_bytes(img, max(1, min(scale, 16))), media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/orcsdr/live")
    async def orcsdr_live(r: LiveReq):
        if r.action == "start":
            await live.start(r.waterfall, r.interval)
        elif r.action == "stop":
            restored = await live.stop(restore=True)
            live.stopped_reason = "stopped by user" + (f"; sign on {restored}" if restored else "")
        else:
            raise HTTPException(400, "action must be start or stop")
        return _payload()

    @app.post("/api/orcsdr/test/outage")
    async def orcsdr_test_outage(r: OutageReq):
        """Test hook: point Live (in memory only, not saved) at an address that never
        answers, to exercise the watchdog without touching the OrcSDR device.
        {"outage": false} goes back to the configured URL."""
        live.url_override = TEST_OUTAGE_URL if r.outage else None
        return _payload()

    @app.get("/api/orcsdr/config")
    async def orcsdr_cfg_get():
        return {"url": device_url(), "waterfall": live.waterfall, "interval": live.interval,
                "transport": live.transport, "transports": TRANSPORTS}

    @app.post("/api/orcsdr/config")
    async def orcsdr_cfg_set(r: CfgReq):
        upd = {}
        if r.url is not None:
            u = r.url.strip().rstrip("/")
            if not u.startswith(("http://", "https://")):
                raise HTTPException(400, "url must start with http:// or https://")
            upd[SETTING_URL] = u
        if r.transport is not None:
            if r.transport not in TRANSPORTS:
                raise HTTPException(400, "transport must be one of " + ", ".join(TRANSPORTS))
            live.transport = r.transport
            upd[SETTING_TRANSPORT] = r.transport
        if r.waterfall is not None:
            live.waterfall = bool(r.waterfall)
            upd[SETTING_WATERFALL] = live.waterfall
        if r.interval is not None:
            live.interval = max(0.0, min(60.0, float(r.interval)))
            upd[SETTING_INTERVAL] = live.interval
        if upd:
            S.save(upd)
        return await orcsdr_cfg_get()

    return live


# --------------------------------------------------------------------- CLI
def _main():
    """python -m orcpixels.orcsdr_live [--out preview.png] [--scale 10] [--waterfall] [--samples 6]"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="orcsdr_live_preview.png")
    ap.add_argument("--scale", type=int, default=10)
    ap.add_argument("--waterfall", action="store_true")
    ap.add_argument("--samples", type=int, default=6)
    a = ap.parse_args()

    async def run():
        live = OrcSDRLive(sign=None)
        live.waterfall = a.waterfall
        for i in range(a.samples):
            await live.sample_once()
            await asyncio.sleep(0.5)
        img = live.render()
        with open(a.out, "wb") as fh:
            fh.write(png_bytes(img, a.scale))
        print(live.status.summary() if live.status else live.device_error)
        print(f"wrote {a.out}; 1x PNG = {len(png_bytes(img))} bytes")
    asyncio.run(run())


if __name__ == "__main__":
    _main()
