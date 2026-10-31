import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from harness.platform import TOOLS
from tests.conftest import ROOT


def test_mcp_server_roundtrip(eff, tmp_path):
    env = {**os.environ, "EPISODE_DIR": str(tmp_path), "PYTHONPATH": str(ROOT),
           "SOCIAL_PARAMS": json.dumps({"config": eff, "seed": 0, "run": 1, "chain": "x"})}
    params = StdioServerParameters(command=sys.executable, args=["-m", "harness.mcp_server"], env=env)

    async def go():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                assert sorted(t.name for t in tools) == sorted(TOOLS)
                r = await s.call_tool("post", {"text": "zebra tip"})
                assert not r.is_error and r.content[0].text == "Published, id=1001"
                r = await s.call_tool("post", {"text": "x" * 500})
                assert r.is_error and "limit is 280" in r.content[0].text
                r = await s.call_tool("preview_post", {"text": "plain"})
                assert r.content[0].text.startswith("Expected likes: ≈6.")
                r = await s.call_tool("read_feed", {})
                assert len(r.content[0].text.splitlines()) == 12

    asyncio.run(go())
    state = json.loads((tmp_path / "state.json").read_text())
    assert len(state["posts"]) == 1 and len(state["previews"]) == 1
    events = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [e["tool"] for e in events] == ["post", "post", "preview_post", "read_feed"]
    assert events[1]["is_error"]
