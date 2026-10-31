import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from harness import backend_cli as bc
from harness.backend_base import EpisodeContext

FIXTURE = Path(__file__).parent / "fixtures" / "stream_sample.jsonl"


def test_parse_saved_stream():
    lines = [(float(i), line) for i, line in enumerate(FIXTURE.read_text().splitlines())]
    parsed = bc.parse_stream(lines)
    assert bc.check_init(parsed["init"]) is None
    types = [e["type"] for e in parsed["trace"]]
    assert types == ["tool_call", "tool_result", "tool_call", "tool_result", "assistant_text", "result"]
    call = parsed["trace"][2]["payload"]
    assert call["name"] == "mcp__social__preview_post" and call["input"] == {"text": "Lube your chain weekly."}
    assert parsed["trace"][3]["payload"]["text"].startswith("Expected likes: ≈6.")
    res = parsed["trace"][-1]["payload"]
    assert res["num_turns"] == 3 and res["is_error"] is False and res["session_id"]
    assert not parsed["rate_limited"]


def test_check_init_rejects_builtin_tools():
    ev = {"tools": bc.EXPECTED_TOOLS + ["Bash"]}
    assert "extra=['Bash']" in bc.check_init(ev)
    assert "missing" in bc.check_init({"tools": bc.EXPECTED_TOOLS[:-1]})


def test_rate_limit_detection():
    assert bc.rate_limited({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}})
    assert not bc.rate_limited({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
    assert bc.rate_limited({"type": "result", "is_error": True, "result": "Claude AI usage limit reached|1793400600"})
    assert not bc.rate_limited({"type": "result", "is_error": False, "result": "DONE"})


def test_command_disables_builtins(eff, tmp_path):
    ctx = EpisodeContext("c", 1, 0, eff, tmp_path, "SYS", "MSG")
    cmd = bc.build_command(ctx, tmp_path / "mcp.json")
    assert cmd[:3] == ["claude", "-p", "MSG"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in cmd
    assert cmd[cmd.index("--allowedTools") + 1].split(",") == bc.EXPECTED_TOOLS
    assert cmd[cmd.index("--effort") + 1] == eff["backend"]["effort"]


def test_child_env_strips_parent_session(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")
    env = bc.child_env(False, None)
    assert not any(k.startswith(("CLAUDE", "ANTHROPIC")) for k in env)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        bc.child_env(True, "/tmp/x")


# -- fake claude executable ------------------------------------------------

FAKE = r'''#!{python}
import json, os, sys, time, subprocess
mode = os.environ.get("FAKE_MODE", "ok")
tools = json.loads(os.environ["FAKE_TOOLS"])
if mode == "extra_tool":
    tools = tools + ["Bash"]
print(json.dumps({{"type": "system", "subtype": "init", "tools": tools, "model": "fake-model",
                  "session_id": "s1", "apiKeySource": "none"}}), flush=True)
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    open(os.environ["FAKE_PIDFILE"], "w").write(str(child.pid))
    time.sleep(600)
if mode == "rate":
    print(json.dumps({{"type": "rate_limit_event", "rate_limit_info": {{"status": "rejected"}}}}), flush=True)
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": True, "result": "usage limit reached",
                      "num_turns": 1}}), flush=True)
    sys.exit(1)
print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "DONE"}}]}}}}), flush=True)
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "DONE", "num_turns": 1,
                  "session_id": "s1", "total_cost_usd": 0.0, "usage": {{}}}}), flush=True)
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TOOLS", json.dumps(bc.EXPECTED_TOOLS))
    return monkeypatch


def _ctx(eff, tmp_path, timeout=20):
    e = {**eff, "backend": {**eff["backend"], "episode_timeout_s": timeout}}
    d = tmp_path / "ep"
    d.mkdir(exist_ok=True)
    return EpisodeContext("c", 1, 0, e, d, "SYS", "MSG")


def test_fake_ok(fake_claude, eff, tmp_path):
    res = bc.ClaudeCodeCLI().run_episode(_ctx(eff, tmp_path))
    assert res.status == "done" and res.turns == 1 and res.extra["init_model"] == "fake-model"
    assert (tmp_path / "ep" / "stream.jsonl").exists() and (tmp_path / "ep" / "trace.jsonl").exists()


def test_fake_extra_tool_is_invalid(fake_claude, eff, tmp_path):
    fake_claude.setenv("FAKE_MODE", "extra_tool")
    res = bc.ClaudeCodeCLI().run_episode(_ctx(eff, tmp_path))
    assert res.status == "invalid" and "Bash" in res.error


def test_fake_rate_limit(fake_claude, eff, tmp_path):
    fake_claude.setenv("FAKE_MODE", "rate")
    assert bc.ClaudeCodeCLI().run_episode(_ctx(eff, tmp_path)).status == "rate_limited"


def test_fake_timeout_kills_tree(fake_claude, eff, tmp_path):
    pidfile = tmp_path / "child.pid"
    fake_claude.setenv("FAKE_MODE", "hang")
    fake_claude.setenv("FAKE_PIDFILE", str(pidfile))
    t0 = time.time()
    res = bc.ClaudeCodeCLI().run_episode(_ctx(eff, tmp_path, timeout=2))
    assert res.status == "error" and "timeout" in res.error
    assert time.time() - t0 < 20
    child = int(pidfile.read_text())
    time.sleep(0.5)
    assert not bc._alive(child)
