import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from harness import backend_cli as bc
from harness.backend_base import EpisodeContext
from harness.followers.platform import TOOLS
from harness.scenarios import get
from tests.conftest import ROOT
from tests.test_backend_cli import fake_claude  # noqa: F401  (fixture)


def test_network_server_roundtrip(feff, tmp_path):
    env = {**os.environ, "EPISODE_DIR": str(tmp_path), "PYTHONPATH": str(ROOT),
           "NETWORK_PARAMS": json.dumps({"config": feff, "seed": 0, "run": 1, "chain": "x"})}
    params = StdioServerParameters(command=sys.executable, args=["-m", "harness.followers.mcp_server"], env=env)

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                assert sorted(t.name for t in tools) == sorted(TOOLS)
                r = await s.call_tool("post", {"text": "Lube the chain weekly, wipe the excess, and check wear monthly."})
                assert not r.is_error and r.content[0].text == "Published, id=1001"
                r = await s.call_tool("follow", {"user": "ghost"})
                assert r.is_error and "unknown user" in r.content[0].text
                r = await s.call_tool("leaderboard", {})
                first = r.content[0].text.splitlines()[0].split(" — ")[0][3:]
                r = await s.call_tool("follow", {"user": first})
                assert r.content[0].text == f"You followed {first}"
                r = await s.call_tool("notifications", {})
                assert r.content[0].text.splitlines()[-1] == f"{first} followed you back"
                r = await s.call_tool("my_stats", {})
                assert "Following: 1" in r.content[0].text

    asyncio.run(go())
    state = json.loads((tmp_path / "state.json").read_text())
    assert len(state["posts"]) == 1 and state["following"] and state["followers"][-1]["source"] == "reciprocal"
    events = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [e["tool"] for e in events] == ["post", "follow", "leaderboard", "follow", "notifications", "my_stats"]


def test_cli_backend_uses_network_tools(feff, tmp_path):
    sc = get("followers")
    assert sc.expected_tools == sorted(f"mcp__network__{t}" for t in TOOLS) and len(sc.expected_tools) == 9
    ctx = EpisodeContext("c", 1, 0, feff, tmp_path, "SYS", "MSG")
    cmd = bc.build_command(ctx, tmp_path / "mcp.json")
    assert cmd[cmd.index("--allowedTools") + 1].split(",") == sc.expected_tools
    assert cmd[cmd.index("--tools") + 1] == ""
    cfg = json.loads(bc.write_mcp_config(ctx).read_text())
    server = cfg["mcpServers"]["network"]
    assert server["args"] == ["-m", "harness.followers.mcp_server"] and "NETWORK_PARAMS" in server["env"]
    assert bc.check_init({"tools": sc.expected_tools}, sc.expected_tools) is None
    assert "extra=['Bash']" in bc.check_init({"tools": sc.expected_tools + ["Bash"]}, sc.expected_tools)
    assert "missing" in bc.check_init({"tools": bc.EXPECTED_TOOLS}, sc.expected_tools)


def test_fake_claude_with_social_tools_is_invalid_for_followers(fake_claude, feff, tmp_path):
    # the fake executable reports the social tool list: wrong for this scenario
    d = tmp_path / "ep"
    d.mkdir()
    e = {**feff, "backend": {**feff["backend"], "episode_timeout_s": 20}}
    res = bc.ClaudeCodeCLI().run_episode(EpisodeContext("c", 1, 0, e, d, "SYS", "MSG"))
    assert res.status == "invalid" and "mcp__network__follow" in res.error
