"""BLE link keeper (SignClient supervisor) + Live pause/persistence, with bleak mocked.

A power cut is simulated the way Windows reports it: the BleakClient flips to
is_connected=False and fires disconnected_callback, and the old object stays in place.
"""
import asyncio
import types

import pytest

from orcpixels import ble_sign as BS
from orcpixels import orcsdr_live as OL
from orcpixels import settings as S


class FakeBleak:
    instances = []
    fail_connects = 0            # next N connect() calls raise

    def __init__(self, dev, disconnected_callback=None, timeout=None, winrt=None):
        self.dev, self.cb, self.winrt = dev, disconnected_callback, winrt
        self.is_connected = False
        self.disconnect_calls = 0
        self.writes = []
        self.mtu_size = 517
        FakeBleak.instances.append(self)

    async def connect(self):
        await asyncio.sleep(0.001)
        if FakeBleak.fail_connects > 0:
            FakeBleak.fail_connects -= 1
            raise OSError("device unreachable")
        self.is_connected = True

    async def start_notify(self, uuid, cb):
        self.notify = cb

    async def write_gatt_char(self, uuid, data, response=True):
        self.writes.append(bytes(data))
        if bytes(data)[:4] == bytes.fromhex("05000480"):          # brightness -> ack
            self.notify(None, bytearray.fromhex("0500048001"))

    async def disconnect(self):
        self.disconnect_calls += 1
        was = self.is_connected
        self.is_connected = False
        if was and self.cb:
            self.cb(self)

    def power_cut(self):
        self.is_connected = False
        self.cb(self)


class FakeScanner:
    misses = 0                   # next N scans find nothing (sign still booting)
    scans = 0

    @staticmethod
    async def find_device_by_filter(match, timeout=10.0):
        FakeScanner.scans += 1
        await asyncio.sleep(0.001)
        if FakeScanner.misses > 0:
            FakeScanner.misses -= 1
            return None
        dev = types.SimpleNamespace(address=BS.DEFAULT_ADDRESS, name="LED_BLE_8D8235E2")
        adv = types.SimpleNamespace(local_name=dev.name)
        return dev if match(dev, adv) else None


@pytest.fixture
def sign(monkeypatch):
    FakeBleak.instances, FakeBleak.fail_connects = [], 0
    FakeScanner.misses, FakeScanner.scans = 0, 0
    monkeypatch.setattr(BS, "BleakClient", FakeBleak)
    monkeypatch.setattr(BS, "BleakScanner", FakeScanner)
    info_ok = {"v": True}

    async def devinfo(self):
        return info_ok["v"]
    monkeypatch.setattr(BS.SignClient, "_device_info_locked", devinfo)
    s = BS.SignClient()
    s.retry_min_s, s.retry_max_s = 0.02, 0.05
    s._info_ok = info_ok
    return s


async def until(pred, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


def test_power_cut_reconnects_with_fresh_client_and_runs_hooks(sign):
    async def run():
        hooks = []

        async def hook():
            hooks.append(sign._client)
        sign.on_reconnect.append(hook)
        await sign.connect()
        sign.start_supervisor()
        c1 = sign._client
        assert c1.winrt == {"use_cached_services": False}
        await until(lambda: sign.link_state == "connected")
        sign._dev_orientation = 0
        sign.state.brightness = 60

        FakeScanner.misses = 3                     # sign is booting for a few scans
        c1.power_cut()                             # stale object stays in sign._client
        await until(lambda: sign.link_state in ("reconnecting",))
        assert not sign.connected
        await until(lambda: sign.reconnects == 1 and len(hooks) == 1)
        c2 = sign._client
        assert sign.connected and sign.link_state == "connected"
        assert c2 is not c1 and c1.disconnect_calls >= 1      # stale client dropped
        assert FakeScanner.scans >= 5                         # rescanned every attempt
        assert sign.reconnects == 1 and hooks == [c2]
        assert sign._dev_orientation is None                  # re-sent before next image
        assert bytes.fromhex("050004803c") in c2.writes      # brightness 60 re-applied
        info = sign.link_info()
        assert info["state"] == "connected" and info["attempts"] == 0
        await sign.stop_supervisor()
    asyncio.run(run())


def test_keeps_retrying_with_backoff_until_sign_is_back(sign):
    async def run():
        await sign.connect()
        sign.start_supervisor()
        FakeBleak.fail_connects = 6
        sign._client.power_cut()
        await until(lambda: sign.reconnect_attempts >= 3)
        assert sign.link_info()["state"] == "reconnecting" and sign.link_error
        await until(lambda: sign.reconnects == 1, timeout=10)
        assert sign.connected
        await sign.stop_supervisor()
    asyncio.run(run())


def test_strict_reconnect_drops_link_without_device_info(sign):
    async def run():
        await sign.connect()
        sign.start_supervisor()
        sign._info_ok["v"] = False                  # first connection after the cut is bad
        sign._client.power_cut()
        await until(lambda: len(FakeBleak.instances) >= 2)
        await asyncio.sleep(0.05)
        assert not sign.connected
        sign._info_ok["v"] = True
        await until(lambda: sign.connected and sign.link_state == "connected")
        bad = FakeBleak.instances[1]
        assert bad.disconnect_calls >= 1
        await sign.stop_supervisor()
    asyncio.run(run())


def test_explicit_disconnect_is_respected(sign):
    async def run():
        await sign.connect()
        sign.start_supervisor()
        await sign.disconnect()
        n = len(FakeBleak.instances)
        await asyncio.sleep(0.2)
        assert not sign.connected and len(FakeBleak.instances) == n
        assert sign.link_info()["state"] == "disconnected"
        await sign.connect()
        assert sign.connected
        await sign.stop_supervisor()
    asyncio.run(run())


def test_test_hook_soft_and_hard_drop(sign):
    async def run():
        await sign.connect()
        sign.start_supervisor()
        c1 = sign._client
        await sign.drop_link_for_test()
        assert sign.want_connected
        await until(lambda: sign.reconnects == 1 and sign.link_state == "connected")
        c2 = sign._client
        assert sign.connected and c2 is not c1
        sign.abort_link("test")
        await until(lambda: sign.reconnects == 2 and sign.link_state == "connected")
        assert sign.connected and sign._client is not c2
        await sign.stop_supervisor()
    asyncio.run(run())


def test_bounded_abandons_an_op_that_ignores_cancel(sign):
    async def run():
        release = asyncio.Event()

        async def stuck():
            async with sign._lock:
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    await release.wait()           # like a WinRT op that finishes late
        old_lock = sign._lock
        t0 = asyncio.get_running_loop().time()
        with pytest.raises(TimeoutError):
            await sign.bounded(stuck(), 0.1, "gatt write")
        assert asyncio.get_running_loop().time() - t0 < 1.0
        assert sign._lock is not old_lock and not sign._lock.locked()
        release.set()
        await asyncio.sleep(0.01)
    asyncio.run(run())


# ----------------------------------------------------------------- Live side
class PausableSign:
    rotate_180 = True
    supervised = True

    def __init__(self):
        self.connected = True
        self.last_frame = None
        self.frames = 0

    def abort_link(self, reason):
        self.connected = False

    async def bounded(self, coro, timeout, what="op"):
        return await asyncio.wait_for(coro, timeout)

    async def send_live_png(self, png, upright, **kw):
        if not self.connected:
            raise RuntimeError("sent while the link is down")
        await asyncio.sleep(0.002)
        self.frames += 1
        self.last_frame = upright


def _live(sign, persist=False):
    from test_orcsdr_watchdog import FakeOrcSDR
    calls = []

    async def fb():
        calls.append("playlist")
        return "playlist test3"
    live = OL.OrcSDRLive(sign, client=FakeOrcSDR(), fallback=fb, persist=persist)
    live.poll_s = 0.01
    live.probe_s, live.probe_max_s, live.confirm_s = 0.03, 0.06, 0.02
    live.loss_min_s = 0.05
    live.transport = "fast"
    return live, calls


def test_live_pauses_cleanly_while_link_is_down():
    async def run():
        sign = PausableSign()
        live, calls = _live(sign)
        await live.start(interval=0.0)
        await until(lambda: sign.frames >= 3)
        sign.connected = False
        await until(lambda: live.ble_paused)
        n = sign.frames
        await asyncio.sleep(0.3)
        assert sign.frames == n and live.send_failures == 0     # idle, no spinning errors
        assert live.info()["label"] == "paused: sign reconnecting"
        assert live.enabled and calls == []
        sign.connected = True
        await until(lambda: sign.frames > n + 2)
        assert not live.ble_paused and live.info()["label"] == "streaming"
        assert await live.on_sign_reconnect() is True and calls == []   # streaming: no playlist
        await live.stop()
    asyncio.run(run())


def test_reconnect_while_waiting_reasserts_playlist():
    async def run():
        sign = PausableSign()
        live, calls = _live(sign)
        await live.start(interval=0.0)
        await until(lambda: sign.frames >= 2)
        live.client.up = False
        await until(lambda: live.state == "waiting")
        assert calls == ["playlist"]
        assert await live.on_sign_reconnect() is True
        assert calls == ["playlist", "playlist"]
        await live.stop()
        assert await live.on_sign_reconnect() is False
    asyncio.run(run())


def test_live_armed_state_and_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")

    async def run():
        live, _ = _live(PausableSign(), persist=True)
        await live.start(waterfall=True, interval=0.9)
        cfg = S.load()
        assert cfg["orcsdr_live_armed"] is True and cfg["orcsdr_interval"] == 0.9
        assert cfg["orcsdr_waterfall"] is True and cfg["orcsdr_transport"] == "fast"
        await live.stop(restore=True)
        assert S.load()["orcsdr_live_armed"] is False
    asyncio.run(run())
    assert S.DEFAULTS["orcsdr_live_armed"] is False


# ------------------------------------------------------------ 30 s rule (task A)
def _fast_rule(sign, t=0.2, long_min=0.4, long_max=0.6):
    sign.stuck_timeout_s = t
    sign.retry_long_min_s, sign.retry_long_max_s = long_min, long_max
    sign._long_delay = long_min


def test_idle_connection_plays_saved_playlist(sign):
    async def run():
        _fast_rule(sign)
        calls = []

        async def on_idle():
            calls.append("play")
            sign._mark_content()
            return "playlist test3"
        sign.on_idle = on_idle
        await sign.connect()
        sign.start_supervisor()
        await until(lambda: calls == ["play"])
        assert sign.connected and sign.idle_resumes == 1 and not sign.gave_up
        await asyncio.sleep(0.4)
        assert calls == ["play"]                    # once per link-up, not a loop
        await sign.stop_supervisor()
    asyncio.run(run())


def test_content_within_timeout_means_no_idle_action(sign):
    async def run():
        _fast_rule(sign)
        calls = []

        async def on_idle():
            calls.append("play")
            return "x"
        sign.on_idle = on_idle
        await sign.connect()
        sign._mark_content()                        # e.g. a Live frame went out
        sign.start_supervisor()
        await asyncio.sleep(0.5)
        assert calls == [] and sign.connected
        await sign.stop_supervisor()
    asyncio.run(run())


def test_idle_action_disconnect_lets_go_and_holds_off(sign):
    async def run():
        _fast_rule(sign)
        sign.idle_action = "disconnect"
        await sign.connect()
        sign.start_supervisor()
        c1 = sign._client
        await until(lambda: sign.gave_up)
        t0 = asyncio.get_running_loop().time()
        assert not sign.connected and sign.link_info()["state"] == "gave_up"
        assert sign.want_connected                  # still wanted: retries later
        await until(lambda: sign.connected and sign._client is not c1, timeout=5)
        assert asyncio.get_running_loop().time() - t0 >= 0.35   # held off ~retry_long_min_s
        await sign.stop_supervisor()
    asyncio.run(run())


def test_failed_idle_play_gives_up(sign):
    async def run():
        _fast_rule(sign)

        async def on_idle():
            raise RuntimeError("no ack")
        sign.on_idle = on_idle
        await sign.connect()
        sign.start_supervisor()
        await until(lambda: sign.gave_up)
        assert not sign.connected and "no content" in sign.gave_up_reason
        await sign.stop_supervisor()
    asyncio.run(run())


def test_stuck_link_setup_is_abandoned_and_disconnected(sign):
    async def run():
        _fast_rule(sign, t=0.3)
        release = asyncio.Event()

        async def slow_resume():                    # resume never completes
            await release.wait()
        sign.on_reconnect.append(slow_resume)
        await sign.connect()
        sign._mark_content()
        sign.start_supervisor()
        sign._client.power_cut()
        await until(lambda: sign.gave_up, timeout=5)
        bad = FakeBleak.instances[-1]
        await until(lambda: bad.disconnect_calls >= 1)            # fully let go
        assert not sign.connected
        assert "link setup" in sign.gave_up_reason
        release.set()
        await sign.stop_supervisor()
    asyncio.run(run())


def test_retries_are_spaced_out_after_first_timeout(sign):
    async def run():
        _fast_rule(sign, t=0.3, long_min=0.5, long_max=0.6)
        times = []
        orig = FakeScanner.find_device_by_filter

        async def scan(match, timeout=10.0):
            times.append(asyncio.get_running_loop().time())
            return None                             # sign never shows up
        await sign.connect()
        FakeScanner.find_device_by_filter = staticmethod(scan)
        try:
            sign.start_supervisor()
            sign._client.power_cut()
            await asyncio.sleep(2.2)
        finally:
            FakeScanner.find_device_by_filter = orig
        t0 = times[0]
        late = [b - a for a, b in zip(times, times[1:]) if a - t0 >= 0.3]
        early = [b - a for a, b in zip(times, times[1:]) if a - t0 < 0.3]
        assert early and min(early) < 0.2           # quick retries at first
        assert late and min(late) >= 0.45           # then >= retry_long_min_s apart
        await sign.stop_supervisor()
    asyncio.run(run())
