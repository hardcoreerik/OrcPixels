"""OTA updater: semver, channel pick, GitHub API mock (ETag/304, rate limits, cache),
download + SHA-256 verify, install flow (mocked launcher), dev-mode notice, web routes."""
import hashlib
import io
import json
import time
import urllib.error

import pytest

from orcpixels import __version__
from orcpixels import paths
from orcpixels import settings as S
from orcpixels import updater as UP

EXE = b"MZ fake installer " * 1000


def rel(tag, pre=False, draft=False, exe=True, sums=True, body="## Changes\n- **faster** things"):
    v = tag.lstrip("v")
    a = []
    if exe:
        a.append({"name": f"OrcPixels-Setup-{v}.exe", "size": len(EXE),
                  "browser_download_url": f"https://dl.test/{v}/OrcPixels-Setup-{v}.exe"})
    if sums:
        a.append({"name": "SHA256SUMS", "browser_download_url": f"https://dl.test/{v}/SHA256SUMS"})
    return {"tag_name": tag, "name": tag, "prerelease": pre, "draft": draft, "body": body,
            "html_url": f"https://github.com/x/releases/tag/{tag}", "assets": a, "published_at": "2026-10-10T00:00:00Z"}


class Resp(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeGH:
    def __init__(self, releases, exe=EXE, sums=None):
        self.releases, self.exe, self.calls, self.etag = releases, exe, [], '"e1"'
        self.sums = sums
        self.limit = None

    def __call__(self, url, headers=None, timeout=15.0):
        self.calls.append((url, dict(headers or {})))
        if url == UP.API:
            if self.limit is not None:
                raise urllib.error.HTTPError(url, 403, "rate", {"X-RateLimit-Remaining": "0",
                                                                "X-RateLimit-Reset": str(int(time.time()) + 900)}, None)
            if (headers or {}).get("If-None-Match") == self.etag:
                raise urllib.error.HTTPError(url, 304, "nm", {"X-RateLimit-Remaining": "59"}, None)
            return Resp(json.dumps(self.releases).encode(), {"ETag": self.etag, "X-RateLimit-Remaining": "58",
                                                             "X-RateLimit-Reset": "1"})
        if url.endswith("SHA256SUMS"):
            v = url.split("/")[-2]
            text = self.sums if self.sums is not None else f"{hashlib.sha256(self.exe).hexdigest()}  OrcPixels-Setup-{v}.exe\n"
            return Resp(text.encode())
        if url.endswith(".exe"):
            return Resp(self.exe, {"Content-Length": str(len(self.exe))})
        raise AssertionError(url)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "OUT", tmp_path / "out")
    monkeypatch.setattr(S, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(S, "INSTALL", tmp_path / "install.json")
    monkeypatch.setenv("ORCPIXELS_UPDATE_MODE", "installed")
    monkeypatch.setattr(UP, "__version__", "0.2.1")

    def use(releases, **kw):
        gh = FakeGH(releases, **kw)
        monkeypatch.setattr(UP, "_get", gh)
        return gh
    return use


def test_semver():
    assert UP.newer("0.2.2", "0.2.1") and UP.newer("v0.10.0", "0.9.9") and not UP.newer("0.2.1", "0.2.1")
    assert UP.newer("0.3.0", "0.3.0-beta.2") and UP.newer("0.3.0-beta.10", "0.3.0-beta.2")
    assert UP.newer("0.3.0-beta.1", "0.2.9") and not UP.newer("junk", "0.1.0")


def test_pick_channels():
    rs = [rel("v0.2.1"), rel("v0.2.2"), rel("v0.3.0-beta.1", pre=True), rel("v9.9.9", draft=True), rel("nightly")]
    assert UP.pick(rs, "stable")["version"] == "0.2.2"
    b = UP.pick(rs, "beta")
    assert b["version"] == "0.3.0-beta.1" and b["prerelease"]
    assert UP.pick(rs, "stable")["installer"]["name"] == "OrcPixels-Setup-0.2.2.exe"


def test_check_caches_and_uses_etag(env):
    gh = env([rel("v0.2.1"), rel("v0.2.2")])
    st = UP.check()
    assert st["available"] and st["latest"]["version"] == "0.2.2" and st["can_install"]
    assert "faster" in st["latest"]["notes"]
    n = len(gh.calls)
    UP.check()                                   # fresh cache: no request at all
    assert len(gh.calls) == n
    c = json.loads(UP.cache_path().read_text())
    c["checked_ts"] -= UP.INTERVAL_S
    UP.cache_path().write_text(json.dumps(c))
    st = UP.check()                              # stale: conditional request -> 304, cached list
    assert gh.calls[-1][1].get("If-None-Match") == '"e1"' and st["available"]


def test_manual_check_throttle_and_rate_limit(env):
    gh = env([rel("v0.2.2")])
    UP.check(force=True)
    n = len(gh.calls)
    UP.check(force=True)                         # within a minute: cached
    assert len(gh.calls) == n
    c = json.loads(UP.cache_path().read_text())
    c["checked_ts"] -= 3600
    c.pop("etag")
    UP.cache_path().write_text(json.dumps(c))
    gh.limit = True
    st = UP.check(force=True)
    assert "rate limit" in st["error"] and st["available"]          # keeps the last good answer
    n = len(gh.calls)
    c = json.loads(UP.cache_path().read_text())
    c["checked_ts"] -= 3600
    UP.cache_path().write_text(json.dumps(c))
    UP.check(force=True)                         # rate limited until reset: no request
    assert len(gh.calls) == n


def test_up_to_date_and_channel_setting(env):
    env([rel("v0.2.1"), rel("v0.3.0-beta.1", pre=True)])
    assert not UP.check()["available"]
    S.save({"update_channel": "beta"})
    st = UP.check()                              # channel change invalidates the cache
    assert st["available"] and st["latest"]["version"] == "0.3.0-beta.1"


def test_source_checkout_notice_only(env, monkeypatch):
    env([rel("v0.2.2")])
    monkeypatch.setenv("ORCPIXELS_UPDATE_MODE", "source")
    st = UP.check()
    assert st["available"] and not st["can_install"] and "git pull" in st["notice"]
    with pytest.raises(PermissionError):
        UP.install(lambda: None, launcher=lambda e, a: None)


def test_release_without_sums_is_not_installable(env):
    env([rel("v0.2.2", sums=False)])
    st = UP.check()
    assert st["available"] and not st["can_install"] and "SHA256SUMS" in st["notice"]


def test_download_verifies(env, tmp_path):
    env([rel("v0.2.2")])
    latest = UP.check()["latest"]
    p = UP.download(latest, tmp_path / "dl")
    assert p.read_bytes() == EXE and p.name == "OrcPixels-Setup-0.2.2.exe"


def test_download_rejects_bad_hash(env, tmp_path):
    env([rel("v0.2.2")], sums="0" * 64 + "  OrcPixels-Setup-0.2.2.exe\n")
    latest = UP.check()["latest"]
    with pytest.raises(ValueError, match="mismatch"):
        UP.download(latest, tmp_path / "dl")
    assert not list((tmp_path / "dl").glob("*.exe")) and not list((tmp_path / "dl").glob("*.part"))


def test_download_rejects_missing_entry(env, tmp_path):
    env([rel("v0.2.2")], sums="ab" * 32 + "  something-else.exe\n")
    with pytest.raises(ValueError, match="no entry"):
        UP.download(UP.check()["latest"], tmp_path / "dl")


def test_install_flow(env, monkeypatch, tmp_path):
    env([rel("v0.2.2")])
    UP.check()
    monkeypatch.setattr(UP, "install_dir", lambda: tmp_path / "app")
    (tmp_path / "app").mkdir()
    launched, shut = [], []
    monkeypatch.setattr(UP.threading, "Timer", lambda s, f: type("T", (), {"start": lambda self: f()})())
    r = UP.install(lambda: shut.append(1), launcher=lambda exe, args: launched.append((exe, args)))
    exe, args = launched[0]
    assert exe.read_bytes() == EXE and r["version"] == "0.2.2" and shut == [1]
    assert "/SILENT" in args and "/RELAUNCH=1" in args and "/CURRENTUSER" in args
    assert any(a.startswith("/DIR=") and str(tmp_path / "app") in a for a in args)
    assert any(a.startswith("/CONFIGDIR=") for a in args) and any(a.startswith("/LIBRARYDIR=") for a in args)
    assert UP.status()["install"]["state"] == "installing"


def test_install_failure_keeps_running(env, monkeypatch, tmp_path):
    env([rel("v0.2.2")], sums="0" * 64 + "  OrcPixels-Setup-0.2.2.exe\n")
    UP.check()
    shut = []
    with pytest.raises(ValueError):
        UP.install(lambda: shut.append(1), launcher=lambda e, a: None)
    assert not shut and UP.status()["install"]["state"] == "failed"


def test_admin_folder_uses_allusers(env, monkeypatch, tmp_path):
    monkeypatch.setattr(UP, "needs_admin", lambda d=None: True)
    assert "/ALLUSERS" in UP.installer_args(tmp_path)


def test_web_routes(env, monkeypatch, tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from orcpixels import displays as D
    from orcpixels import playlist as PL
    from orcpixels import web
    env([rel("v0.2.2")])
    monkeypatch.setattr(D, "PATH", tmp_path / "displays.json")          # no saved signs: nothing connects
    monkeypatch.setattr(PL, "PLAYLISTS", tmp_path / "playlists.json")
    monkeypatch.setattr(PL, "SLOTS", tmp_path / "device_slots.json")
    app = web.create_multi_app()
    with TestClient(app) as c:
        assert c.get("/api/update").json()["current"] == "0.2.1"
        st = c.post("/api/update/check").json()
        assert st["available"] and st["latest"]["version"] == "0.2.2"
        st = c.post("/api/update/settings", json={"update_check": False, "update_channel": "beta"}).json()
        assert st["enabled"] is False and st["channel"] == "beta"
        assert c.post("/api/update/settings", json={"update_channel": "nightly"}).status_code == 422
        monkeypatch.setenv("ORCPIXELS_UPDATE_MODE", "source")
        assert c.post("/api/update/install").status_code == 403


def test_background_disabled_in_tests():
    t = UP.start_background()
    assert t.stop_event.is_set()
    t.join(2)
    assert not t.is_alive()
