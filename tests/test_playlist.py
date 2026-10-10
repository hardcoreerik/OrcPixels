"""Offline tests for program-slot commands and playlist upload (fake sign, no BLE)."""
import asyncio

import pytest

from orcpixels import playlist as PL
from orcpixels import protocol as P


def test_program_list_bytes():
    assert P.set_program_list([1, 2, 3]).frames[0].data == bytes.fromhex("0900088003000102 03".replace(" ", ""))
    # pypixelcolor show_slot is the one-slot program list
    assert P.show_slot(5).frames[0].data == P.set_program_list([5]).frames[0].data
    assert P.delete_slot(7).frames[0].data == P.delete_slots([7]).frames[0].data
    assert P.delete_slots([2, 4]).frames[0].data == bytes.fromhex("0800020102000204")


def test_slot_guards():
    for bad in ([], [0], [101], [255]):
        with pytest.raises(ValueError):
            P.set_program_list(bad)
        with pytest.raises(ValueError):
            P.delete_slots(bad)          # never the erase-all form


def test_save_slot_byte():
    f = P.send_gif(b"GIF89a....", save_slot=9).frames[0].data
    assert f[2:4] == b"\x03\x00" and f[13] == 0x02 and f[14] == 9


def test_parse_ack():
    assert P.parse_ack(bytes.fromhex("0500088001")) == {"cmd": 0x8008, "status": 1, "raw": "0500088001"}
    assert P.parse_ack(bytes.fromhex("0500030003"))["cmd"] == 0x0003


class FakeSign:
    rotate_180 = True

    def __init__(self):
        from orcpixels.ble_sign import SignClient
        self._real = SignClient(None, rotate_180=True)
        self.calls = []
        self.last_frame = self.last_gif = None

    def prepare_png(self, img):
        return self._real.prepare_png(img)

    def prepare_gif(self, b):
        return self._real.prepare_gif(b)

    async def _ensure_orientation(self, v):
        self.calls.append(("orient", v))

    async def set_program_list(self, slots):
        self.calls.append(("list", list(slots)))
        return {"raw": "0500088001"}

    async def store_gif(self, b, slot, progress=None):
        progress and progress(len(b), len(b))
        self.calls.append(("gif", slot))
        return {"raw": "0500030003"}

    async def store_png(self, b, slot, progress=None):
        self.calls.append(("png", slot))
        return {"raw": "0500020003"}


def test_upload_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    PL.save("t", ["orcsdr_wave_small_anim", {"preset": "orcpixels_splash", "duration": 5}])
    s = FakeSign()
    r = asyncio.run(PL.upload(s, "t"))
    assert r["slots"] == [1, 2]
    assert s.calls == [("orient", 0), ("list", [1, 2]), ("gif", 1), ("gif", 2), ("list", [1, 2])]
    rec = PL.slot_record()
    assert rec["slots"]["2"]["preset"] == "orcpixels_splash" and rec["active"]["slots"] == [1, 2]
    s.calls.clear()
    asyncio.run(PL.upload(s, "t", skip_unchanged=True))
    assert ("gif", 1) not in s.calls
    asyncio.run(PL.play(s))
    assert s.calls[-1] == ("list", [1, 2])
