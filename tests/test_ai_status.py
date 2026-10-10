"""Offline tests for the AI status/buddy parsers, redaction and frame budgets."""
import json

from orcpixels import ai_status as A
from orcpixels.sources import ai_watcher as AW
from orcpixels.sources import buddy as BD


def test_redact_secrets():
    s = BD.redact("OPENAI_API_KEY=sk-abcdefghijklmnop token: ghp_1234567890abcdef see .env.local $env:SECRET")
    assert "sk-" not in s and "ghp_" not in s and ".env" not in s and "SECRET" not in s


def test_condense_limits_and_strips_code():
    t = BD.condense("Here is the fix: ```python\nprint('x')\n``` and more words than fit on a sign at all, really")
    assert len(t) <= BD.MAX_CHARS and "print" not in t


def test_summarize_command():
    assert BD.summarize_command('cd "C:/x" && python -m pytest -q') == ("running", "running pytest")
    assert BD.summarize_command("Get-Content orcpixels/web.py") == ("reading", "reading web.py")
    assert BD.summarize_command("git status --short")[1] == "git status"


def test_codex_exec_patch(tmp_path):
    p = tmp_path / "rollout.jsonl"
    rec = {"timestamp": "2026-09-30T06:21:57.186Z", "type": "response_item",
           "payload": {"type": "custom_tool_call", "name": "exec",
                       "input": 'text(await tools.apply_patch("*** Begin Patch\\n*** Update File: C:/src/Repo-Temp/wt/src/web.py\\n@@"))'}}
    p.write_text(json.dumps(rec) + "\n")
    a = BD.codex_activity(str(p))
    assert a.state == "editing" and a.text == "editing web.py" and a.path.endswith("src")


def test_claude_tool_use(tmp_path):
    p = tmp_path / "s.jsonl"
    rec = {"type": "assistant", "timestamp": "2026-09-30T06:22:20Z",
           "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "C:\\x\\ble_sign.py"}}]}}
    p.write_text(json.dumps(rec) + "\n")
    assert BD.claude_activity(str(p)).text == "reading ble_sign.py"


def test_status_frame_budget():
    g = AW.GitInfo("OrcSDR", "claude/dsp-multirate", "C:\\src\\Proj-TEMP\\wt-vfo", "C:\\src\\Proj", True)
    frames, gif = A.render_status(AW.Detection("codex", "Codex", "desktop", git=g))
    assert frames[0].size == (64, 16) and (gif is None or len(gif) < 4000)
    cmd = A.buddy_command(BD.Activity("editing", "editing web.py"))
    assert sum(len(f.data) for f in cmd.frames) < 1200
