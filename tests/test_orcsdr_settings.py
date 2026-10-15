"""Settings > OrcSDR connection card (2026-10-15): port list with friendly names and the
Espressif ports flagged, Test connection (network / USB, busy port), saving through the
feed-config settings API, and the links from the FT8 set-up badge / Ham Radio category.
Mocked ports and HTTP only."""
import urllib.error
from pathlib import Path

import pytest

from orcpixels import categories as CAT
from orcpixels import livedata_screens as SC
from orcpixels import settings as S
from orcpixels.sources import ft8feed as FT
from orcpixels.sources import livefeeds as LF
from tests.test_ft8 import FakeSerial, fetch_ft8  # noqa: F401
from tests.test_ft8_autodetect import Dial, Ports, PORTS
from tests.test_livedata import net  # noqa: F401  (autouse: mocked HTTP, temp settings, no serial)
from tests.test_routes_http import asgi

HTML = (Path(__file__).resolve().parents[1] / "orcpixels" / "static" / "index.html").read_text(encoding="utf-8")
WIN_PORTS = [("COM14", FT.ESPRESSIF_VID, 0x1001, "USB Serial Device (COM14)"),
             ("COM16", FT.ESPRESSIF_VID, 0x1001, "USB Serial Device (COM16)"),
             ("COM40", 0x1A86, 0x55D3, "USB-Enhanced-SERIAL CH343 (COM40)"),
             ("COM5", None, None, "Standard Serial over Bluetooth link (COM5)")]


@pytest.fixture
def app(tmp_path, monkeypatch):
    from orcpixels import playlist as PL
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    from orcpixels.web import create_app
    return create_app(address=None)


def _404(url, headers, timeout):
    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def test_port_list_friendly_and_flagged(app, monkeypatch):
    Ports(monkeypatch, ports=WIN_PORTS)
    FT.SOURCE.auto_port = "COM16"
    st, r = asgi(app, "GET", "/api/livedata/orcsdr")
    assert st == 200 and r["baud"] == "115200" and "115200" in r["bauds"]
    devs = [p["device"] for p in r["ports"]]
    assert devs == ["COM16", "COM14", "COM5", "COM40"]                      # OrcSDR, Espressif, then the rest
    p17, p14, p40 = r["ports"][0], r["ports"][1], r["ports"][3]
    assert p17["label"] == "COM16: USB Serial Device [OrcSDR]" and p17["orcsdr"] and p17["espressif"]
    assert p14["label"] == "COM14: USB Serial Device [Espressif USB: OrcSDR?]" and not p14["orcsdr"]
    assert p40["label"] == "COM40: USB-Enhanced-SERIAL CH343" and not p40["espressif"]
    assert [s[0] for s in r["sources"]] == ["auto", "serial", "http", "off"]
    assert r["url"] == "http://192.0.2.10" and r["home_grid_auto"] == "CN85"


def test_test_connection_busy_port(app, monkeypatch):
    p = Ports(monkeypatch, ports=WIN_PORTS, busy={"COM16"})
    st, r = asgi(app, "POST", "/api/livedata/orcsdr/test", {"source": "serial", "port": "COM16", "baud": "115200"})
    assert st == 200 and not r["ok"] and r["message"].startswith("COM16 busy: another app has it open")
    assert p.opened == [] and S.load().get("livedata_ft8_source") is None          # nothing saved


def test_test_connection_ok_port(app, monkeypatch):
    p = Ports(monkeypatch, ports=WIN_PORTS)
    st, r = asgi(app, "POST", "/api/livedata/orcsdr/test", {"source": "serial", "port": "com16"})
    assert st == 200 and r["ok"] and r["via"] == "serial" and r["port"] == "COM16"
    assert "OrcSDR answered on COM16" in r["message"] and "FT8 20m" in r["message"] and "14.074 MHz" in r["message"]
    assert [s.written for s in p.opened] == [[b"FT8 STATUS\n"]]                     # one read-only query


def test_test_connection_auto_network_404_then_usb_scan(app, monkeypatch):
    p = Ports(monkeypatch, ports=WIN_PORTS)
    monkeypatch.setattr(LF, "HTTP", _404)
    st, r = asgi(app, "POST", "/api/livedata/orcsdr/test", {"source": "auto", "port": "", "url": "192.0.2.10"})
    assert st == 200 and r["ok"] and r["port"] == "COM16"
    assert "no /api/ft8 yet" in r["lines"][0] and "http://192.0.2.10" in r["lines"][0]
    assert "COM14: no FT8 STATUS reply" in r["lines"][1]
    assert {s.port for s in p.opened} == {"COM14", "COM16"}                        # CH343 / BT never opened
    assert FT.SOURCE.auto_port == "COM16" and r["conn"]["ports"][0]["device"] == "COM16"


def test_test_connection_network_ok(app, monkeypatch, net):
    net.override["/api/ft8"] = "ft8_api.json"
    Ports(monkeypatch, ports=[])
    st, r = asgi(app, "POST", "/api/livedata/orcsdr/test", {"source": "http", "url": "http://10.0.0.9"})
    assert st == 200 and r["ok"] and r["via"] == "http" and r["message"].startswith("Network OK: http://10.0.0.9")


def test_test_connection_validates(app, monkeypatch):
    Ports(monkeypatch, ports=[])
    assert asgi(app, "POST", "/api/livedata/orcsdr/test", {"port": "COM9; rm"})[0] == 400
    assert asgi(app, "POST", "/api/livedata/orcsdr/test", {"baud": "1234"})[0] == 400
    st, r = asgi(app, "POST", "/api/livedata/orcsdr/test", {"source": "serial"})
    assert st == 200 and not r["ok"] and "no Espressif USB serial port" in r["message"]


def test_save_takes_effect_live(app, monkeypatch):
    Ports(monkeypatch, ports=WIN_PORTS)
    FT.SOURCE.auto_port, FT.SOURCE.http_off_until = "COM14", 9e12
    FT.SOURCE.detect_fail = LF.SetupNeeded("x")
    st, r = asgi(app, "POST", "/api/livedata/feed-config", {"values": {
        "livedata_ft8_source": "serial", "livedata_ft8_port": "com16", "livedata_ft8_baud": "115200",
        "livedata_ft8_url": "", "livedata_ft8_home_grid": "cn85ab"}})
    assert st == 200
    cur = S.load()
    assert cur["livedata_ft8_port"] == "COM16" and cur["livedata_ft8_source"] == "serial" and cur["livedata_ft8_home_grid"] == "CN85ab"
    assert FT.SOURCE.auto_port == "" and FT.SOURCE.detect_fail is None and FT.SOURCE.http_off_until == 0.0
    assert FT.SOURCE.polled_at == 0.0


def test_ft8_feeds_point_at_the_card(app, monkeypatch):
    Ports(monkeypatch, ports=[], orcsdr=None)
    S.save({"livedata_ft8_source": "serial"})
    cur = LF._setting
    monkeypatch.setattr(LF, "_setting", lambda k: None if k == "livedata_ft8_port" else cur(k))
    f = fetch_ft8()
    assert f.state()["setup_card"] == "card-sdrconn" and f.state()["hint"] == list(FT.NOT_SET_HINT)
    assert LF.Feed().state()["setup_card"] is None
    ham = next(c for c in CAT.as_json(SC.SCREENS) if c["id"] == "ham")
    assert {"title": "OrcSDR connection (FT8 port / URL)", "card": "card-sdrconn"} in ham["links"]


def test_card_markup():
    assert 'id="card-sdrconn"' in HTML and "OrcSDR connection" in HTML
    card = HTML[HTML.index('id="card-sdrconn"'):HTML.index('<div class="card"><h2>Connection</h2>')]
    for opt in ('value="auto">Auto', 'value="serial">USB serial', 'value="http">Network'):
        assert opt in card
    for el in ('id="sc_port"', 'id="sc_baud"', 'id="sc_url"', 'id="sc_grid"', ">Refresh<", ">Test connection<", ">Save<"):
        assert el in card
    assert "openCard(s.setup_card)" in HTML and "openCard('card-sdrconn')" in HTML and "function openCard(" in HTML
    assert HTML.index('id="card-sdrconn"') > HTML.index('id="tab-settings"')
