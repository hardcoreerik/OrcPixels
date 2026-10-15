"""OrcSDR serial auto-detect for the FT8 screens (2026-10-15): with no port set, `auto`
finds the OrcSDR among the Espressif USB serial ports with the read-only `FT8 STATUS`
probe, and a missing / busy OrcSDR gives a friendly "SET ORCSDR PORT" frame (SetupNeeded)
instead of "NO DATA RuntimeError". Mocked ports only, never a real COM port."""
import asyncio

import pytest

from orcpixels import live_adaptive as A
from orcpixels import livedata_screens as SC
from orcpixels import settings as S
from orcpixels.sources import ft8feed as FT
from orcpixels.sources import livefeeds as LF
from tests.test_ft8 import CAPTURE, FakeSerial, fetch_ft8, http_404  # noqa: F401
from tests.test_livedata import net  # noqa: F401  (autouse: mocked HTTP, temp settings, no serial)

ESP = FT.ESPRESSIF_VID
PORTS = [("COM14", ESP, 0x1001, "USB Serial Device"), ("COM16", ESP, 0x1001, "USB Serial Device"),
         ("COM40", 0x1A86, 0x55D3, "USB-Enhanced-SERIAL CH343"), ("COM5", None, None, "Serial over Bluetooth")]


class Dial(FakeSerial):
    """Another ESP32 (the M5Dial): answers anything with an error line."""
    def write(self, b):
        self.written.append(b)
        self.out += b"ORCDIAL_COMMAND_ERROR unknown\n"


class Ports:
    def __init__(self, monkeypatch, ports=PORTS, orcsdr="COM16", busy=()):
        self.ports, self.orcsdr, self.busy = list(ports), orcsdr, set(busy)
        self.opened, self.listed = [], 0
        monkeypatch.setattr(FT, "LIST_PORTS", self.list)
        monkeypatch.setattr(FT, "SERIAL_OPEN", self.open)
        monkeypatch.setattr(FT, "PROBE_TIMEOUT_S", 0.05)

    def list(self):
        self.listed += 1
        return self.ports

    def open(self, port, baud):
        if port in self.busy:
            raise OSError(f"could not open port '{port}': PermissionError(13, 'Access is denied.', None, 5)")
        s = FakeSerial(port, baud) if port == self.orcsdr else Dial(port, baud)
        self.opened.append(s)
        return s


@pytest.fixture
def no_port(monkeypatch):
    S.save({"livedata_ft8_source": "auto"})
    cur = LF._setting                                # conftest's sample install has COM9: blank it
    monkeypatch.setattr(LF, "_setting", lambda k: None if k == "livedata_ft8_port" else cur(k))
    monkeypatch.setattr(LF, "HTTP", _http_404)


def _http_404(url, headers, timeout):
    import urllib.error
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def cols(img) -> set:
    return {c for _, c in img.getcolors(1 << 16)}


def again(f):
    FT.SOURCE.polled_at = 0
    asyncio.run(f.refresh(force=True))
    return f


def test_candidates_are_espressif_ports_only(monkeypatch):
    Ports(monkeypatch, ports=PORTS + [("COM3", ESP, 0x1001, "x"), ("bogus port", ESP, 1, "x")])
    assert FT.candidate_ports() == ["COM3", "COM14", "COM16"]       # numeric order, CH343 / BT never


def test_auto_detects_orcsdr_and_uses_it(no_port, monkeypatch):
    p = Ports(monkeypatch)
    f = fetch_ft8()
    assert f.error is None and f.hint is None and f.value["via"] == "serial"
    assert f.value["port"] == "COM16 (auto)" and f.value["status"]["band"] == "20m"
    dial = [s for s in p.opened if s.port == "COM14"]
    assert len(dial) == 1 and dial[0].written == [b"FT8 STATUS\n"] and dial[0].closed   # probe only
    assert {s.port for s in p.opened} == {"COM14", "COM16"}                            # never CH343 / BT
    n = len(p.opened)
    again(f)
    assert f.error is None and len(p.opened) == n + 1 and p.opened[-1].port == "COM16"  # cached, no rescan


def test_auto_port_lost_rescans(no_port, monkeypatch):
    p = Ports(monkeypatch)
    f = fetch_ft8()
    assert FT.SOURCE.auto_port == "COM16"
    p.ports, p.orcsdr = [("COM21", ESP, 0x1001, "")], "COM21"        # replugged, new number
    real = p.open

    def gone(port, baud):
        if port == "COM16":
            raise OSError("could not open port 'COM16': FileNotFoundError(2, 'The system cannot find the file')")
        return real(port, baud)
    monkeypatch.setattr(FT, "SERIAL_OPEN", gone)
    again(f)
    assert f.error and "COM16" in f.error and FT.SOURCE.auto_port == ""
    again(f)
    assert f.error is None and f.value["port"] == "COM21 (auto)"


def test_no_orcsdr_gives_friendly_setup_frame(no_port, monkeypatch):
    p = Ports(monkeypatch, orcsdr=None)
    f = fetch_ft8()
    assert f.value is None and f.error.startswith("SetupNeeded") and "RuntimeError" not in f.error
    assert "Settings" in f.error and tuple(f.hint) == FT.NOT_SET_HINT
    assert f.state()["hint"] == list(FT.NOT_SET_HINT)
    probes = len(p.opened)
    again(f)                                                          # within 5 min: no new probes
    assert len(p.opened) == probes and f.hint
    p.ports = PORTS + [("COM30", ESP, 0x1001, "")]                    # port list changed: scan now
    again(f)
    assert len(p.opened) > probes


def test_no_serial_ports_at_all(no_port, monkeypatch):
    Ports(monkeypatch, ports=[])
    f = fetch_ft8()
    assert f.error.startswith("SetupNeeded") and "plug it in" in f.error and tuple(f.hint) == FT.NOT_SET_HINT


def test_busy_port_during_detect(no_port, monkeypatch):
    Ports(monkeypatch, orcsdr=None, busy={"COM16"})
    f = fetch_ft8()
    assert f.hint[0] == "COM16 BUSY" and "busy" in f.error


def test_configured_busy_port_is_friendly(monkeypatch):
    S.save({"livedata_ft8_source": "serial", "livedata_ft8_port": "COM9"})
    p = Ports(monkeypatch, busy={"COM9"})
    f = fetch_ft8()
    assert f.error.startswith("SetupNeeded") and "COM9" in f.error and f.hint[0] == "COM9 BUSY"
    assert p.listed == 0                                              # a set port never triggers a scan


def test_configured_port_skips_detect(monkeypatch):
    S.save({"livedata_ft8_source": "serial", "livedata_ft8_port": "COM16"})
    p = Ports(monkeypatch)
    f = fetch_ft8()
    assert f.error is None and f.value["port"] == "COM16" and p.listed == 0
    assert [s.port for s in p.opened] == ["COM16"]


def test_hint_clears_after_success(no_port, monkeypatch):
    p = Ports(monkeypatch, orcsdr=None)
    f = fetch_ft8()
    assert f.hint
    p.orcsdr = "COM16"
    FT.SOURCE.detect_fail = None                                     # e.g. settings changed / 5 min later
    again(f)
    assert f.error is None and f.hint is None and f.state()["hint"] is None


@pytest.mark.parametrize("size", [(64, 16), (64, 64)])
def test_setup_frame_renders_native_sizes(no_port, monkeypatch, size):
    Ports(monkeypatch, orcsdr=None)
    f = fetch_ft8()
    img = SC.render("ft8_ticker", f, 0, size=size)
    assert img.size == size and img.getbbox()
    assert SC.AMB in cols(img)                              # amber headline, not a red error
    f2 = LF.Feed()                                                   # same feed state minus the hint
    f2.value, f2.attempt_at, f2.error = None, f.attempt_at, f.error
    assert SC.render("ft8_ticker", f2, 0, size=size).tobytes() != img.tobytes()


@pytest.mark.parametrize("size", [(16, 16), (64, 32), (256, 16), (96, 24), (64, 16), (64, 64)])
def test_setup_frame_adaptive_screens(size):
    key = next(k for k, s in SC.SCREENS.items() if getattr(s, "adaptive", False))
    f = SC.SCREENS[key].feed()
    f.attempt_at, f.error, f.hint = 1.0, "SetupNeeded: x", FT.NOT_SET_HINT
    img = SC.render(key, f, 0, size=size)
    assert img.size == size and A.AMB in cols(img)
    f.hint = None
    assert SC.render(key, f, 0, size=size).tobytes() != img.tobytes()


def test_setup_frame_texts_fit(no_port, monkeypatch):
    from orcpixels.tinyfont import text_width
    from orcpixels.livedata_screens import big_w
    assert text_width(FT.NOT_SET_HINT[0]) <= 62                       # 64x16 line 2, whole
    assert all(big_w(w) <= 62 for w in FT.NOT_SET_HINT[0].split())   # 64x64 big words
    assert text_width("COM16 BUSY") <= 62
