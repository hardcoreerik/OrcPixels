"""Multi-display support (2026-10-08): device registry, strict-address connect, per-display
routing / settings / playlists, and the curated 64x64 set (presets64/, live64, OrcSDR, AI)."""
import asyncio
import json
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from orcpixels import ble_sign as BS
from orcpixels import displays as D
from orcpixels import playlist as PL
from orcpixels import presets as PR
from orcpixels import settings as S
from orcpixels.loopmetric import loop_metric_frames

ROOT = Path(__file__).resolve().parents[1]
A16, A64 = "AA:BB:CC:00:16:64", "AA:BB:CC:00:64:64"     # made-up addresses
TWO = [{"id": "sign16", "label": "64x16 sign", "name": "LED_BLE_00001664", "address": A16, "width": 64,
        "height": 16, "rotate_180": None, "autoconnect": True},
       {"id": "matrix64", "label": "64x64 matrix", "name": "LED_BLE_00006464", "address": A64, "width": 64,
        "height": 64, "rotate_180": None, "autoconnect": False}]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "PATH", tmp_path / "displays.json")
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    (tmp_path / "displays.json").write_text(json.dumps({"displays": TWO}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "PATH", tmp_path / "displays.json")
    return tmp_path


def test_fresh_install_has_one_unconfigured_display_that_the_first_sign_fills(fresh):
    ds = D.load()
    assert len(ds) == 1 and not D.configured(ds[0]) and ds[0]["address"] == ""
    d = D.add({"address": A16.lower(), "label": "mine", "width": 64, "height": 16})
    assert d["id"] == ds[0]["id"] and len(D.load()) == 1 and D.configured(D.load()[0])
    d2 = D.add({"address": A64, "label": "big", "width": 64, "height": 64})
    assert [x["id"] for x in D.load()] == [ds[0]["id"], d2["id"]]


def call(app, method, path, body=None, query="", headers=()):
    raw = json.dumps(body).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
             "query_string": query.encode(), "server": ("test", 80), "client": ("test", 1),
             "headers": [(b"content-type", b"application/json"),
                         (b"content-length", str(len(raw)).encode()), *headers]}
    out = {"body": b"", "status": None, "headers": []}
    msgs = [{"type": "http.request", "body": raw, "more_body": False}]

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"], out["headers"] = m["status"], m.get("headers", [])
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    asyncio.run(app(scope, receive, send))
    try:
        data = json.loads(out["body"] or b"null")
    except ValueError:
        data = out["body"].decode("utf-8", "replace")
    return out["status"], data, out["headers"]


# ----------------------------------------------------------------- registry
def test_registry_defaults_have_both_known_signs(cfg):
    ds = D.load()
    by = {d["id"]: d for d in ds}
    assert ds[0]["id"] == "sign16" and D.primary_id() == "sign16"
    assert by["sign16"]["address"] == A16 and (by["sign16"]["width"], by["sign16"]["height"]) == (64, 16)
    assert by["matrix64"]["address"] == A64 and (by["matrix64"]["width"], by["matrix64"]["height"]) == (64, 64)
    assert by["matrix64"]["autoconnect"] is False          # only connected from the UI


def test_registry_validation(cfg):
    with pytest.raises(ValueError):
        D.add({"address": "not-an-address", "width": 64, "height": 16})
    with pytest.raises(ValueError):
        D.add({"address": A16.lower(), "width": 64, "height": 16})     # duplicate address
    d = D.add({"address": "AA:BB:CC:DD:EE:01", "label": "spare", "width": 32, "height": 32})
    assert D.get(d["id"])["address"] == "AA:BB:CC:DD:EE:01"
    D.remove(d["id"])
    with pytest.raises(ValueError):
        D.remove("sign16")                                  # the primary stays


# ----------------------------------------------------------------- strict connect
def test_find_matches_only_the_saved_address(monkeypatch):
    """Root cause of the wrong-sign reconnect: any LED_BLE_* name used to match."""
    seen = []
    adverts = [types.SimpleNamespace(address=A64, name="LED_BLE_d0736074"),     # 64x64 first
               types.SimpleNamespace(address=A16, name="LED_BLE_8D8235E2")]

    class Scanner:
        @staticmethod
        async def find_device_by_filter(match, timeout=10.0):
            for d in adverts:
                seen.append(d.address)
                if match(d, types.SimpleNamespace(local_name=d.name)):
                    return d
            return None
    monkeypatch.setattr(BS, "BleakScanner", Scanner)
    dev = asyncio.run(BS.SignClient(address=A16)._find())
    assert dev.address == A16 and seen[0] == A64            # the 64x64 was seen and refused
    adverts.pop(1)
    with pytest.raises(ConnectionError):                    # 16x64 off: never falls back to the 64x64
        asyncio.run(BS.SignClient(address=A16)._find())


# ----------------------------------------------------------------- multi app
@pytest.fixture
def multi(cfg):
    from orcpixels.web import create_multi_app
    return create_multi_app()


def test_displays_list_and_routing(multi):
    st, r, _ = call(multi, "GET", "/api/displays")
    assert st == 200 and [d["id"] for d in r["displays"]] == ["sign16", "matrix64"]
    assert r["selected"] == "sign16" and not any(d["connected"] for d in r["displays"])
    st, r, _ = call(multi, "GET", "/d/matrix64/api/status")
    assert st == 200 and r["display"]["id"] == "matrix64" and (r["display"]["width"], r["display"]["height"]) == (64, 64)
    st, r, _ = call(multi, "GET", "/api/status", headers=[(b"x-display", b"matrix64")])
    assert r["display"]["id"] == "matrix64"
    st, r, _ = call(multi, "GET", "/api/status", query="display=matrix64")
    assert r["display"]["id"] == "matrix64"
    st, r, _ = call(multi, "GET", "/api/status")
    assert r["display"]["id"] == "sign16"


def test_select_sets_cookie_and_cookie_routes(multi):
    st, r, hdrs = call(multi, "POST", "/api/displays/select", {"id": "matrix64"})
    assert st == 200
    cookie = [v.decode() for k, v in hdrs if k == b"set-cookie"]
    assert cookie and cookie[0].startswith("opdisplay=matrix64")
    st, r, _ = call(multi, "GET", "/api/status", headers=[(b"cookie", b"opdisplay=matrix64")])
    assert r["display"]["id"] == "matrix64"
    st, html, _ = call(multi, "GET", "/", headers=[(b"cookie", b"opdisplay=matrix64")])
    assert 'window.OP_DISPLAY={"id": "matrix64"' in html and '"height": 64' in html
    st, r, _ = call(multi, "POST", "/api/displays/select", {"id": "nope"})
    assert st == 404


def test_per_display_playlists_seed_rename_delete(multi):
    st, r, _ = call(multi, "POST", "/api/playlists/Default",
                    {"items": [{"preset": "orcsdr_wave_small_anim"}, {"preset": "p_orco_marquee"}]})
    assert st == 200
    st, r, _ = call(multi, "GET", "/d/matrix64/api/playlists")
    assert st == 200 and [i["preset"] for i in r["playlists"]["Default"]["items"]] == \
        ["orcsdr_wave_small_anim", "p_orco_marquee"]           # seeded from the primary's Default
    st, r, _ = call(multi, "POST", "/d/matrix64/api/playlists/Night",
                    {"items": [{"preset": "p_orco_night"}, {"preset": "p_amb_starfield"}]})
    assert st == 200
    st, r, _ = call(multi, "POST", "/d/matrix64/api/playlists/Night/rename", {"name": "Late nite"})
    assert st == 200 and r["name"] == "Late nite"
    st, r, _ = call(multi, "POST", "/d/matrix64/api/playlists/Default/rename", {"name": "Late nite"})
    assert st == 400                                              # name taken
    st, r, _ = call(multi, "GET", "/d/matrix64/api/playlists")
    assert set(r["playlists"]) == {"Default", "Late nite"}
    st, r, _ = call(multi, "GET", "/api/playlists")
    assert set(r["playlists"]) == {"Default"}                    # the 16x64 never sees them
    st, _, _ = call(multi, "DELETE", "/d/matrix64/api/playlists/Late nite")
    st, r, _ = call(multi, "GET", "/d/matrix64/api/playlists")
    assert set(r["playlists"]) == {"Default"}
    assert (PL.PLAYLISTS.parent / "displays" / "matrix64" / "playlists.json").exists()


def test_rotation_and_settings_are_per_display(multi, cfg):
    S.save({"rotate_180": True, "livedata_interval": 7})
    st, r, _ = call(multi, "POST", "/d/matrix64/api/settings", {"rotate_180": False})
    assert st == 200
    assert D.get("matrix64")["rotate_180"] is False                # stored in the registry
    with D.use(D.get("matrix64")):
        assert S.get("rotate_180") is False
    assert S.get("rotate_180") is True                             # 16x64 untouched
    assert multi.apps["sign16"].state.sign.rotate_180 is True
    with D.use(multi.displays["matrix64"]):
        assert multi.apps["matrix64"].state.sign.rotate_180 is False


def test_slot_records_are_per_display(multi):
    """The old shared record made an idle-resume play the 16x64's playlist on the 64x64."""
    PL._save_record({"slots": {"1": {"preset": "x"}}, "active": {"playlist": "Default", "slots": [1]}})
    with D.use(D.get("matrix64")):
        assert not PL.slot_record().get("active")


def test_secondary_display_does_not_autoconnect(multi, monkeypatch):
    calls = []

    async def fake_connect(self):
        calls.append(self.address)
    monkeypatch.setattr(BS.SignClient, "connect", fake_connect)

    async def run():
        sent = []
        msgs = [{"type": "lifespan.startup"}]

        async def receive():
            if msgs:
                return msgs.pop(0)
            await asyncio.sleep(0.05)
            return {"type": "lifespan.shutdown"}

        async def send(m):
            sent.append(m["type"])
        await multi({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
        return sent
    sent = asyncio.run(run())
    assert "lifespan.startup.complete" in sent
    assert calls == [A16]
    assert multi.apps["matrix64"].state.sign.want_connected is False


# ----------------------------------------------------------------- curated 64x64 set
def _gif_frames(path):
    im = Image.open(path)
    out = []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        out.append(im.convert("RGB"))
    return out


def test_presets64_cover_every_preset_with_seamless_64x64_loops():
    names = sorted(p.stem for p in (ROOT / "presets").glob("*.json"))
    assert len(names) >= 97
    bad = []
    for n in names:
        meta = json.loads((ROOT / "presets64" / f"{n}.json").read_text(encoding="utf-8"))
        src = json.loads((ROOT / "presets" / f"{n}.json").read_text(encoding="utf-8"))
        assert meta.get("family") == src.get("family") and meta.get("size") in ("64x64", [64, 64]), n
        f = ROOT / "presets64" / meta["file"]
        frames = _gif_frames(f) if f.suffix == ".gif" else [Image.open(f).convert("RGB")]
        assert all(fr.size == (64, 64) for fr in frames), n
        if f.stat().st_size > 52_000:
            bad.append((n, "size", f.stat().st_size))
        if len(frames) > 1 and not loop_metric_frames(frames)["ok"]:
            bad.append((n, "loop"))
    assert not bad, bad


def test_presets_resolve_by_display_size(cfg):
    assert PR.get("p_orco_marquee", (64, 16))["_dir"] != PR.get("p_orco_marquee", (64, 64))["_dir"]
    with D.use(D.get("matrix64")):
        assert Path(PR.get("p_orco_marquee")["_dir"]).name == "presets64"
        assert len(PR.list_presets()) == len(PR.list_presets((64, 16)))


def test_live64_every_screen_renders_64x64_and_loops(monkeypatch):
    from orcpixels.sources import livefeeds as LF
    monkeypatch.setattr(LF, "HTTP", LF.HTTP)
    sys.path.insert(0, str(ROOT / "tools"))
    import led64_sheet
    rows = led64_sheet.live_rows(4)
    assert len(rows) >= 39
    for key, sub, frames in rows:
        assert all(f.size == (64, 64) for f in frames), key
        assert "seamless" in sub, (key, sub)


def test_orcsdr_and_ai_render_at_64x64():
    from orcpixels import ai_status as AI
    from orcpixels import orcsdr_live as OL
    assert AI.render_status(None, (64, 64))[0][0].size == (64, 64)
    assert AI.render_status(None, (64, 16))[0][0].size == (64, 16)
    import inspect
    assert "render_frame64" in dir(OL) and inspect.isfunction(OL.render_frame64)


def test_slot_record_written_for_another_sign_is_ignored(cfg):
    PL._save_record({"slots": {"1": {"preset": "x"}}, "active": {"playlist": "64x64", "slots": [1]}})
    assert PL.slot_record()["active"]["playlist"] == "64x64"
    rec = json.loads(PL.SLOTS.read_text(encoding="utf-8"))
    assert rec["address"] == A16
    rec["address"] = A64                                   # as if written while talking to the 64x64
    PL.SLOTS.write_text(json.dumps(rec), encoding="utf-8")
    r = PL.slot_record()
    assert r["active"] is None and r["foreign_record"] == A64


def test_show_and_thumbnails_use_the_64x64_variant(multi):
    """Presets tab with Matrix 64x64 selected: Show sends the presets64 file, thumbnails are
    the curated 64x64 frames at the exact tile size (192 px = scale 3)."""
    sent = []
    sign = multi.apps["matrix64"].state.sign

    async def fake_gif(data, *a, **k):
        sent.append(data)
    sign.send_gif = fake_gif
    st, r, _ = call(multi, "POST", "/d/matrix64/api/presets/p_orco_radar/show")
    assert st == 200, r
    assert sent and sent[0] == (ROOT / "presets64" / "p_orco_radar.gif").read_bytes()
    st, body, hdrs = call(multi, "GET", "/d/matrix64/api/presets/p_orco_radar/led", query="scale=3")
    assert st == 200
    import io
    from orcpixels.studio import led_thumb
    with D.use(multi.displays["matrix64"]):
        data, mt = led_thumb(PR.file_path(PR.get("p_orco_radar")), 3)
    assert mt == "image/gif" and Image.open(io.BytesIO(data)).size == (192, 192)
    html = (ROOT / "orcpixels" / "static" / "index.html").read_text(encoding="utf-8")
    assert "function psrc(p,scale=(SQ?3:4))" in html and "html.sq .pcard img,html.sq .ldtile img{width:192px;height:192px}" in html
