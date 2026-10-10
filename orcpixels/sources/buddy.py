"""Buddy: condense the latest agent activity from local session logs into a short,
privacy-safe line (<= ~40 chars) plus a state: thinking | reading | editing |
running | talking | done.

Read-only. Formats were learned from real files on the user's PC (2026-09-29):

Codex (desktop "Codex"/ChatGPT.exe app + codex CLI)
  ~/.codex/state_5.sqlite  table threads(id, rollout_path, cwd, title, git_branch,
      source, archived, updated_at_ms, ...)   (paths may carry a \\\\?\\ prefix)
  rollout_path = ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, one JSON per line:
      {"type":"session_meta","payload":{"cwd":...}}
      {"type":"response_item","payload":{"type":"custom_tool_call","name":"exec",
          "input":"text(await tools.apply_patch(\"*** Begin Patch\\n*** Update File: P ..."}}
          (also tools.exec_command({cmd:"...",workdir:"..."}))
      {"type":"response_item","payload":{"type":"function_call","name":..,"arguments":"{json}"}}
      {"type":"response_item","payload":{"type":"reasoning","summary":[{"text":"**Title**"}]}}
      {"type":"response_item","payload":{"type":"message","role":"assistant","phase":"commentary"|"final_answer",
          "content":[{"type":"output_text","text":...}]}}
      {"type":"event_msg","payload":{"type":"task_started"|"task_complete"|"item_completed"|...}}
  NB: Windows does not refresh the rollout file mtime while Codex holds it open, so
  recency comes from the sqlite updated_at_ms, not the file time.

Claude Code (Claude desktop "Code" tab = entrypoint claude-desktop, or CLI)
  ~/.claude/sessions/<pid>.json  {"pid","sessionId","cwd","name","status":"busy"|"idle",
      "updatedAt","entrypoint"}
  ~/.claude/projects/<cwd-with-non-alnum-as-dash>/<sessionId>.jsonl, records:
      {"type":"assistant","cwd","gitBranch","message":{"content":[
          {"type":"tool_use","name":"Read"|"Edit"|"Write"|"Bash"|"PowerShell"|...,"input":{...}},
          {"type":"text","text":...}, {"type":"thinking","thinking":""}],"stop_reason":...}}
      {"type":"user","toolUseResult":...}   (tool results; skipped)

Grok CLI (grok.exe)
  ~/.grok/sessions/<urlencoded cwd>/<id>/summary.json {"info":{"cwd"},"session_summary","updated_at"}
  .../chat_history.jsonl records {"type":"reasoning","summary":[{"text"}]},
      {"type":"assistant","content":str,"tool_calls"?:[...]}, {"type":"user"|"system"|"tool"...}

Grok Bot desktop (this app; agents run on a remote box, so no local cwd/tool calls)
  %APPDATA%/Grok Bot/sand-client-persistence/<base32(name)>.blob, JSON
      sand.client.slice.account.<acct>.selection.last-agent  {"value":{"agentId"}}
      ...roster.last-roster  {"value":{"rows":[{"id","name","title","lastEntry":{"text"}}]}}
      ...transcript.replicas.<agentId> {"value":{"entries":[
          {"kind":"message","role":"user","content","timestampMs"},
          {"kind":"send-message","message":{"type":"text","content"},"timestampMs"}]}}
"""
from __future__ import annotations

import json
import os
import re
import shlex
import time
from dataclasses import asdict, dataclass

MAX_CHARS = 40
STATES = ("thinking", "reading", "editing", "running", "talking", "done")


@dataclass
class Activity:
    state: str            # one of STATES
    text: str             # condensed, redacted, <= MAX_CHARS
    ts: float = 0.0       # unix seconds of the underlying event (0 = unknown)
    source: str = ""      # e.g. "codex:custom_tool_call"
    path: str | None = None   # a directory the tool touched (for worktree refinement)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        return d


# ------------------------------------------------------------------ privacy
_SECRET_KV = re.compile(
    r"(?i)\b([\w-]*(?:api[_-]?key|token|secret|passw(?:or)?d|pwd|auth|bearer|credential|cookie|session[_-]?id)[\w-]*)"
    r"(\s*[:=]\s*|\s+)(\"[^\"]*\"|'[^']*'|\S+)")
_SECRET_TOK = re.compile(
    r"\b(?:sk-|sk_|pk_|rk_|ghp_|gho_|ghs_|github_pat_|glpat-|xox[abpr]-|AKIA|ASIA|AIza|eyJ|ya29\.)[\w\-\.=+/]{4,}")
_LONG_BLOB = re.compile(r"[A-Za-z0-9+/_\-=]{28,}")
_ENV_FILE = re.compile(r"(?i)[^\s\"'`]*\.env(?:\.[\w-]+)?\b[^\s\"'`]*")
_ENV_VAR = re.compile(r"(?i)(\$env:|\$\{?)[A-Za-z_][\w]*\}?|%[A-Za-z_][\w]*%")
_SECRET_FILE = re.compile(r"(?i)[^\s\"'`]*(?:\.credentials|credentials|auth|secrets?|id_rsa|id_ed25519|\.pem|\.key|\.pfx|\.p12)"
                          r"(?:\.json|\.txt)?\b")
_URL = re.compile(r"(?i)\bhttps?://([^/\s\"'`?#]+)[^\s\"'`]*")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def redact(text: str) -> str:
    """Strip anything that could be a secret: key=value pairs, token-looking strings,
    long blobs, .env files, env var values, credential files, URL paths/queries, emails."""
    if not text:
        return ""
    t = _URL.sub(lambda m: m.group(1), text)
    t = _SECRET_KV.sub(lambda m: m.group(1) + "=***", t)
    t = _SECRET_TOK.sub("***", t)
    t = _ENV_FILE.sub("[env]", t)
    t = _ENV_VAR.sub("$env", t)
    t = _SECRET_FILE.sub("[secret]", t)
    t = _EMAIL.sub("[email]", t)
    t = _LONG_BLOB.sub("***", t)
    return t


def condense(text: str, limit: int = MAX_CHARS) -> str:
    t = re.sub(r"```.*?(```|$)", " ", text or "", flags=re.S)          # no code blocks
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)                      # md links -> label
    t = re.sub(r"`([^`]{0,24})`", r"\1", t)                             # short inline code
    t = re.sub(r"`[^`]*`", "…", t)                                      # long inline code
    t = re.sub(r"\*+|(?<!\w)_+|_+(?!\w)|#+|>+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = redact(t)
    m = re.match(r"(.+?[.!?])(\s|$)", t)                                # first sentence
    if m and len(m.group(1)) >= 8:
        t = m.group(1)
    t = t.rstrip(".")
    if len(t) > limit:
        t = t[:limit - 1].rstrip() + "…"
    return t


def _base(p: str) -> str:
    p = (p or "").strip().strip("\"'").rstrip("/\\")
    return re.split(r"[/\\]", p)[-1] if p else ""


# -------------------------------------------------------- command summaries
_READERS = {"get-content", "gc", "cat", "type", "head", "tail", "less", "more", "sed", "awk", "nl",
            "ls", "dir", "get-childitem", "gci", "tree", "stat", "get-item", "wc", "file", "xxd",
            "import-csv", "format-hex", "test-path", "resolve-path"}
_SEARCHERS = {"rg", "grep", "select-string", "sls", "findstr", "find", "fd", "ag", "git-grep"}
_SHELL_PREFIX = re.compile(r"^\s*(?:(?:cd|set-location|pushd|sl)\s+(\"[^\"]+\"|'[^']+'|\S+)\s*(?:&&|;|\n)\s*)+", re.I)


def _split(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd, posix=False)
    except ValueError:
        return cmd.split()


def summarize_command(cmd: str) -> tuple[str, str]:
    """-> (state, text) for a shell command, e.g. ('running', 'running pytest')."""
    c = (cmd or "").strip()
    if isinstance(cmd, list):
        c = " ".join(cmd)
    c = _SHELL_PREFIX.sub("", c)
    c = re.sub(r"^\s*(?:\.\s+\S+\s*;|&\s*)", "", c)      # '. script.ps1;' / '& ' prefixes
    first = re.split(r"\s*(?:&&|\|\||;|\||\n)\s*", c, maxsplit=1)[0]
    toks = [t.strip("\"'") for t in _split(first)]
    if not toks:
        return "running", "running a command"
    exe = _base(toks[0]).lower()
    exe = re.sub(r"\.(exe|cmd|bat|ps1)$", "", exe)
    args = [t for t in toks[1:] if not t.startswith("-")]
    if exe in ("pwsh", "powershell", "cmd", "bash", "sh"):
        low = [t.lower() for t in toks]
        for flag in ("-command", "-c", "/c", "/k", "-file", "-f"):
            if flag in low[1:] and low.index(flag) + 1 < len(toks):
                inner = " ".join(toks[low.index(flag) + 1:])
                if flag in ("-file", "-f"):
                    return "running", f"running {_base(toks[low.index(flag) + 1])}"
                return summarize_command(inner)
        return "running", "running a shell"
    if exe in ("python", "python3", "py") or exe.endswith("python"):
        if "-m" in toks:
            mod = toks[toks.index("-m") + 1] if toks.index("-m") + 1 < len(toks) else "module"
            if mod == "pytest":
                return "running", "running pytest"
            return "running", f"running {mod}"
        script = next((a for a in args if a.endswith(".py")), None)
        return "running", f"running {_base(script)}" if script else "running python"
    if exe == "pytest":
        return "running", "running pytest"
    if exe == "git":
        sub = args[0] if args else ""
        return ("reading" if sub in ("status", "log", "diff", "show", "branch", "grep", "blame") else "running",
                f"git {sub}".strip())
    if exe in _SEARCHERS or (exe == "git" and args[:1] == ["grep"]):
        return "reading", "searching code"
    if exe in _READERS:
        target = next((a for a in reversed(args) if re.search(r"[\w]\.[\w]{1,6}$|[/\\]", a)), None)
        return "reading", f"reading {_base(target)}" if target else "looking around"
    if exe in ("npm", "pnpm", "yarn", "bun"):
        sub = [a for a in args if a != "run"][:1]
        return "running", f"{exe} {sub[0]}" if sub else exe
    if exe in ("pio", "platformio", "idf.py", "cmake", "make", "ninja", "cargo", "go", "dotnet", "msbuild", "gradle"):
        sub = args[0] if args and len(args[0]) < 16 else ""
        return "running", f"{exe} {sub}".strip()
    if exe in ("curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm"):
        return "running", "fetching a URL"
    if exe.startswith("$") or "[" in exe or "=" in exe:
        return "running", "running a script"
    return "running", f"running {exe}"[:MAX_CHARS]


def _tool_activity(name: str, inp: dict | str | None) -> tuple[str, str, str | None]:
    """Generic tool-call mapping (Claude tool names + common function-call names).
    Returns (state, text, dir_touched)."""
    n = (name or "").lower()
    d = inp if isinstance(inp, dict) else {}
    fp = d.get("file_path") or d.get("path") or d.get("notebook_path") or d.get("filePath")
    dirp = os.path.dirname(fp) if isinstance(fp, str) and fp else None
    if n in ("read", "view", "read_file", "notebookread", "open_file"):
        return "reading", f"reading {_base(fp)}" if fp else "reading", dirp
    if n in ("edit", "write", "multiedit", "notebookedit", "str_replace", "write_file", "apply_patch",
             "create_file", "edit_file", "search_replace"):
        return "editing", f"editing {_base(fp)}" if fp else "editing files", dirp
    if n in ("grep", "glob", "search", "find", "codebase_search", "list_dir", "ls"):
        return "reading", "searching code", None
    if n in ("bash", "powershell", "shell", "exec_command", "run_terminal_cmd", "local_shell", "terminal"):
        cmd = d.get("command") or d.get("cmd") or ""
        wd = d.get("workdir") or d.get("cwd") or d.get("working_directory")
        m = _SHELL_PREFIX.match(cmd if isinstance(cmd, str) else "")
        if not wd and m:
            wd = m.group(1).strip("\"'")
        st, txt = summarize_command(cmd if isinstance(cmd, str) else " ".join(map(str, cmd)))
        return st, txt, wd
    if n in ("webfetch", "websearch", "web_search", "fetch"):
        return "reading", "reading the web", None
    if n in ("task", "agent", "spawn_agent"):
        desc = d.get("description") or ""
        return "running", condense(f"delegating: {desc}" if desc else "delegating a task"), None
    if n in ("todowrite", "update_plan", "exitplanmode"):
        return "thinking", "planning", None
    if n.startswith("mcp__") or n.startswith("mcp_"):
        return "running", condense("using " + n.split("__")[-1].replace("_", " ")), None
    return "running", condense(f"using {name}"), None


# ------------------------------------------------------------------- tails
def read_tail(path: str, nbytes: int = 262144) -> list[dict]:
    """Last ~nbytes of a JSONL file as parsed records (the first partial line dropped)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - nbytes))
            data = f.read()
    except OSError:
        return []
    lines = data.splitlines()
    if size > nbytes and lines:
        lines = lines[1:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _ts(s) -> float:
    if isinstance(s, (int, float)):
        return float(s) / (1000.0 if s > 1e11 else 1.0)
    if isinstance(s, str) and s:
        from datetime import datetime
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


# ------------------------------------------------------------------- Codex
_PATCH_FILE = re.compile(r"\*\*\* (Add|Update|Delete) File: ([^\\\n\"]+?)(?:\\n|\n|\")")
_EXEC_CMD = re.compile(r"tools\.exec_command\(\{\s*cmd:\s*\"((?:[^\"\\]|\\.)*)\"(?:.*?workdir:\s*\"((?:[^\"\\]|\\.)*)\")?", re.S)
_TOOLS_CALL = re.compile(r"tools\.([A-Za-z_][\w]*)\(")


def _unesc(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except ValueError:
        return s.replace("\\\\", "\\")


def _codex_exec(inp: str) -> tuple[str, str, str | None]:
    m = _PATCH_FILE.search(inp)
    if m and "apply_patch" in inp:
        path = _unesc(m.group(2)).strip()
        verb = {"Add": "creating", "Delete": "deleting"}.get(m.group(1), "editing")
        return "editing", f"{verb} {_base(path)}", os.path.dirname(path) or None
    m = _EXEC_CMD.search(inp)
    if m:
        cmd = _unesc(m.group(1))
        wd = _unesc(m.group(2)) if m.group(2) else None
        st, txt = summarize_command(cmd)
        return st, txt, wd
    m = _TOOLS_CALL.search(inp)
    if m:
        return "running", condense("using " + m.group(1).replace("_", " ")), None
    return "running", "running code", None


def _codex_iter(recs):
    for o in reversed(recs):
        p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        t, pt, ts = o.get("type"), p.get("type"), _ts(o.get("timestamp"))
        if t == "event_msg" and pt in ("task_complete", "turn_complete"):
            msg = p.get("last_agent_message") or ""
            yield Activity("done", condense(msg) or "done", ts, "codex:task_complete")
        if t == "event_msg" and pt in ("turn_aborted",):
            yield Activity("done", "stopped", ts, "codex:turn_aborted")
        if t != "response_item":
            continue
        if pt == "custom_tool_call":
            inp = p.get("input") or ""
            if p.get("name") == "exec" and isinstance(inp, str):
                st, txt, d = _codex_exec(inp)
            else:
                st, txt, d = _tool_activity(p.get("name") or "", inp)
            yield Activity(st, redact(txt)[:MAX_CHARS], ts, "codex:custom_tool_call", d)
        if pt in ("function_call", "local_shell_call"):
            args = p.get("arguments") or p.get("action") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"command": args}
            name = p.get("name") or "shell"
            if isinstance(args, dict) and isinstance(args.get("command"), list):
                args = dict(args, command=" ".join(args["command"][2:] if args["command"][:1] in (["bash"], ["pwsh"], ["powershell"]) else args["command"]))
            st, txt, d = _tool_activity(name if name != "shell" else "shell", args)
            yield Activity(st, redact(txt)[:MAX_CHARS], ts, f"codex:{pt}", d)
        if pt == "reasoning":
            summ = [s.get("text", "") for s in p.get("summary") or [] if isinstance(s, dict)]
            summ = [s for s in summ if s.strip()]
            if summ:
                yield Activity("thinking", condense(summ[-1]), ts, "codex:reasoning")
            continue
        if pt == "message" and p.get("role") == "assistant":
            txt = " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
            if txt.strip():
                st = "done" if p.get("phase") == "final_answer" else "talking"
                yield Activity(st, condense(txt), ts, "codex:message")
        if pt == "message" and p.get("role") == "user":
            yield Activity("thinking", "reading your prompt", ts, "codex:user")


def codex_activity(rollout_path: str, need_path: bool = False) -> Activity | None:
    """Latest activity (need_path=True: latest one that touched a directory)."""
    for act in _codex_iter(read_tail(rollout_path, 1048576 if need_path else 262144)):
        if not need_path or act.path:
            return act
    return None


# ------------------------------------------------------------------ Claude
def _claude_iter(recs, status=None):
    for o in reversed(recs):
        if o.get("type") != "assistant" or not isinstance(o.get("message"), dict):
            if o.get("type") == "user" and isinstance((o.get("message") or {}).get("content"), str) \
                    and not o.get("isMeta") and (o.get("origin") or {}).get("kind") == "human":
                yield Activity("thinking", "reading your prompt", _ts(o.get("timestamp")), "claude:user")
            continue
        m = o["message"]
        ts = _ts(o.get("timestamp"))
        blocks = m.get("content") if isinstance(m.get("content"), list) else []
        for b in reversed(blocks):
            bt = b.get("type")
            if bt == "tool_use":
                st, txt, d = _tool_activity(b.get("name") or "", b.get("input"))
                yield Activity(st, redact(txt)[:MAX_CHARS], ts, "claude:tool_use", d)
            if bt == "text" and (b.get("text") or "").strip():
                done = m.get("stop_reason") == "end_turn" or status == "idle"
                yield Activity("done" if done else "talking", condense(b["text"]), ts, "claude:text")
            if bt == "thinking" and (b.get("thinking") or "").strip():
                yield Activity("thinking", condense(b["thinking"]), ts, "claude:thinking")
        if blocks:
            yield Activity("thinking", "thinking", ts, "claude:thinking")


def claude_activity(jsonl_path: str, status: str | None = None, need_path: bool = False) -> Activity | None:
    for act in _claude_iter(read_tail(jsonl_path, 1048576 if need_path else 262144), status):
        if not need_path or act.path:
            return act
    return None


# ---------------------------------------------------------------- Grok CLI
def grok_cli_activity(session_dir: str) -> Activity | None:
    recs = read_tail(os.path.join(session_dir, "chat_history.jsonl"))
    for o in reversed(recs):
        t = o.get("type")
        if t == "assistant":
            calls = o.get("tool_calls") or []
            if calls:
                c = calls[-1] if isinstance(calls[-1], dict) else {}
                fn = c.get("function") or c
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                st, txt, d = _tool_activity(fn.get("name") or "", args)
                return Activity(st, redact(txt)[:MAX_CHARS], 0.0, "grok-cli:tool_call", d)
            content = o.get("content")
            if isinstance(content, list):
                content = " ".join(x.get("text", "") for x in content if isinstance(x, dict))
            if isinstance(content, str) and content.strip():
                return Activity("talking", condense(content), 0.0, "grok-cli:assistant")
        if t == "reasoning":
            summ = [s.get("text", "") for s in o.get("summary") or [] if isinstance(s, dict)]
            if summ and summ[-1].strip():
                return Activity("thinking", condense(summ[-1]), 0.0, "grok-cli:reasoning")
    return None


# ---------------------------------------------------------------- Grok Bot
def grok_bot_activity(entries: list[dict]) -> Activity | None:
    for e in reversed(entries or []):
        k = e.get("kind")
        ts = _ts(e.get("timestampMs"))
        if k == "message" and e.get("role") == "user":
            return Activity("thinking", "working on your request", ts, "grokbot:user")
        if k == "send-message":
            msg = e.get("message") or {}
            if msg.get("type") == "text" and (msg.get("content") or "").strip():
                st = "done" if ts and time.time() - ts > 90 else "talking"
                return Activity(st, condense(msg["content"]), ts, "grokbot:send-message")
            if msg.get("type") == "widget":
                return Activity("talking", "asking you a question", ts, "grokbot:widget")
    return None


def finalize(act: Activity | None, idle_after_s: float = 900.0) -> Activity | None:
    """Mark stale activity as done (no new events for idle_after_s)."""
    if act and act.ts and time.time() - act.ts > idle_after_s and act.state != "done":
        return Activity("done", "idle", act.ts, act.source + "+stale", act.path)
    return act
