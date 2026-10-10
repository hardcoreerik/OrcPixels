"""Foreground AI-app watcher: which coding assistant is in front, which folder it is
working in, and the git repo/branch/worktree of that folder.

Read-only everywhere: session logs/state are opened read-only (sqlite mode=ro), and
git is only queried with rev-parse/branch --show-current (GIT_OPTIONAL_LOCKS=0).
Nothing is hardcoded per project: the repo name comes from the main worktree's
toplevel (the parent of `git rev-parse --git-common-dir`), the branch from
`git branch --show-current`, the worktree path from `--show-toplevel`.

App identity lives in config/apps.json (process names, exe-path hints, title
patterns, terminal CLI names), discovered on the user's PC 2026-09-29; see there.
"""
from __future__ import annotations

import base64
import glob
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import buddy as BD

log = logging.getLogger("orcpixels.ai_watcher")
HOME = Path(os.path.expanduser("~"))
APPDATA = Path(os.environ.get("APPDATA", HOME / "AppData" / "Roaming"))
from ..paths import APP as _APP, CONFIG as _UCFG  # noqa: E402
CONFIG = _APP / "config" / "apps.json"
IS_WIN = os.name == "nt"
_NOWIN = 0x08000000 if IS_WIN else 0      # CREATE_NO_WINDOW


# ------------------------------------------------------------------ models
@dataclass
class Foreground:
    pid: int
    exe: str            # lowercase exe name, e.g. 'chatgpt.exe'
    path: str
    title: str


@dataclass
class GitInfo:
    repo: str                   # main repo folder name, e.g. 'OrcSDR'
    branch: str                 # '' only if git failed; detached -> short sha
    toplevel: str               # this worktree's root
    main_toplevel: str          # main worktree root
    is_worktree: bool           # toplevel != main_toplevel


@dataclass
class Detection:
    app: str                    # apps.json key ('codex', 'claude', 'grok', 'cursor')
    app_display: str
    via: str                    # how: 'desktop' | 'terminal' | 'title'
    folder: str | None = None   # working folder used for git
    cwd: str | None = None      # session cwd from the log (before refinement)
    session: str | None = None  # session/thread title (not shown on the sign)
    source: str = ""            # which log/state the folder came from
    git: GitInfo | None = None
    log_path: str | None = None
    extra: dict = field(default_factory=dict)
    fg_title: str = ""

    def key(self) -> tuple:
        g = self.git
        return (self.app, g.repo if g else None, g.branch if g else None,
                g.toplevel if g and g.is_worktree else None)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        return d


# ------------------------------------------------------------------ config
LOCAL = _UCFG / "apps.local.json"      # per-machine overrides (gitignored)


def load_apps() -> dict:
    """config/apps.json (generic, tracked) merged with config/apps.local.json: per app key,
    local fields replace the shipped ones; new keys add apps."""
    try:
        raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    try:
        local = json.loads(LOCAL.read_text(encoding="utf-8")) if LOCAL.exists() else {}
    except (OSError, ValueError):
        log.warning("ignoring unreadable %s", LOCAL)
        local = {}
    for k, v in local.items():
        if isinstance(v, dict) and isinstance(raw.get(k), dict):
            raw[k] = {**raw[k], **v}
        else:
            raw[k] = v
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}


_warned: set = set()


def _warn_once(k: str, msg: str, *args) -> None:
    if k not in _warned:
        _warned.add(k)
        log.warning(msg, *args)


def capabilities() -> dict:
    """What the AI status / buddy feature can do on this machine (shown in the UI).
    Foreground-window detection is Windows-only (pywin32 + psutil); elsewhere the feature
    reports itself unavailable instead of failing."""
    import shutil
    import sys
    out = {"platform": sys.platform, "foreground": False, "reason": "", "git": bool(shutil.which("git")),
           "apps": {}}
    if not IS_WIN:
        out["reason"] = "foreground-window detection is Windows-only for now"
    else:
        missing = []
        for mod in ("win32gui", "win32process", "psutil"):
            try:
                __import__(mod)
            except ImportError:
                missing.append(mod)
        out["foreground"] = not missing
        if missing:
            out["reason"] = "install the AI extras: pip install -r requirements-ai.txt (missing " + ", ".join(missing) + ")"
    home = Path.home()
    probes = {"codex": home / ".codex", "claude": home / ".claude", "grok": home / ".grok"}
    for k, d in probes.items():
        out["apps"][k] = d.exists()
    out["available"] = out["foreground"]
    return out


# -------------------------------------------------------------- foreground
def foreground() -> Foreground | None:
    if not IS_WIN:
        return None
    try:
        import psutil
        import win32gui
        import win32process
    except ImportError:          # pip install pywin32 psutil
        return None
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    title = win32gui.GetWindowText(hwnd) or ""
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    try:
        p = psutil.Process(pid)
        return Foreground(pid, p.name().lower(), p.exe() or "", title)
    except Exception:  # noqa: BLE001  (access denied / exited)
        return Foreground(pid, "", "", title)


def _descendants(pid: int):
    try:
        import psutil
        return psutil.Process(pid).children(recursive=True)
    except Exception:  # noqa: BLE001
        return []


def _cmdline(p) -> str:
    try:
        return " ".join(p.cmdline())
    except Exception:  # noqa: BLE001
        return ""


# ----------------------------------------------------------------------- git
_git_cache: dict[str, tuple[float, GitInfo | None]] = {}


def _git(args: list[str], cwd: str) -> str | None:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=4,
                           env=env, creationflags=_NOWIN)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _norm(p: str) -> str:
    p = (p or "").strip()
    if p.startswith("\\\\?\\"):
        p = p[4:]
    if re.match(r"^/[A-Za-z]:/", p):          # '/C:/src/x' (Codex tab routes)
        p = p[1:]
    return os.path.normpath(p) if p else p


def git_info(path: str | None, ttl: float = 15.0) -> GitInfo | None:
    if not path:
        return None
    path = _norm(path)
    d = path
    while d and not os.path.isdir(d):          # file path or deleted dir -> nearest parent
        nd = os.path.dirname(d)
        if nd == d:
            return None
        d = nd
    key = os.path.normcase(d)
    hit = _git_cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    info = None
    out = _git(["rev-parse", "--show-toplevel", "--git-common-dir"], d)
    if out:
        lines = out.splitlines()
        if len(lines) >= 2:
            top = os.path.normpath(lines[0])
            common = lines[1]
            if not os.path.isabs(common):
                common = os.path.join(d, common)
            common = os.path.normpath(common)
            main_top = os.path.dirname(common) if os.path.basename(common).lower() == ".git" else common
            repo = os.path.basename(main_top)
            if repo.lower().endswith(".git"):
                repo = repo[:-4]
            branch = _git(["branch", "--show-current"], d) or ""
            if not branch:
                sha = _git(["rev-parse", "--short", "HEAD"], d)
                branch = f"@{sha}" if sha else ""
            info = GitInfo(repo, branch, top, main_top,
                           os.path.normcase(top) != os.path.normcase(main_top))
    _git_cache[key] = (time.monotonic(), info)
    return info


# ------------------------------------------------------------ Codex state
def _ro_sqlite(path: Path):
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)


def codex_thread(cli_cwd: str | None = None, cli_only: bool = False) -> dict | None:
    """Most recently updated top-level (non-subagent, non-archived) Codex thread."""
    db = HOME / ".codex" / "state_5.sqlite"
    if not db.exists():
        cands = sorted((HOME / ".codex").glob("state_*.sqlite"))
        if not cands:
            return None
        db = cands[-1]
    try:
        con = _ro_sqlite(db)
        con.row_factory = sqlite3.Row
        q = ("select id, rollout_path, cwd, title, git_branch, source, updated_at_ms, updated_at "
             "from threads where archived = 0 and source not like '%subagent%' "
             + ("and source = 'cli' " if cli_only else "")
             + "order by coalesce(updated_at_ms, updated_at * 1000) desc limit 20")
        rows = [dict(r) for r in con.execute(q)]
        con.close()
    except sqlite3.Error as e:
        log.debug("codex sqlite: %s", e)
        return None
    if cli_cwd:
        for r in rows:
            if os.path.normcase(_norm(r["cwd"])) == os.path.normcase(_norm(cli_cwd)):
                return r
    return rows[0] if rows else None


# ----------------------------------------------------------- Claude state
def claude_sessions() -> list[dict]:
    out = []
    for f in (HOME / ".claude" / "sessions").glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _pid_alive(pid) -> bool:
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:  # noqa: BLE001
        return True


def claude_session(pid: int | None = None, entrypoint: str | None = None) -> dict | None:
    ss = [s for s in claude_sessions() if _pid_alive(s.get("pid"))]
    if pid is not None:
        for s in ss:
            if s.get("pid") == pid:
                return s
    if entrypoint:
        ss = [s for s in ss if s.get("entrypoint") == entrypoint] or ss
    ss.sort(key=lambda s: s.get("updatedAt") or s.get("startedAt") or 0, reverse=True)
    return ss[0] if ss else None


def claude_jsonl(session_id: str) -> str | None:
    hits = glob.glob(str(HOME / ".claude" / "projects" / "*" / f"{session_id}.jsonl"))
    return hits[0] if hits else None


# -------------------------------------------------------------- Grok state
def grok_cli_session(cwd: str | None = None) -> dict | None:
    best = None
    for f in glob.glob(str(HOME / ".grok" / "sessions" / "*" / "*" / "summary.json")):
        try:
            s = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        s["_dir"] = os.path.dirname(f)
        s["_cwd"] = (s.get("info") or {}).get("cwd") or urllib.parse.unquote(Path(f).parents[1].name)
        if cwd and os.path.normcase(_norm(s["_cwd"])) != os.path.normcase(_norm(cwd)):
            continue
        if best is None or (s.get("updated_at") or "") > (best.get("updated_at") or ""):
            best = s
    return best


def _b32name(n: str) -> str:
    s = n[:-5].upper() if n.endswith(".blob") else n.upper()
    s += "=" * ((8 - len(s) % 8) % 8)
    try:
        return base64.b32decode(s).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def grok_bot_state() -> dict | None:
    """Selected Grok Bot agent (name/title) + its transcript replica entries."""
    d = APPDATA / "Grok Bot" / "sand-client-persistence"
    if not d.is_dir():
        return None
    names = {}
    for n in os.listdir(d):
        if n.endswith(".blob"):
            names[_b32name(n)] = d / n

    def load(suffix):
        for k, p in names.items():
            if k.endswith(suffix):
                try:
                    return json.loads(p.read_text(encoding="utf-8")).get("value")
                except (OSError, ValueError):
                    return None
        return None
    sel = load(".selection.last-agent") or {}
    aid = sel.get("agentId")
    if not aid:
        return None
    row = next((r for r in (load(".roster.last-roster") or {}).get("rows", []) if r.get("id") == aid), {})
    rep = load(f".transcript.replicas.{aid}") or {}
    return {"agent_id": aid, "name": row.get("name") or "", "title": row.get("title") or "",
            "entries": rep.get("entries") or []}


# ------------------------------------------------------------- Cursor state
def cursor_folder(title: str) -> str | None:
    """Cursor title is '<file> - <folder> - Cursor'; map folder name via workspaceStorage."""
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) < 2:
        return None
    want = parts[-2].lower()
    for f in glob.glob(str(APPDATA / "Cursor" / "User" / "workspaceStorage" / "*" / "workspace.json")):
        try:
            uri = json.loads(Path(f).read_text(encoding="utf-8")).get("folder") or ""
        except (OSError, ValueError):
            continue
        p = urllib.parse.unquote(urllib.parse.urlparse(uri).path).lstrip("/")
        if os.path.basename(p.rstrip("/")).lower() == want:
            return p
    return None


# ---------------------------------------------------------------- matching
def match_app(fg: Foreground, apps: dict) -> tuple[str | None, str]:
    """-> (app key, via). Desktop exe match first, then terminal CLI child, then title."""
    for key, a in apps.items():
        names = [n.lower() for n in a.get("process_names", [])]
        if fg.exe in names:
            hint = a.get("path_contains")
            if hint and not any(h.lower() in fg.path.lower() for h in hint):
                continue
            return key, "desktop"
    terminals = {n.lower() for a in apps.values() for n in a.get("terminal_hosts", [])} or {
        "windowsterminal.exe", "openconsole.exe", "conhost.exe", "pwsh.exe", "powershell.exe", "cmd.exe"}
    if fg.exe in terminals:
        for key, a in apps.items():                    # title first (active tab)
            for pat in a.get("terminal_title_regex", []):
                if re.search(pat, fg.title, re.I):
                    return key, "terminal"
        best = None                                    # else newest CLI descendant
        for ch in _descendants(fg.pid):
            try:
                nm, ct = ch.name().lower(), ch.create_time()
            except Exception:  # noqa: BLE001
                continue
            cl = None
            for key, a in apps.items():
                if nm in [n.lower() for n in a.get("cli_process_names", [])] or any(
                        s.lower() in _cmdline(ch).lower() for s in a.get("cli_cmdline_contains", []) if nm == "node.exe"):
                    cl = key
            if cl and (best is None or ct > best[1]):
                best = (cl, ct)
        if best:
            return best[0], "terminal"
    for key, a in apps.items():
        for pat in a.get("title_regex", []):
            if re.search(pat, fg.title, re.I):
                return key, "title"
    return None, ""


def _cli_proc(fg: Foreground, app: dict):
    names = [n.lower() for n in app.get("cli_process_names", [])]
    best = None
    for ch in _descendants(fg.pid):
        try:
            if ch.name().lower() in names and (best is None or ch.create_time() > best.create_time()):
                best = ch
        except Exception:  # noqa: BLE001
            continue
    return best


# ------------------------------------------------------------------ resolve
def resolve(key: str, via: str, fg: Foreground, apps: dict) -> Detection:
    a = apps.get(key, {})
    det = Detection(app=key, app_display=a.get("display", key), via=via, fg_title=fg.title)
    proc = _cli_proc(fg, a) if via == "terminal" else None
    proc_cwd = None
    if proc is not None:
        try:
            proc_cwd = proc.cwd()
        except Exception:  # noqa: BLE001
            proc_cwd = None
    log_kind = a.get("log", key)

    if log_kind == "codex":
        th = codex_thread(proc_cwd, cli_only=via == "terminal") or (codex_thread(proc_cwd) if via == "terminal" else None)
        if th:
            det.cwd, det.session = _norm(th["cwd"]), th.get("title", "")[:80]
            det.log_path = _norm(th["rollout_path"])
            det.source = "~/.codex/state_5.sqlite threads (most recent)"
        elif proc_cwd:
            det.cwd, det.source = proc_cwd, "process cwd"
    elif log_kind == "claude":
        s = claude_session(proc.pid if proc else None, None if via == "terminal" else "claude-desktop")
        if s:
            det.cwd, det.session = s.get("cwd"), s.get("name")
            det.log_path = claude_jsonl(s.get("sessionId", ""))
            det.extra["status"] = s.get("status")
            det.source = f"~/.claude/sessions/{s.get('pid')}.json"
        elif proc_cwd:
            det.cwd, det.source = proc_cwd, "process cwd"
    elif log_kind == "grok":
        if via == "desktop":
            st = grok_bot_state()
            if st:
                det.session = st["name"]
                det.extra["entries"] = st["entries"]
                det.source = "Grok Bot sand-client-persistence (agent name only; no local cwd)"
                det.extra["name_hint"] = f"{st['name']} {st['title']}"   # project guess from agent name only
        else:
            s = grok_cli_session(proc_cwd) or grok_cli_session()
            if s:
                det.cwd, det.session = s["_cwd"], s.get("session_summary")
                det.log_path = s["_dir"]
                det.source = "~/.grok/sessions/*/summary.json"
            elif proc_cwd:
                det.cwd, det.source = proc_cwd, "process cwd"
    elif log_kind == "cursor":
        det.cwd = cursor_folder(fg.title)
        det.source = "window title + Cursor workspaceStorage" if det.cwd else "window title"
    det.folder = det.cwd
    return det


_act_cache: dict = {}


def _sig(path: str | None):
    if not path:
        return None
    p = os.path.join(path, "chat_history.jsonl") if os.path.isdir(path) else path
    try:
        st = os.stat(p)
        return (p, st.st_size, st.st_mtime)
    except OSError:
        return None


def activity(det: Detection) -> BD.Activity | None:
    """Latest buddy activity for a detection (reads the session log tail; cached
    until the log grows)."""
    kind = load_apps().get(det.app, {}).get("log", det.app)
    sig = _sig(det.log_path) or ("entries", len(det.extra.get("entries") or []),
                                 (det.extra.get("entries") or [{}])[-1].get("id"))
    ck = (det.app, sig, det.extra.get("status"))
    if ck in _act_cache:
        return BD.finalize(_act_cache[ck])
    act = None
    try:
        if kind == "codex" and det.log_path:
            act = BD.codex_activity(det.log_path)
        elif kind == "claude" and det.log_path:
            act = BD.claude_activity(det.log_path, det.extra.get("status"))
        elif kind == "grok":
            if det.log_path:
                act = BD.grok_cli_activity(det.log_path)
            elif det.extra.get("entries") is not None:
                act = BD.grok_bot_activity(det.extra["entries"])
    except Exception as e:  # noqa: BLE001
        log.warning("activity %s: %s", det.app, e)
    if len(_act_cache) > 64:
        _act_cache.clear()
    _act_cache[ck] = act
    return BD.finalize(act)


class Watcher:
    """poll() -> Detection of the foreground AI app, or the last one if a non-AI
    window is in front (so the sign keeps showing what you were working on)."""

    def __init__(self):
        self.apps = load_apps()
        self.last: Detection | None = None
        self.last_fg: Foreground | None = None
        self._apps_at = time.monotonic()
        self._sticky: dict[str, str] = {}     # log_path -> last dir a tool call touched

    def poll(self, refine: bool = True) -> Detection | None:
        if time.monotonic() - self._apps_at > 30:
            self.apps, self._apps_at = load_apps(), time.monotonic()
        fg = foreground()
        self.last_fg = fg
        if fg is None:
            return self.last
        key, via = match_app(fg, self.apps)
        if key is None:
            return self.last
        try:
            det = resolve(key, via, fg, self.apps)
        except Exception as e:  # noqa: BLE001  (app updated its private files: degrade quietly)
            _warn_once(f"resolve:{key}", "AI status: %s reader failed (%s); showing the app only", key, e)
            a = self.apps.get(key, {})
            det = Detection(app=key, app_display=a.get("display", key), via=via, fg_title=fg.title)
        act = activity(det)
        # refine the folder with the dir the agent's latest tool call touched (e.g. a
        # worktree under <Repo>-Temp/ while the session cwd is the main checkout)
        if refine and det.log_path:
            if act and act.path and git_info(act.path):
                self._sticky[det.log_path] = _norm(act.path)
            elif det.log_path not in self._sticky:          # first look: newest call with a path
                kind = self.apps.get(det.app, {}).get("log", det.app)
                prev = None
                try:
                    if kind == "codex":
                        prev = BD.codex_activity(det.log_path, need_path=True)
                    elif kind == "claude":
                        prev = BD.claude_activity(det.log_path, need_path=True)
                except Exception:  # noqa: BLE001
                    prev = None
                self._sticky[det.log_path] = _norm(prev.path) if prev and prev.path and git_info(prev.path) else ""
            tp = self._sticky.get(det.log_path)
            gi = git_info(tp) if tp else None
            if gi:
                det.folder, det.git = tp, gi
                det.source += " + latest tool-call path"
        if det.git is None:
            det.git = git_info(det.folder)
        if det.git is None and det.extra.get("name_hint"):
            det.extra["project_hint"] = det.extra["name_hint"]
        det.extra["activity"] = act
        self.last = det
        return det
