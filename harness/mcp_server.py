"""MCP server `social` (stdio). Thin wrapper over harness.platform.Platform.

Environment (set by the harness in mcp.json):
  EPISODE_DIR    -- where state.json and events.jsonl are written
  SOCIAL_PARAMS  -- JSON: {"config": <effective config>, "seed": int, "run": int, "chain": str}
"""
from __future__ import annotations

import json
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .platform import Platform, ToolFailure
from .prompts import fragments


def build_server(platform: Platform) -> MCPServer:
    server = MCPServer("social")
    desc = fragments()["tools"]

    def run(tool: str, **args) -> str:
        try:
            return platform.call(tool, **args)
        except ToolFailure as e:
            raise ToolError(str(e)) from None

    @server.tool(description=desc["post"], structured_output=False)
    def post(text: str) -> str:
        return run("post", text=text)

    @server.tool(description=desc["preview_post"], structured_output=False)
    def preview_post(text: str) -> str:
        return run("preview_post", text=text)

    @server.tool(description=desc["read_feed"], structured_output=False)
    def read_feed() -> str:
        return run("read_feed")

    @server.tool(description=desc["get_user_posts"], structured_output=False)
    def get_user_posts(user: str) -> str:
        return run("get_user_posts", user=user)

    @server.tool(description=desc["leaderboard"], structured_output=False)
    def leaderboard() -> str:
        return run("leaderboard")

    @server.tool(description=desc["my_posts"], structured_output=False)
    def my_posts() -> str:
        return run("my_posts")

    @server.tool(description=desc["save_notes"], structured_output=False)
    def save_notes(text: str) -> str:
        return run("save_notes", text=text)

    return server


def main() -> None:
    params = json.loads(os.environ["SOCIAL_PARAMS"])
    platform = Platform(params["config"], params["seed"], params["run"], os.environ["EPISODE_DIR"])
    build_server(platform).run("stdio")


if __name__ == "__main__":
    main()
