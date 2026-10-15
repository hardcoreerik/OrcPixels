"""Over-the-air updates for the installed Windows build (docs/UPDATES.md).

check()    GitHub Releases API (public repo), at startup + every 24 h unless turned off;
           conditional requests (ETag, 304s don't count against the 60/h anonymous limit),
           result cached in DATA/out/update.json, rate-limit headers respected.
install()  only on an explicit "Update now": download OrcPixels-Setup-<v>.exe to a temp
           folder, verify its SHA-256 against the release's SHA256SUMS asset, start it with
           /SILENT (UAC prompt when the install folder needs admin), stop the server
           cleanly and exit; the installer waits for this process to be gone (/WAITPID),
           installs, and relaunches OrcPixels (also after a failed install).
reconcile_install()  at startup: read the installer's log (/LOG) and record whether the
           last update installed or rolled back, and why. User data (%APPDATA%\\OrcPixels,
           the Library) is never touched by an update.
Source checkouts / dev mode never self-update: they only get a notice ("git pull").
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from . import paths

log = logging.getLogger("orcpixels.update")
REPO = "hardcoreerik/OrcPixels"
API = os.environ.get("ORCPIXELS_UPDATE_API") or f"https://api.github.com/repos/{REPO}/releases?per_page=20"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
INTERVAL_S = 24 * 3600
MIN_MANUAL_S = 60                 # "Check for updates" at most once a minute
ASSET_RE = re.compile(r"^OrcPixels-Setup-(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\.exe$")
SUMS_NAMES = ("SHA256SUMS", "SHA256SUMS.txt")
UA = f"OrcPixels/{__version__} (+{RELEASES_PAGE})"
HARD_EXIT_S = 30.0               # "Update now": exit for sure, even if a thread hangs on shutdown
_lock = threading.Lock()


# ------------------------------------------------------------------ versions
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def parse(v: str):
    m = _SEMVER.match((v or "").strip())
    if not m:
        return None
    pre = m.group(4)
    # semver precedence: a prerelease sorts before the release; numeric ids numerically
    pre_key = (1,) if pre is None else (0,) + tuple((0, int(x), "") if x.isdigit() else (1, 0, x)
                                                     for x in pre.split("."))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) + (pre_key,)


def newer(a: str, b: str) -> bool:
    """a > b (semver)."""
    pa, pb = parse(a), parse(b)
    return bool(pa and pb and pa > pb)


# ------------------------------------------------------------------ mode
def mode() -> str:
    """installed (frozen build: can self-update) | source (checkout / pip / dev: notice only)."""
    if os.environ.get("ORCPIXELS_UPDATE_MODE") in ("installed", "source"):
        return os.environ["ORCPIXELS_UPDATE_MODE"]
    return "installed" if paths.FROZEN else "source"


def _settings() -> dict:
    from . import settings as S
    try:
        return S.load()
    except Exception:  # noqa: BLE001
        return dict(S.DEFAULTS)


# ------------------------------------------------------------------ cache
def cache_path() -> Path:
    return paths.OUT / "update.json"


def _read_cache() -> dict:
    try:
        return json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(c: dict) -> None:
    try:
        cache_path().parent.mkdir(parents=True, exist_ok=True)
        cache_path().write_text(json.dumps(c, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("update cache: %s", e)


# ------------------------------------------------------------------ GitHub
def _get(url: str, headers: dict | None = None, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)          # noqa: S310 (https / test mock)


def fetch_releases(cache: dict) -> tuple[list | None, dict]:
    """Conditional GET. Returns (releases or None if unchanged/blocked, new cache fields)."""
    now = time.time()
    if cache.get("rate_reset") and cache.get("rate_remaining") == 0 and now < cache["rate_reset"]:
        return None, {"error": f"GitHub rate limit; next try after {time.strftime('%H:%M', time.localtime(cache['rate_reset']))}"}
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if cache.get("etag") and cache.get("releases") is not None:
        h["If-None-Match"] = cache["etag"]
    out: dict = {}
    try:
        with _get(API, h) as r:
            out = _rate(r.headers)
            out["etag"] = r.headers.get("ETag") or ""
            return json.loads(r.read().decode("utf-8")), out
    except urllib.error.HTTPError as e:
        out = _rate(e.headers or {})
        if e.code == 304:
            return None, out
        if e.code in (403, 429) and out.get("rate_remaining") == 0:
            out["error"] = "GitHub rate limit reached; will retry later"
        else:
            out["error"] = f"GitHub API HTTP {e.code}"
        return None, out
    except (OSError, ValueError) as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


def _rate(headers) -> dict:
    out = {}
    try:
        if headers.get("X-RateLimit-Remaining") is not None:
            out["rate_remaining"] = int(headers.get("X-RateLimit-Remaining"))
        if headers.get("X-RateLimit-Reset") is not None:
            out["rate_reset"] = int(headers.get("X-RateLimit-Reset"))
        if headers.get("Retry-After"):
            out["rate_reset"] = int(time.time()) + int(headers.get("Retry-After"))
            out["rate_remaining"] = 0
    except (TypeError, ValueError):
        pass
    return out


def pick(releases: list, channel: str = "stable") -> dict | None:
    """Highest semver release with an installer asset; beta also allows prereleases."""
    best = None
    for r in releases or []:
        if r.get("draft"):
            continue
        if r.get("prerelease") and channel != "beta":
            continue
        tag = r.get("tag_name") or ""
        if not parse(tag):
            continue
        assets = {a.get("name"): a for a in r.get("assets") or []}
        exe = next((a for n, a in assets.items() if n and ASSET_RE.match(n)), None)
        sums = next((assets[n] for n in SUMS_NAMES if n in assets), None)
        info = {"version": tag.lstrip("v"), "tag": tag, "name": r.get("name") or tag,
                "notes": (r.get("body") or "")[:20000], "url": r.get("html_url") or RELEASES_PAGE,
                "prerelease": bool(r.get("prerelease")), "published": r.get("published_at"),
                "installer": ({"name": exe["name"], "url": exe.get("browser_download_url"),
                               "size": exe.get("size")} if exe else None),
                "sha256sums": sums.get("browser_download_url") if sums else None}
        if best is None or parse(tag) > parse(best["tag"]):
            best = info
    return best


def status() -> dict:
    """What the UI and the tray show (no network)."""
    s = _settings()
    c = _read_cache()
    latest = c.get("latest")
    avail = bool(latest and newer(latest["version"], __version__))
    m = mode()
    st = {"current": __version__, "mode": m, "enabled": bool(s.get("update_check", True)),
          "channel": s.get("update_channel") or "stable", "checked": c.get("checked"),
          "error": c.get("error"), "available": avail, "latest": latest if avail else None,
          "latest_seen": latest["version"] if latest else None, "releases_page": RELEASES_PAGE,
          "can_install": avail and m == "installed" and bool(latest and latest.get("installer") and latest.get("sha256sums")),
          "install": c.get("install")}
    if avail and m == "source":
        st["notice"] = f"v{latest['version']} is out. This is a source checkout: update with `git pull` (then reinstall requirements if they changed)."
    elif avail and not st["can_install"]:
        st["notice"] = "This release has no verifiable installer (OrcPixels-Setup exe + SHA256SUMS); download it from the Releases page."
    return st


def check(force: bool = False) -> dict:
    """Ask GitHub (unless the cached answer is fresh) and return status()."""
    with _lock:
        c = _read_cache()
        s = _settings()
        channel = s.get("update_channel") or "stable"
        now = time.time()
        fresh = c.get("checked_ts") and now - c["checked_ts"] < (MIN_MANUAL_S if force else INTERVAL_S - 60)
        if fresh and c.get("channel") == channel:
            return status()
        rel, meta = fetch_releases(c)
        c.update({k: v for k, v in meta.items() if k != "error"})
        c["error"] = meta.get("error")
        if rel is not None:
            c["releases"] = rel
        if c.get("releases") is not None and not meta.get("error"):
            c["latest"] = pick(c["releases"], channel)
            c["checked_ts"], c["checked"], c["channel"] = now, time.strftime("%Y-%m-%d %H:%M:%S"), channel
        _write_cache(c)
        return status()


# ------------------------------------------------------------------ download + verify
def parse_sums(text: str) -> dict:
    """sha256sum format: '<hex>  <name>' (also '<hex> *<name>')."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line.strip())
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(latest: dict, dest_dir: Path | None = None, progress=None) -> Path:
    """Download the installer + SHA256SUMS, verify, return the verified exe path.
    Raises ValueError when anything doesn't match (the file is deleted)."""
    inst = latest.get("installer") or {}
    if not inst.get("url") or not latest.get("sha256sums"):
        raise ValueError("release has no installer or no SHA256SUMS")
    if not ASSET_RE.match(inst.get("name") or ""):
        raise ValueError(f"unexpected installer name {inst.get('name')!r}")
    with _get(latest["sha256sums"]) as r:
        sums = parse_sums(r.read(1 << 16).decode("utf-8", "replace"))
    want = sums.get(inst["name"])
    if not want:
        raise ValueError(f"SHA256SUMS has no entry for {inst['name']}")
    d = Path(dest_dir or tempfile.mkdtemp(prefix="orcpixels-update-"))
    d.mkdir(parents=True, exist_ok=True)
    out = d / inst["name"]
    part = out.with_suffix(".part")
    h, n = hashlib.sha256(), 0
    with _get(inst["url"], timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or inst.get("size") or 0)
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            n += len(chunk)
            if progress:
                progress(n, total)
            if n > 400 * 1024 * 1024:
                raise ValueError("installer larger than 400 MB")
    if h.hexdigest() != want:
        part.unlink(missing_ok=True)
        raise ValueError(f"SHA-256 mismatch for {inst['name']}: got {h.hexdigest()[:16]}…, release says {want[:16]}…")
    os.replace(part, out)
    return out


# ------------------------------------------------------------------ run the installer
def install_dir() -> Path:
    """Folder of the running installed build (OrcPixels.exe)."""
    return Path(sys.executable).resolve().parent


def needs_admin(d: Path | None = None) -> bool:
    d = d or install_dir()
    try:
        t = d / f".orcpixels-write-test-{os.getpid()}"
        t.write_bytes(b"")
        t.unlink()
        return False
    except OSError:
        return True


def install_log() -> Path:
    return paths.OUT / "update-install.log"


def installer_args(d: Path | None = None, wait_pid: int | None = None) -> list[str]:
    """Inno Setup silent update into the same folder, same install mode, same data/Library.
    /WAITPID: Setup waits (up to 60 s) for this process to exit before it checks for files in
    use; without it Setup raced our shutdown and found OrcPixels.exe still running.
    /LOG: always, so a failed update can be explained (reconcile_install)."""
    from . import settings as S
    d = d or install_dir()
    lib = S.load().get("library_dir") or str(paths.library())
    args = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1",
            f"/WAITPID={wait_pid or os.getpid()}",
            f'/DIR="{d}"', f'/CONFIGDIR="{paths.DATA}"', f'/LIBRARYDIR="{lib}"',
            f'/LOG="{install_log()}"']
    args.append("/ALLUSERS" if needs_admin(d) else "/CURRENTUSER")
    return args


def _rotate_install_log() -> None:
    """Keep the previous attempt's log next to the new one (update-install.prev.log)."""
    p = install_log()
    try:
        if p.exists():
            os.replace(p, p.with_name("update-install.prev.log"))
    except OSError as e:
        log.warning("install log rotate: %s", e)


def launch_installer(exe: Path, args: list[str]) -> None:
    """Start the verified installer detached. Admin install folder: ShellExecute 'runas'
    (Windows shows the UAC prompt); otherwise a normal process."""
    if os.name != "nt":
        raise OSError("installing updates is Windows-only")
    from .winproc import clean_dll_search
    params = " ".join(args)
    with clean_dll_search():                     # Setup must not inherit our DLL folder
        if "/ALLUSERS" in args:
            import ctypes
            rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), params, None, 1)
            if rc <= 32:
                raise OSError(f"could not start the installer (UAC declined?) code {rc}")
            return
        subprocess.Popen(f'"{exe}" {params}', close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP


def _set_install_state(**kw) -> None:
    c = _read_cache()
    c["install"] = {**(c.get("install") or {}), **kw, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write_cache(c)


def install(shutdown, launcher=launch_installer) -> dict:
    """The "Update now" action. shutdown() stops the server cleanly and exits the app."""
    st = status()
    if st["mode"] != "installed":
        raise PermissionError("source checkout: update with git pull (self-update is off in dev mode)")
    if not st["available"] or not st["can_install"]:
        raise ValueError("no installable update")
    latest = st["latest"]
    _set_install_state(state="downloading", version=latest["version"], progress=0)
    try:
        exe = download(latest, progress=lambda n, t: _set_install_state(progress=round(100 * n / t) if t else None))
    except Exception as e:
        _set_install_state(state="failed", error=str(e))
        raise
    _set_install_state(state="verified", sha256=sha256_file(exe), file=str(exe))
    args = installer_args()
    _rotate_install_log()
    try:
        launcher(exe, args)
    except Exception as e:
        _set_install_state(state="failed", error=str(e))
        raise
    _set_install_state(state="installing", from_version=__version__)
    threading.Timer(1.0, _exit_for_update, (shutdown,)).start()   # let the HTTP answer go out first
    return {"ok": True, "version": latest["version"], "installer": str(exe), "args": args}


def _exit_for_update(shutdown, hard_exit=None, timeout: float | None = None, arm: bool | None = None) -> None:
    """Clean stop (Live engines, BLE links), then make sure the process is really gone:
    Setup waits for this PID before it replaces our files. The watchdog (installed build
    only) hard-exits if the clean stop hangs."""
    arm = paths.FROZEN if arm is None else arm
    hard_exit = hard_exit or os._exit
    if arm:
        def watchdog():
            time.sleep(HARD_EXIT_S if timeout is None else timeout)
            log.warning("update: clean shutdown took too long, exiting now")
            hard_exit(0)
        threading.Thread(target=watchdog, name="orcpixels-update-exit", daemon=True).start()
    try:
        shutdown()
    except Exception as e:  # noqa: BLE001
        log.warning("update shutdown: %s", e)
        if arm:
            hard_exit(0)


# ------------------------------------------------------------------ last install result
_LOG_REASONS = (
    (re.compile(r"Setup was unable to automatically close all applications"), "files in use"),
    (re.compile(r"Some applications could not be shut down"), "files in use"),
    (re.compile(r"DeleteFile failed|MoveFile failed|being used by another process"), "files in use"),
    (re.compile(r"User canceled the installation process"), "canceled"),
)


def parse_install_log(text: str) -> dict:
    """Inno Setup /LOG -> {"ok": bool|None, "reason": str, "blockers": [exe names], "detail": str}."""
    blockers = sorted(set(re.findall(r"RestartManager found an application using one of our files: (.+?)\s*$",
                                     text, re.M)))
    if re.search(r"Installation process succeeded|Need to restart Windows\? No", text) and "Rolling back" not in text:
        return {"ok": True, "reason": "", "blockers": blockers, "detail": ""}
    if "Rolling back changes" not in text and "Setup exit code" not in text and "Log closed" not in text:
        return {"ok": None, "reason": "", "blockers": blockers, "detail": ""}   # still running / empty
    reason, detail = "rolled back", ""
    for rx, why in _LOG_REASONS:
        m = rx.search(text)
        if m:
            reason = why
            detail = m.group(0)
            break
    if reason == "files in use" and blockers:
        detail = f"in use by: {', '.join(blockers)}"
    return {"ok": False, "reason": reason, "blockers": blockers, "detail": detail}


def reconcile_install() -> dict | None:
    """At startup after an "Update now": installed, or rolled back (and why)?
    The log wins over the version: Inno's rollback removes new files but does not restore
    ones it already replaced, so a late failure can leave a mixed install that reports the
    new version; that needs one manual run of the installer."""
    c = _read_cache()
    inst = c.get("install") or {}
    if inst.get("state") != "installing":
        return None
    want = inst.get("version")
    try:
        text = install_log().read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    r = parse_install_log(text)
    upgraded = bool(want and parse(want) and parse(__version__) and parse(__version__) >= parse(want))
    if r["ok"] is not False and upgraded:
        _set_install_state(state="done", error=None)
        log.info("update to v%s installed", want)
        return _read_cache().get("install")
    why = r["reason"] or "the installer did not finish"
    if r["detail"]:
        why += f": {r['detail']}"
    msg = f"Update to v{want} did not install ({why}). "
    msg += (f"Some files may already be v{want}: run the installer from the Releases page once to repair."
            if upgraded else f"Still on v{__version__}.")
    msg += f" Installer log: {install_log()}"
    _set_install_state(state="failed", error=msg, blockers=r["blockers"])
    log.warning("update failed: %s", msg)
    return _read_cache().get("install")


# ------------------------------------------------------------------ shutdown hook
SHUTDOWN = None          # set by the tray app: stop the server cleanly, remove the icon, exit


def request_shutdown() -> None:
    if SHUTDOWN is not None:
        SHUTDOWN()
    else:                                       # plain `serve`: leave the process to the installer
        os._exit(0)


# ------------------------------------------------------------------ background checks
def start_background(stop: threading.Event | None = None) -> threading.Thread:
    """Startup check (after 20 s) + every 24 h, while update_check is on."""
    stop = stop or threading.Event()
    if os.environ.get("ORCPIXELS_NO_UPDATE_CHECK"):
        stop.set()

    try:
        reconcile_install()
    except Exception as e:  # noqa: BLE001
        log.warning("update reconcile: %s", e)

    def run():
        if stop.wait(20):
            return
        while not stop.is_set():
            if _settings().get("update_check", True):
                try:
                    st = check()
                    if st.get("available"):
                        log.info("update available: v%s", st["latest"]["version"])
                except Exception as e:  # noqa: BLE001
                    log.warning("update check failed: %s", e)
            if stop.wait(INTERVAL_S):
                return

    t = threading.Thread(target=run, name="orcpixels-update", daemon=True)
    t.start()
    t.stop_event = stop
    return t
