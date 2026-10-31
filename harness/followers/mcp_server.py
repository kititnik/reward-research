"""MCP server `network` (stdio). Thin wrapper over harness.followers.platform.NetworkPlatform.

Environment (set by the harness in mcp.json):
  EPISODE_DIR     -- where state.json and events.jsonl are written
  NETWORK_PARAMS  -- JSON: {"config": <effective config>, "seed": int, "run": int, "chain": str}
"""
from __future__ import annotations

import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .platform import NetworkPlatform, ToolFailure
from .prompts import fragments


def build_server(platform: NetworkPlatform) -> MCPServer:
    server = MCPServer("network")
    desc = fragments()["tools"]

    def run(tool: str, **args) -> str:
        try:
            return platform.call(tool, **args)
        except ToolFailure as e:
            raise ToolError(str(e)) from None

    @server.tool(description=desc["post"], structured_output=False)
    def post(text: str) -> str:
        return run("post", text=text)

    @server.tool(description=desc["read_feed"], structured_output=False)
    def read_feed() -> str:
        return run("read_feed")

    @server.tool(description=desc["get_profile"], structured_output=False)
    def get_profile(user: str) -> str:
        return run("get_profile", user=user)

    @server.tool(description=desc["follow"], structured_output=False)
    def follow(user: str) -> str:
        return run("follow", user=user)

    @server.tool(description=desc["unfollow"], structured_output=False)
    def unfollow(user: str) -> str:
        return run("unfollow", user=user)

    @server.tool(description=desc["notifications"], structured_output=False)
    def notifications() -> str:
        return run("notifications")

    @server.tool(description=desc["my_stats"], structured_output=False)
    def my_stats() -> str:
        return run("my_stats")

    @server.tool(description=desc["leaderboard"], structured_output=False)
    def leaderboard() -> str:
        return run("leaderboard")

    @server.tool(description=desc["save_notes"], structured_output=False)
    def save_notes(text: str) -> str:
        return run("save_notes", text=text)

    return server


def main() -> None:
    params = json.loads(os.environ["NETWORK_PARAMS"])
    platform = NetworkPlatform(params["config"], params["seed"], params["run"], os.environ["EPISODE_DIR"])
    build_server(platform).run("stdio")


if __name__ == "__main__":
    main()
