"""Async BLE client for the iPixel Color LED sign (LED_BLE_xxxxxxxx)."""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from PIL import Image

from . import activity as A
from . import displays as D
from . import protocol as P
from . import settings as S

log = logging.getLogger("orcpixels.ble")

NAME_PREFIX = "LED_BLE_"
DEFAULT_ADDRESS = os.environ.get("ORCPIXELS_ADDRESS") or None   # e.g. AA:BB:CC:DD:EE:FF; None = first LED_BLE_* (CLI only)


@dataclass
class SignState:
    connected: bool = False
    address: str | None = None
    name: str | None = None
    width: int = 64
    height: int = 16
    device_info: dict | None = None
    brightness: int | None = None
    power: bool | None = None
    last_error: str | None = None
    notifications: deque = field(default_factory=lambda: deque(maxlen=50))


async def scan(timeout: float = 6.0, prefix: str = NAME_PREFIX) -> list[tuple[BLEDevice, int | None]]:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        if name.startswith(prefix):
            out.append((dev, adv.rssi))
    return out


class Superseded(RuntimeError):
    """A queued display send was dropped because something newer (e.g. OrcSDR Live being
    started) took the sign before it got the BLE link."""


class TakenOver(RuntimeError):
    """A Live frame was not sent because another display command got the sign first."""


class SignClient:
    """High-level sign API. All commands are serialized with a lock and
    auto-(re)connect, so it is safe to share between a web server and a
    background watcher task."""

    def __init__(self, address: str | None = DEFAULT_ADDRESS, name_prefix: str = NAME_PREFIX,
                 ack_timeout: float = 8.0, connect_timeout: float = 20.0,
                 write_timeout: float = 10.0,
                 chunk_size: int = P.CHUNK_SIZE, rotate_180: bool | None = None,
                 display: dict | None = None):
        # display: registry entry (displays.py) this client drives; its settings scope
        # (rotate_180, 30 s rule) and nominal size until device_info answers
        self.display = display
        if display and not address:
            address = display.get("address")
        self.address = address
        self.name_prefix = name_prefix
        self.ack_timeout = ack_timeout
        self.connect_timeout = connect_timeout
        # WinRT GATT writes / disconnects have no timeout of their own; a half-dead link
        # could otherwise block forever while holding the command lock (2026-09-30)
        self.write_timeout = write_timeout
        self.chunk_size = int(__import__("os").environ.get("ORCPIXELS_CHUNK", chunk_size))
        self.state = SignState(address=address)
        if display:
            self.state.width, self.state.height = int(display["width"]), int(display["height"])
            self.state.name = display.get("name") or None
        # None -> follow config/settings.json (re-read on every send, so the web
        # toggle / CLI 'config' take effect immediately)
        self._rotate_override = rotate_180
        self._dev_orientation: int | None = None   # last orientation we set on the device
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._acks: asyncio.Queue[tuple[str | None, bytes]] = asyncio.Queue()
        self.on_notify: list[Callable[[bytes], None]] = []
        self.last_frame: Image.Image | None = None   # what we last showed (for UI preview)
        self.last_gif: bytes | None = None           # set when the last thing shown was a GIF
        # ---- link supervisor (start_supervisor; the web server runs it, the CLI doesn't)
        # Power-cycling the sign (2026-09-30 11:18) left recovery to whoever sent next; now
        # a keeper task reconnects forever with backoff whenever the link is down.
        self.supervised = False
        self.want_connected = True       # False after an explicit disconnect()
        self.link_state = "idle"         # connected | connecting | reconnecting | disconnected
        self.link_error: str | None = None
        self.link_lost_at: float | None = None       # wall clock
        self.next_retry_at: float | None = None      # wall clock
        self.reconnect_attempts = 0                  # in the current outage
        self.reconnects = 0                          # successful re-connections
        self.on_reconnect: list[Callable[[], Awaitable[None]]] = []
        # what to put back after a reconnect when no live mode owns the sign
        self.resume_hint: dict | None = None         # {"kind": "playlist"|"preset", "name": ..}
        self.retry_min_s, self.retry_max_s = 2.0, 10.0
        # 30 s rule (settings sign_stuck_timeout_s / sign_idle_action, follow_settings=True)
        self.follow_settings = False
        self.stuck_timeout_s = 30.0
        self.idle_action = "play"                    # play | disconnect
        self.retry_long_min_s, self.retry_long_max_s = 30.0, 60.0
        self.on_idle: Callable[[], Awaitable[str]] | None = None
        self.content_at: float | None = None         # wall clock of the last real content
        self._content_deadline: float | None = None  # monotonic; armed on every link-up
        self.gave_up = False
        self.gave_up_count = 0
        self.gave_up_reason: str | None = None
        self.idle_resumes = 0
        self.last_idle_resume: dict | None = None
        self._hold_until = 0.0
        self._long_delay = 30.0
        self._had_link = False
        self._link_evt = asyncio.Event()
        self._keeper_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bg: set[asyncio.Task] = set()
        # ---- web UI: send log + "what's on the sign now" (Task B)
        self.activity = A.Activity()
        self.showing: dict | None = None   # {"kind", "name", "since", ...}
        # 2026-09-30 18:05: rapid preset clicks queued ~60 s of 10 KB GIF sends behind the
        # BLE lock; Live started meanwhile only got its first frame after all of them.
        # supersede_pending() makes queued (not yet sending) display sends skip themselves.
        self._supersede_epoch = 0
        # every non-Live display send bumps display_seq (inside the BLE lock order), so
        # OrcSDR Live can tell "someone else showed something" even when its own frame was
        # waiting on the lock meanwhile (18:16 test: a preset sent during Live was simply
        # overwritten by the next queued Live frame and Live never disarmed)
        self.display_seq = 0
        self.live_seq: int | None = None   # display_seq seen by the last Live frame
        self._supersede_reason: str | None = None

    # ------------------------------------------------------------ connection
    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def _find(self) -> BLEDevice:
        """Fresh scan every time (never a cached BLEDevice). With an address configured
        ONLY that address matches. (Until 2026-10-08 any LED_BLE_* name was accepted as
        well, so with a second panel nearby a reconnect took whichever advertised first:
        that is how the 64x64 matrix got connected instead of the 64x16 strip.) The name
        prefix is used only when no address is configured (bare CLI use)."""
        addr = (self.address or "").upper()
        if not addr and self.display is not None:
            raise ConnectionError("no sign address configured for this display: use Scan / add display")

        def match(d, a):
            if addr:
                return (d.address or "").upper() == addr
            return (a.local_name or d.name or "").startswith(self.name_prefix)
        dev = await BleakScanner.find_device_by_filter(match, timeout=10.0)
        if not dev:
            raise ConnectionError(
                f"sign not found (address={self.address}, prefix={self.name_prefix}). "
                "Is it powered, and is the phone app disconnected?")
        return dev

    def _on_disconnect(self, c):
        if c is not None and self._client is not None and c is not self._client:
            log.info("stale client disconnected (ignored)")
            return
        log.info("sign disconnected")
        self.state.connected = False
        if self.link_lost_at is None:
            self.link_lost_at = time.time()
        self._wake()

    def _wake(self):
        """Wake the keeper; bleak may call back from another thread."""
        loop = self._loop
        try:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._link_evt.set)
            else:
                self._link_evt.set()
        except RuntimeError:  # loop closed
            pass

    def _notify(self, _char, data: bytearray):
        data = bytes(data)
        kind = P.classify_notify(data)
        log.debug("notify %s (%s)", data.hex(), kind)
        self.state.notifications.append({"t": _dt.datetime.now().isoformat(timespec="seconds"),
                                         "hex": data.hex(), "kind": kind})
        self._acks.put_nowait((kind, data))
        for cb in self.on_notify:
            try:
                cb(data)
            except Exception:  # pragma: no cover
                log.exception("notify callback failed")

    async def connect(self) -> SignState:
        self.want_connected = True
        self._hold_until = 0.0                   # explicit connect overrides a give-up hold
        async with self._lock:
            await self._connect_locked()
        self._wake()
        return self.state

    async def _connect_locked(self, attempts: int = 3, strict: bool = False):
        """strict: the link only counts once device_info round-trips (write + notify), so
        a half-working first connection after a power cycle is dropped and retried."""
        if self.connected:
            return
        self._loop = asyncio.get_running_loop()
        err: Exception | None = None
        for i in range(attempts):
            mine = None
            try:
                if self._client is not None:            # stale client (e.g. sign power-cycled)
                    await self._safe_disconnect()
                dev = await self._find()
                # use_cached_services=False: re-discover GATT, don't trust Windows' cache
                self._client = BleakClient(dev, disconnected_callback=self._on_disconnect,
                                           timeout=self.connect_timeout,
                                           winrt={"use_cached_services": False})
                mine = self._client
                await self._client.connect()
                await self._client.start_notify(P.NOTIFY_UUID, self._notify)
                self.state.connected = True
                self.state.address = dev.address
                self.state.name = dev.name
                self.state.last_error = None
                log.info("connected to %s (%s), mtu=%s", dev.name, dev.address,
                         getattr(self._client, "mtu_size", "?"))
                self._dev_orientation = None     # device may have rebooted: re-send before use
                ok = await self._device_info_locked()
                if strict and not ok:
                    raise ConnectionError("connected but device_info got no reply")
                if self.state.brightness is not None:   # re-apply after a (re)connect
                    try:
                        await self._run_locked(P.set_brightness(self.state.brightness))
                    except Exception as e:  # noqa: BLE001
                        log.warning("re-applying brightness failed: %s", e)
                self._had_link = True
                self.link_lost_at = None
                # 30 s rule: content (frame/preset/playlist) must follow within the timeout
                self._content_deadline = time.monotonic() + self.stuck_timeout_s
                return
            except asyncio.CancelledError:
                # caller timed out mid-connect: don't leave a half-open client behind (only
                # our own: after abort_link() a newer client may already be in the slot)
                if mine is not None and self._client is mine:
                    await self._safe_disconnect()
                raise
            except Exception as e:  # noqa: BLE001
                err = e
                self.state.last_error = f"{type(e).__name__}: {e}"
                log.warning("connect attempt %d failed: %s", i + 1, self.state.last_error)
                await self._safe_disconnect()
                if i < attempts - 1:
                    await asyncio.sleep(1.5 * (i + 1))
        raise ConnectionError(f"could not connect: {err}")

    async def _safe_disconnect(self):
        c, self._client = self._client, None
        if c:
            try:
                await asyncio.wait_for(c.disconnect(), 8.0)
            except Exception:  # noqa: BLE001  (incl. TimeoutError)
                pass
        self.state.connected = False

    async def disconnect(self):
        """Explicit disconnect (user / shutdown): the keeper will NOT reconnect until
        connect() is called again."""
        self.want_connected = False
        async with self._lock:
            await self._safe_disconnect()
        self._wake()

    async def drop_link_for_test(self, hard: bool = False):
        """Test hook: lose the link like a power cut, WITHOUT clearing want_connected.
        soft: disconnect the BleakClient but keep the (now stale) object, exactly the state
        an unplugged sign leaves behind. hard: abandon it as if a GATT call hung."""
        if hard:
            self.abort_link("test hook: hard drop")
            return
        async with self._lock:
            c = self._client
            if c is not None:
                try:
                    await asyncio.wait_for(c.disconnect(), 8.0)
                except Exception:  # noqa: BLE001
                    pass
        self.state.connected = False
        if self.link_lost_at is None:
            self.link_lost_at = time.time()
        self._wake()

    def abort_link(self, reason: str):
        """Abandon the current link WITHOUT waiting for it: a GATT operation that ignores
        cancellation keeps holding the old lock, so hand out a fresh lock and client slot.
        The old client is disconnected in the background."""
        log.warning("dropping BLE link: %s", reason)
        c, self._client = self._client, None
        self._lock = asyncio.Lock()
        self.state.connected = False
        self.state.last_error = reason
        if self.link_lost_at is None:
            self.link_lost_at = time.time()
        if c is not None:
            async def _drop():
                try:
                    await asyncio.wait_for(c.disconnect(), 8.0)
                except Exception:  # noqa: BLE001
                    pass
            t = asyncio.get_running_loop().create_task(_drop())
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)
        self._wake()

    async def bounded(self, coro, timeout: float, what: str = "operation"):
        """Run coro with a hard deadline that does not wait for the cancellation to be
        honoured (asyncio.wait_for does). On timeout the link is abandoned."""
        t = asyncio.ensure_future(coro)
        done, _ = await asyncio.wait({t}, timeout=timeout)
        if t not in done:
            t.cancel()
            t.add_done_callback(lambda f: f.cancelled() or f.exception())
            self.abort_link(f"{what} stuck > {timeout:.0f}s")
            raise TimeoutError(f"{what} did not finish within {timeout:.0f}s")
        return t.result()

    # ------------------------------------------------------------ supervisor
    def start_supervisor(self):
        self._loop = asyncio.get_running_loop()
        self.supervised = True
        if self._keeper_task is None or self._keeper_task.done():
            self._keeper_task = asyncio.create_task(self._keeper())

    async def stop_supervisor(self):
        t, self._keeper_task = self._keeper_task, None
        self.supervised = False
        if t:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _reconnect_once(self):
        async with self._lock:
            await self._connect_locked(attempts=1, strict=True)

    def _reload_cfg(self):
        if not self.follow_settings:
            return
        try:
            cfg = self._settings()
            self.stuck_timeout_s = max(5.0, float(cfg.get("sign_stuck_timeout_s") or 30.0))
            self.idle_action = str(cfg.get("sign_idle_action") or "play")
        except Exception:  # noqa: BLE001
            pass

    def _mark_content(self):
        """Something real was put on the display (frame, preset, playlist play...)."""
        self.content_at = time.time()
        self._content_deadline = None

    def _give_up(self, reason: str):
        """30 s rule: fully let go of the sign so it runs its own stored playlist, and hold
        off (retry_long_min_s..retry_long_max_s) before trying again."""
        log.warning("giving up on the link (%s); sign left on its own playlist, next try in %.0fs",
                    reason, self._long_delay)
        self.gave_up, self.gave_up_count = True, self.gave_up_count + 1
        self.gave_up_reason = reason
        self.link_state = "gave_up"
        if self._client is not None:
            self.abort_link(reason)
        self._hold_until = time.monotonic() + self._long_delay
        self.next_retry_at = time.time() + self._long_delay
        self._long_delay = min(self.retry_long_max_s, self._long_delay * 1.5)

    async def _check_idle(self):
        """Connected but nothing was put on the sign within stuck_timeout_s of the link
        coming up: play the saved playlist (0x8008) or, idle_action="disconnect" / if that
        fails, let go of the sign entirely."""
        d = self._content_deadline
        if d is None or time.monotonic() < d:
            return
        self._content_deadline = None
        log.warning("sign connected %.0fs without content (idle_action=%s)",
                    self.stuck_timeout_s, self.idle_action)
        if self.idle_action == "play" and self.on_idle is not None:
            try:
                what = await self.bounded(self.on_idle(), self.stuck_timeout_s, "idle resume")
                self.idle_resumes += 1
                self.last_idle_resume = {"at": time.time(), "what": what}
                log.info("idle rule: sign back on %s", what)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("idle resume failed: %s: %s", type(e).__name__, e)
        self._give_up(f"no content within {self.stuck_timeout_s:.0f}s of connecting")

    async def _setup_link(self, was_up: bool):
        """Connect (device_info must answer) and resume content; the caller bounds the
        whole thing by stuck_timeout_s."""
        await self._reconnect_once()
        if not self.connected:
            raise ConnectionError("link dropped right after connecting")
        self.link_state, self.link_error = "connected", None
        self.reconnect_attempts = 0
        if was_up:
            self.reconnects += 1
            log.info("sign reconnected (#%d); resuming", self.reconnects)
            for hook in list(self.on_reconnect):
                try:
                    await hook()
                except Exception as e:  # noqa: BLE001
                    log.warning("on_reconnect hook failed: %s: %s", type(e).__name__, e)

    async def _keeper(self):
        """Keep the link up: whenever it is down (and wanted), rescan + reconnect with
        backoff retry_min_s..retry_max_s, and after the first stuck_timeout_s of an outage
        (or after giving up) only every retry_long_min_s..retry_long_max_s, so repeated
        attempts can't keep the sign on a connecting screen. A link setup (connect +
        device_info + resume hooks) that doesn't finish within stuck_timeout_s is
        abandoned (full disconnect -> the sign falls back to its own stored playlist)."""
        delay = self.retry_min_s
        outage_t0: float | None = None
        while True:
            try:
                self._reload_cfg()
                if self.connected or not self.want_connected:
                    if self.connected:
                        if self.link_state != "connected":
                            self.link_state = "connected"
                        self.reconnect_attempts = 0
                        self.next_retry_at, self.link_error = None, None
                        self.gave_up, self._hold_until = False, 0.0
                        self._long_delay = self.retry_long_min_s
                        delay, outage_t0 = self.retry_min_s, None
                        await self._check_idle()
                    else:
                        self.link_state = "disconnected"
                    self._link_evt.clear()
                    try:
                        await asyncio.wait_for(self._link_evt.wait(), 1.0 if self.connected else 2.0)
                    except (TimeoutError, asyncio.TimeoutError):
                        pass
                    continue
                hold = self._hold_until - time.monotonic()
                if hold > 0:                               # gave up: leave the sign alone
                    self.link_state = "gave_up"
                    self.next_retry_at = time.time() + hold
                    await asyncio.sleep(min(hold, 1.0))
                    continue
                if outage_t0 is None:
                    outage_t0 = time.monotonic()
                was_up = self._had_link
                if self.link_lost_at is None:
                    self.link_lost_at = time.time()
                self.link_state = "reconnecting" if was_up else "connecting"
                self.reconnect_attempts += 1
                self.next_retry_at = None
                log.info("sign link down; %s (attempt %d)", self.link_state, self.reconnect_attempts)
                await self.bounded(self._setup_link(was_up), self.stuck_timeout_s, "link setup")
                self.link_state, self.link_error = "connected", None
                self.reconnect_attempts, delay = 0, self.retry_min_s
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.link_error = f"{type(e).__name__}: {e}"
                if isinstance(e, TimeoutError) and "link setup" in str(e):
                    self._give_up(f"link setup not complete within {self.stuck_timeout_s:.0f}s")
                    continue
                long = outage_t0 is not None and \
                    time.monotonic() - outage_t0 >= self.stuck_timeout_s
                if long and delay < self.retry_long_min_s:
                    delay = self.retry_long_min_s
                self.next_retry_at = time.time() + delay
                log.warning("sign reconnect attempt %d failed: %s; next try in %.1fs",
                            self.reconnect_attempts, self.link_error, delay)
                await asyncio.sleep(delay)
                delay = min(self.retry_long_max_s if long else self.retry_max_s, delay * 1.6)

    def link_info(self) -> dict:
        now = time.time()
        return {"state": self.link_state if self.supervised else
                ("connected" if self.connected else "disconnected"),
                "supervised": self.supervised, "want_connected": self.want_connected,
                "attempts": self.reconnect_attempts, "reconnects": self.reconnects,
                "error": self.link_error,
                "down_s": round(now - self.link_lost_at, 1) if self.link_lost_at else None,
                "next_retry_s": round(max(0.0, self.next_retry_at - now), 1)
                if self.next_retry_at else None,
                "resume_hint": self.resume_hint,
                "gave_up": self.gave_up, "gave_up_count": self.gave_up_count,
                "gave_up_reason": self.gave_up_reason,
                "stuck_timeout_s": self.stuck_timeout_s, "idle_action": self.idle_action,
                "idle_in_s": round(max(0.0, self._content_deadline - time.monotonic()), 1)
                if self._content_deadline and self.connected else None,
                "idle_resumes": self.idle_resumes, "last_idle_resume": self.last_idle_resume,
                "content_at": self.content_at}

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    # ------------------------------------------------------------- transport
    def _drain(self):
        while not self._acks.empty():
            self._acks.get_nowait()

    async def _wait(self, want: set[str | None], timeout: float) -> bytes:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            left = end - loop.time()
            if left <= 0:
                raise TimeoutError(f"no {want} notification within {timeout}s")
            kind, data = await asyncio.wait_for(self._acks.get(), left)
            if kind in want or None in want:
                return data

    async def _run_locked(self, cmd: P.Command,
                          progress: Callable[[int, int], None] | None = None) -> bytes | None:
        await self._connect_locked(1 if self.supervised else 3)
        assert self._client
        mtu = getattr(self._client, "mtu_size", 247) or 247
        chunk = max(20, min(self.chunk_size, mtu - 3))
        reply = None
        total, sent = sum(len(f.data) for f in cmd.frames), 0
        for frame in cmd.frames:
            self._drain()
            for pos in range(0, len(frame.data), chunk):
                piece = frame.data[pos:pos + chunk]
                await asyncio.wait_for(
                    self._client.write_gatt_char(P.WRITE_UUID, piece, response=True),
                    self.write_timeout)
                sent += len(piece)
                if progress:
                    progress(sent, total)
            if cmd.expects_response:
                reply = await self._wait({None}, self.ack_timeout)
            elif frame.requires_ack:
                reply = await self._wait({"window", "done"}, self.ack_timeout)
        log.info("%s ok (%d frame(s), %d bytes)%s", cmd.name, len(cmd.frames),
                 sum(len(f.data) for f in cmd.frames), f" reply={reply.hex()}" if reply else "")
        return reply

    def supersede_pending(self, reason: str) -> int:
        """Drop display sends that are queued but not yet on the air. Returns how many."""
        self._supersede_epoch += 1
        self._supersede_reason = reason
        n = sum(1 for e in self.activity.recent(200) if e["status"] == "queued" and e.get("display"))
        if n:
            log.info("dropping %d queued display send(s): %s", n, reason)
        return n

    def queue_info(self) -> dict:
        items = self.activity.recent(200)
        q = [e for e in items if e["status"] == "queued"]
        s = [e for e in items if e["status"] == "sending"]
        pend = sum((e["bytes"] or 0) for e in q + s)
        return {"queued": len(q), "sending": s[-1]["what"] if s else None,
                "queued_what": [e["what"] for e in q][:10],
                "est_s": round(pend / 1100 + 0.6 * len(q + s), 1) if q or s else 0}

    async def run(self, cmd: P.Command, retry: bool = True,
                  progress: Callable[[int, int], None] | None = None,
                  supersedable: bool = False) -> bytes | None:
        ent = self.activity.start(cmd.name, sum(len(f.data) for f in cmd.frames))
        ent["status"], ent["display"] = "queued", supersedable
        epoch = self._supersede_epoch
        try:
            reply = await self._run_retry(cmd, retry, progress, ent, epoch if supersedable else None)
        except Superseded as e:
            self.activity.finish(ent, "skipped", error=str(e))
            raise
        except BaseException as e:
            self.activity.finish(ent, "failed", error=f"{type(e).__name__}: {e}")
            raise
        acked = reply is not None or not (cmd.expects_response or any(f.requires_ack for f in cmd.frames))
        self.activity.finish(ent, "acked" if reply is not None else ("sent" if acked else "no reply"), reply)
        return reply

    async def _run_retry(self, cmd: P.Command, retry: bool,
                         progress: Callable[[int, int], None] | None,
                         ent: dict | None = None, epoch: int | None = None) -> bytes | None:
        async with self._lock:
            if epoch is not None and epoch != self._supersede_epoch:
                raise Superseded(f"skipped: {self._supersede_reason or 'superseded'} after it was queued")
            if ent is not None:
                self.activity.mark(ent, "sending")
            try:
                return await self._run_locked(cmd, progress)
            except Exception as e:  # noqa: BLE001
                self.state.last_error = f"{cmd.name}: {type(e).__name__}: {e}"
                if not retry:
                    raise
                log.warning("%s failed (%s); reconnecting and retrying once", cmd.name, e)
                await self._safe_disconnect()
                self._wake()
                return await self._run_locked(cmd, progress)

    def set_showing(self, kind: str, name: str | None = None, **extra):
        self.showing = {"kind": kind, "name": name, "since": time.time(), **extra}

    async def _device_info_locked(self) -> bool:
        now = _dt.datetime.now()
        try:
            reply = await self._run_locked(P.get_device_info(now.hour, now.minute, now.second))
            info = P.parse_device_info(reply)
            self.state.device_info = info
            self.state.width, self.state.height = info["width"], info["height"]
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("device info failed (%s); assuming 64x16", e)
            return False

    # ------------------------------------------------------------ rotation
    @property
    def rotate_180(self) -> bool:
        if self._rotate_override is not None:
            return self._rotate_override
        if self.display is not None:
            with D.use(self.display):
                return bool(S.get("rotate_180"))
        return bool(S.get("rotate_180"))

    def _settings(self) -> dict:
        if self.display is not None:
            with D.use(self.display):
                return S.load()
        return S.load()

    async def _ensure_orientation(self, value: int):
        if self._dev_orientation != value:
            await self.run(P.set_orientation(value))
            self._dev_orientation = value

    def _orient(self, img: Image.Image) -> Image.Image:
        return img.rotate(180) if self.rotate_180 else img

    # ---------------------------------------------------------------- API
    async def send_text(self, text: str, *, color: str = "ffffff", bg_color: str | None = None,
                        animation: int = P.TextAnimation.STATIC, speed: int = 80,
                        rainbow: int = 0) -> None:
        """Native device text (device scrolls/animates it).

        Glyphs can't be rotated in software (the device lays them out), so with
        rotate_180 the device-side orientation command (2 = 180 deg) is used for text;
        images/GIFs are rotated in software with device orientation 0."""
        await self._ensure_orientation(2 if self.rotate_180 else 0)
        await self.run(P.send_text(text, color=color, bg_color=bg_color, animation=animation,
                                   speed=speed, rainbow=rainbow), supersedable=True)
        self.last_gif = None
        self.resume_hint = None
        self._mark_content()
        self.display_seq += 1
        self.set_showing("text", text[:40])
        from .render import preview_native_text
        self.last_frame = preview_native_text(text, color, bg_color, self.state.width, self.state.height)

    async def send_image(self, img: Image.Image) -> None:
        """Show a static image (resized/letterboxed to the panel, sent as PNG)."""
        w, h = self.state.width, self.state.height
        upright = Image.open(__import__("io").BytesIO(P.image_to_png(img, w, h))).convert("RGB")
        await self._ensure_orientation(0)
        await self.run(P.send_png(P.image_to_png(self._orient(upright), w, h)), supersedable=True)
        self.last_gif = None
        self.last_frame = upright   # preview shows the as-viewed (upright) image
        self.resume_hint = None     # presets.play / playlist.play set it after the send
        self._mark_content()
        self.display_seq += 1
        self.set_showing("image", A.LABEL.get() or "image")

    async def send_live_png(self, png: bytes, upright: Image.Image, chunk: int = 244,
                            response: bool = False, ack_timeout: float | None = None,
                            timeout: float | None = None, expect_seq: int | None = None) -> bytes:
        """Fast path for live frames (OrcSDR Live). `png` must already be panel-sized and
        rotated. Writes the single-window send_png command in `chunk`-byte pieces, by
        default WITHOUT GATT response (fa02 supports write-without-response, max 514 B at
        MTU 517), then waits only for the device's final transfer ack.

        Measured 2026-09-30: each frame yields exactly one notification, 05 00 02 00 03
        (cmd 0x0002, status 3 = CRC ok + complete); no per-chunk or window acks for a
        single-window frame. status 0 (NG/CRC) raises. Any error drops the connection so
        the next send reconnects cleanly (no silent partial frames).

        ack_timeout overrides self.ack_timeout for the final ack; timeout bounds the
        whole write+ack (not the reconnect), so a lost ack or a stuck GATT write raises
        TimeoutError (and drops the link) instead of wedging the caller."""
        await self._ensure_orientation(0)
        cmd = P.send_png(png)
        async with self._lock:
            if expect_seq is not None and self.display_seq != expect_seq:
                raise TakenOver("another display command took the sign before this Live frame")
            try:
                await self._connect_locked(1 if self.supervised else 3)
                assert self._client
                reply = None
                ack_t = ack_timeout or self.ack_timeout
                async with asyncio.timeout(timeout):          # None = unbounded (old behaviour)
                    for i, frame in enumerate(cmd.frames):
                        self._drain()
                        for pos in range(0, len(frame.data), chunk):
                            await self._client.write_gatt_char(
                                P.WRITE_UUID, frame.data[pos:pos + chunk], response=response)
                        last = i == len(cmd.frames) - 1
                        while True:
                            reply = await self._wait({"window", "done"}, ack_t)
                            if reply[4] == 0:
                                raise RuntimeError(f"live png NG ack {reply.hex()}")
                            if not last or reply[4] == 3:
                                break
            except BaseException as e:
                self.activity.live_frame(len(png), False, error=f"{type(e).__name__}: {e}")
                if not isinstance(e, Exception):
                    raise
                self.state.last_error = f"send_live_png: {type(e).__name__}: {e}"
                await self._safe_disconnect()
                self._wake()
                raise
        self.activity.live_frame(len(png), True, reply)
        self.live_seq = self.display_seq
        self.last_gif = None
        self.last_frame = upright
        self.resume_hint = None
        self._mark_content()
        if not self.showing or self.showing.get("kind") != "live":
            self.set_showing("live", "OrcSDR Live")
        return reply

    async def send_gif(self, gif_bytes: bytes, first_frame: Image.Image | None = None) -> None:
        """Show an animated GIF. Frames are fitted to the panel, rotated if
        rotate_180, and re-encoded device-friendly (global palette, full frames)."""
        from .render import encode_gif, gif_frames
        frames, durs = gif_frames(gif_bytes)
        w, h = self.state.width, self.state.height
        frames = [f if f.size == (w, h) else P.fit_image(f, w, h) for f in frames]
        upright = encode_gif(frames, durs)
        out = encode_gif([self._orient(f) for f in frames], durs) if self.rotate_180 else upright
        await self._ensure_orientation(0)
        await self.run(P.send_gif(out), supersedable=True)
        self.last_gif = upright
        self.last_frame = first_frame or frames[0]
        self.resume_hint = None
        self._mark_content()
        self.display_seq += 1
        self.set_showing("animation", A.LABEL.get() or "animation", frames=len(frames))

    async def brightness(self, level: int) -> None:
        await self.run(P.set_brightness(level))
        self.state.brightness = int(level)

    async def power(self, on: bool) -> None:
        await self.run(P.set_power(on))
        self.state.power = bool(on)

    async def clear(self) -> None:
        """Blank the display by sending an all-black frame (does NOT wipe storage)."""
        await self.send_image(Image.new("RGB", (self.state.width, self.state.height)))

    async def orientation(self, value: int) -> None:
        await self.run(P.set_orientation(value))

    # ------------------------------------------------ stored slots / program list
    # Byte formats + sources: protocol.py "stored slots / program list".
    @staticmethod
    def _check_ack(reply: bytes | None, want_cmd: int, ok: set[int], what: str) -> dict:
        a = P.parse_ack(reply or b"")
        if a["cmd"] != want_cmd or a["status"] not in ok:
            raise RuntimeError(f"{what}: unexpected ack {a['raw'] or None} "
                               f"(want cmd {want_cmd:#06x} status in {sorted(ok)})")
        return a

    def prepare_png(self, img: Image.Image) -> tuple[bytes, Image.Image]:
        """-> (device PNG bytes incl. rotate_180, upright preview)."""
        w, h = self.state.width, self.state.height
        upright = Image.open(__import__("io").BytesIO(P.image_to_png(img, w, h))).convert("RGB")
        return P.image_to_png(self._orient(upright), w, h), upright

    def prepare_gif(self, gif_bytes: bytes) -> tuple[bytes, bytes, Image.Image]:
        """-> (device GIF bytes incl. rotate_180, upright GIF, upright first frame)."""
        from .render import encode_gif, gif_frames
        frames, durs = gif_frames(gif_bytes)
        w, h = self.state.width, self.state.height
        frames = [f if f.size == (w, h) else P.fit_image(f, w, h) for f in frames]
        upright = encode_gif(frames, durs)
        out = encode_gif([self._orient(f) for f in frames], durs) if self.rotate_180 else upright
        return out, upright, frames[0]

    async def set_program_list(self, slots: list[int]) -> dict:
        """0x8008: device plays these stored slots in order, endlessly, without the PC."""
        reply = await self.run(P.set_program_list(slots))
        a = self._check_ack(reply, 0x8008, {1}, "program_list")
        self._mark_content()
        self.display_seq += 1
        self.set_showing("playlist", None, slots=list(slots))
        return a

    async def show_slot(self, n: int) -> dict:
        reply = await self.run(P.show_slot(n))
        a = self._check_ack(reply, 0x8008, {1}, "show_slot")
        self._mark_content()
        self.display_seq += 1
        self.set_showing("slot", f"slot {n}", slots=[n])
        return a

    async def store_png(self, png: bytes, slot: int, progress=None) -> dict:
        """Write device-ready PNG bytes (already rotated) into program slot 1..100."""
        P._check_slots([slot])
        reply = await self.run(P.send_png(png, save_slot=slot), progress=progress)
        return self._check_ack(reply, 0x0002, {3}, f"store png slot {slot}")

    async def store_gif(self, gif: bytes, slot: int, progress=None) -> dict:
        P._check_slots([slot])
        reply = await self.run(P.send_gif(gif, save_slot=slot), progress=progress)
        return self._check_ack(reply, 0x0003, {3}, f"store gif slot {slot}")

    async def delete_slots(self, slots: list[int]) -> dict:
        """0x0102 with an explicit slot list (never the erase-all form). Callers must only
        pass slots they wrote (playlist.py checks its local slot record)."""
        reply = await self.run(P.delete_slots(slots))
        return self._check_ack(reply, 0x0102, {1}, "delete_slots")

    async def firmware_versions(self) -> str | None:
        reply = await self.run(P.get_firmware_versions())
        return reply.hex() if reply else None
