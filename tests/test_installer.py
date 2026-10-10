"""Installed-build plumbing: data paths, LAN guard, tray helpers (no GUI, no registry)."""
import asyncio
import importlib

import pytest


def test_paths_checkout_vs_installed(monkeypatch, tmp_path):
    from orcpixels import paths
    assert paths.DATA == paths.APP and not paths.SEPARATE          # dev checkout: unchanged
    monkeypatch.setenv("ORCPIXELS_HOME", str(tmp_path / "home"))
    p2 = importlib.reload(paths)
    try:
        assert p2.CONFIG == tmp_path / "home" / "config" and p2.SEPARATE
        assert p2.config_file("apps.json") == p2.APP / "config" / "apps.json"   # shipped default
        (p2.CONFIG).mkdir(parents=True)
        (p2.CONFIG / "apps.json").write_text("{}")
        assert p2.config_file("apps.json") == p2.CONFIG / "apps.json"
        assert p2.user_presets("presets") == tmp_path / "home" / "presets"
    finally:
        monkeypatch.delenv("ORCPIXELS_HOME")
        importlib.reload(paths)


def test_install_json_sets_library(monkeypatch, tmp_path):
    from orcpixels import settings as S
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(S, "INSTALL", tmp_path / "install.json")
    (tmp_path / "install.json").write_text('{"library_dir": "D:\\\\Media\\\\Lib", "rotate_180": true}')
    cfg = S.load()
    assert cfg["library_dir"] == "D:\\Media\\Lib" and cfg["rotate_180"] is False   # only whitelisted keys
    assert cfg["listen_lan"] is False and cfg["port"] == 8765


def _call(app, client):
    sent = []

    async def recv():
        return {"type": "http.request", "body": b""}

    async def send(m):
        sent.append(m)
    scope = {"type": "http", "client": (client, 5000), "headers": [], "query_string": b"", "path": "/"}
    asyncio.run(app(scope, recv, send))
    return sent


def test_lan_guard(monkeypatch):
    from orcpixels import settings as S
    from orcpixels.web import LanGuard
    hit = []

    async def inner(scope, r, s):
        hit.append(scope["client"][0])
    g = LanGuard(inner)
    monkeypatch.setattr(S, "load", lambda: {"lan_token": "s3cret"})
    _call(g, "127.0.0.1")
    assert _call(g, "198.51.100.20")[0]["status"] == 401
    monkeypatch.setattr(S, "load", lambda: {"lan_token": ""})
    _call(g, "198.51.100.20")
    assert hit == ["127.0.0.1", "198.51.100.20"]


def test_tray_addresses():
    from orcpixels import tray
    a = tray.addresses(8765, False)
    assert a == ["http://127.0.0.1:8765"]
    assert tray.addresses(8765, True)[-1] == "http://127.0.0.1:8765"
    assert all(not ip.startswith("127.") for ip in tray.lan_ips())
