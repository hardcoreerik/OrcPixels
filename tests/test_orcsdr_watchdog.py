"""OrcSDR Live watchdog: mocked OrcSDR goes up -> down -> up; fake sign, no BLE, no HTTP.

Checks: loss detection after consecutive failed polls, fallback (playlist) exactly once,
Live stays armed while waiting, automatic resume after 2 good probes, user stop leaves
the playlist playing, takeover while waiting disarms, and a stuck ack can't wedge it.
"""
import asyncio
import random

from orcpixels import orcsdr_live as OL
from orcpixels.sources.orcsdr import OrcSDRStatus


class FakeOrcSDR:
    """Read-only GET mock: status() / spectrum_raw() fail while .up is False."""

    def __init__(self):
        self.up = True
        self.base_url = ""
        self.status_calls = 0
        self.spectrum_calls = 0

    async def status(self):
        self.status_calls += 1
        await asyncio.sleep(0.001)
        if not self.up:
            raise TimeoutError("timed out")
        return OrcSDRStatus.from_json({"mode": "FM", "frequency_hz": 96_100_000,
                                       "signal_dbfs": -22.0, "receiving": True, "rtl_ready": True})

    async def spectrum_raw(self):
        self.spectrum_calls += 1
        await asyncio.sleep(0.001)
        if not self.up:
            raise OSError("no route to host")
        return [random.randrange(256) for _ in range(64)]


class FakeSign:
    rotate_180 = True

    def __init__(self, hang_first: int = 0):
        self.connected = True
        self.last_frame = None
        self.last_gif = None
        self.frames = 0
        self.hang_first = hang_first
        self.disconnects = 0

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnects += 1
        self.connected = False

    async def send_live_png(self, png, upright, chunk=244, response=False,
                            ack_timeout=None, timeout=None):
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        if self.hang_first > 0:        # an ack that never comes
            self.hang_first -= 1
            await asyncio.sleep(3600)
        await asyncio.sleep(0.002)
        self.frames += 1
        self.last_frame = upright
        self.last_gif = None


def make(sign=None, client=None):
    calls = []

    async def fallback():
        calls.append("playlist")
        return "playlist test3"

    live = OL.OrcSDRLive(sign or FakeSign(), client=client or FakeOrcSDR(), fallback=fallback)
    live.poll_s = 0.01
    live.loss_fails, live.loss_min_s = 3, 0.05
    live.probe_s, live.probe_max_s, live.confirm_s = 0.03, 0.06, 0.02
    live.ack_timeout_s, live.send_timeout_s, live.connect_timeout_s = 0.1, 0.2, 0.2
    live.transport = "fast"
    return live, calls


async def until(pred, timeout=5.0, step=0.01):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(step)


def test_up_down_up_resumes_and_user_stop_restores_playlist():
    async def run():
        live, calls = make()
        sign, sdr = live.sign, live.client
        await live.start(interval=0.0)
        assert live.enabled and live.info()["label"] == "streaming"
        await until(lambda: sign.frames >= 5)
        assert calls == []

        sdr.up = False                                           # OrcSDR drops
        await until(lambda: live.state == "waiting")
        assert calls == ["playlist"]                             # playlist restarted once
        assert live.enabled and live.outages == 1                # still armed
        assert live.info()["label"] == "waiting for OrcSDR (showing playlist test3)"
        frozen = sign.frames
        probes = sdr.status_calls
        await asyncio.sleep(0.3)
        assert sign.frames == frozen                             # no Live frames over the playlist
        assert sdr.status_calls > probes                         # keeps probing /api/status
        assert calls == ["playlist"]                             # ...without re-sending 0x8008

        sdr.up = True                                            # OrcSDR is back
        await until(lambda: live.state == "streaming" and sign.frames > frozen + 3)
        assert live.resumes == 1 and live.outages == 1

        restored = await live.stop(restore=True)                 # user presses stop
        assert restored == "playlist test3"
        assert calls == ["playlist", "playlist"]
        assert not live.enabled and live.state == "off"
        n = sign.frames
        await asyncio.sleep(0.1)
        assert sign.frames == n
    asyncio.run(run())


def test_flapping_needs_two_good_probes():
    async def run():
        live, calls = make()
        sdr = live.client
        live.resume_oks = 2
        await live.start(interval=0.0)
        await until(lambda: live.sign.frames >= 2)
        sdr.up = False
        await until(lambda: live.state == "waiting")
        # one good probe, then down again: must not resume
        orig = sdr.status
        seq = iter([True, False, False, False])

        async def status():
            sdr.up = next(seq, False)
            return await orig()
        sdr.status = status
        await asyncio.sleep(0.4)
        assert live.state == "waiting" and live.resumes == 0
        sdr.status = orig
        sdr.up = True
        await until(lambda: live.state == "streaming")
        await live.stop()
    asyncio.run(run())


def test_stop_while_waiting_leaves_playlist_alone():
    async def run():
        live, calls = make()
        await live.start(interval=0.0)
        await until(lambda: live.sign.frames >= 2)
        live.client.up = False
        await until(lambda: live.state == "waiting")
        assert await live.stop(restore=True) == "playlist test3"
        assert calls == ["playlist"]                             # not restarted a 2nd time
        assert not live.enabled
    asyncio.run(run())


def test_start_while_offline_goes_straight_to_playlist():
    async def run():
        live, calls = make()
        live.client.up = False
        await live.start(interval=0.0)
        await until(lambda: live.state == "waiting")
        assert calls == ["playlist"] and live.sign.frames == 0
        live.client.up = True
        await until(lambda: live.state == "streaming" and live.sign.frames > 0)
        await live.stop()
    asyncio.run(run())


def test_takeover_while_waiting_disarms():
    async def run():
        live, calls = make()
        await live.start(interval=0.0)
        await until(lambda: live.sign.frames >= 2)
        live.client.up = False
        await until(lambda: live.state == "waiting")
        live.sign.last_frame = object()                          # user showed a preset
        live.client.up = True
        await until(lambda: not live.enabled)
        assert live.state == "off" and "took over" in live.stopped_reason
        assert live.resumes == 0
    asyncio.run(run())


def test_stuck_ack_does_not_wedge_sender():
    async def run():
        sign = FakeSign(hang_first=2)
        live, _ = make(sign=sign)
        await live.start(interval=0.0)
        await until(lambda: sign.frames >= 3, timeout=10)
        assert live.send_failures == 2 and sign.disconnects >= 2
        assert live.state == "streaming"
        await live.stop()
    asyncio.run(run())


def test_default_fallback_prefers_playlist(monkeypatch):
    from orcpixels import playlist as PL
    from orcpixels import presets as PR
    played = []

    async def play(sign, name=None):
        played.append(("playlist", name))
        return {"playlist": "test3", "slots": [1, 2, 3], "ack": "0500088001"}

    async def preset(sign, name):
        played.append(("preset", name))
        return {"name": name}
    monkeypatch.setattr(PL, "play", play)
    monkeypatch.setattr(PR, "play", preset)
    live = OL.OrcSDRLive(FakeSign(), client=FakeOrcSDR())
    live.restore_settle_s, live.restore_repeat_s = 0.0, 0.01
    monkeypatch.setattr(PL, "slot_record", lambda: {"active": {"playlist": "test3", "slots": [1, 2, 3]}})
    assert asyncio.run(live.restore_fallback()) == "playlist test3"
    monkeypatch.setattr(PL, "slot_record", lambda: {"slots": {}, "active": None})
    assert asyncio.run(live.restore_fallback()) == "preset orcsdr_wave_small_anim"
    # 0x8008 is sent twice (22:15 PT: the first one after a legacy frame didn't show)
    assert played == [("playlist", None), ("playlist", None), ("preset", "orcsdr_wave_small_anim")]


def test_client_is_read_only_and_short_timeout():
    import inspect
    from orcpixels.sources import orcsdr as src
    assert OL.OrcSDRLive(FakeSign()).client.timeout <= 2.0
    code = inspect.getsource(src.OrcSDRClient)
    assert 'method="GET"' in code and "/api/action" not in code


def test_relapse_right_after_resume_does_not_restart_playlist():
    async def run():
        live, calls = make()
        sdr = live.client
        await live.start(interval=0.0)
        await until(lambda: live.sign.frames >= 2)
        sdr.up = False
        await until(lambda: live.state == "waiting")
        frames = live.sign.frames
        orig = sdr.status
        seq = iter([True, True])                   # 2 good probes, then gone again

        async def status():
            sdr.up = next(seq, False)
            return await orig()
        sdr.status = status
        await until(lambda: live.resumes == 1)
        await until(lambda: live.state == "waiting" and live.outages == 2)
        assert live.sign.frames == frames          # no Live frame went out in between
        assert calls == ["playlist"]               # so the playlist wasn't restarted
        assert await live.stop(restore=True) == "playlist test3"
        assert calls == ["playlist"]
    asyncio.run(run())


class LockSign(FakeSign):
    """Serializes sends like SignClient (one BLE lock) and keeps display_seq/live_seq."""

    def __init__(self):
        super().__init__()
        self.lock = asyncio.Lock()
        self.display_seq = 0
        self.live_seq = None
        self.showing = None

    async def send_live_png(self, png, upright, chunk=244, response=False,
                            ack_timeout=None, timeout=None, expect_seq=None):
        async with self.lock:
            if expect_seq is not None and self.display_seq != expect_seq:
                from orcpixels.ble_sign import TakenOver
                raise TakenOver("taken")
            await asyncio.sleep(0.002)
            self.frames += 1
            self.last_frame = upright
            self.live_seq = self.display_seq

    async def send_gif_like_preset(self, name, hold=0.2):
        async with self.lock:
            await asyncio.sleep(hold)          # a 10 KB GIF takes ~9 s over BLE
            self.last_frame = object()
        self.display_seq += 1
        self.showing = {"kind": "preset", "name": name}


def test_preset_sent_while_live_frame_waits_on_the_lock_disarms_live():
    """18:16 real-sign finding: the Live frame queued behind a preset used to overwrite it
    and Live never noticed (last_frame identity check raced with the BLE lock)."""
    async def run():
        sign = LockSign()
        live, _ = make(sign=sign)
        await live.start(interval=0.0)
        await until(lambda: sign.frames >= 5)
        await sign.send_gif_like_preset("p_orc_eyes")
        n = sign.frames
        await until(lambda: not live.enabled)
        assert sign.frames == n                    # the waiting Live frame was dropped
        assert live.stopped_reason == "preset p_orc_eyes sent"
        assert live.stopped_at is not None
    asyncio.run(run())


def test_signclient_live_frame_refuses_after_display_send():
    from orcpixels.ble_sign import SignClient, TakenOver
    from PIL import Image

    async def run():
        s = SignClient(address=None, rotate_180=False)
        s._dev_orientation = 0
        s.display_seq = 3
        try:
            await s.send_live_png(b"\x89PNG\r\n\x1a\n", Image.new("RGB", (64, 16)), expect_seq=2)
        except TakenOver:
            return "taken"
    assert asyncio.run(run()) == "taken"
