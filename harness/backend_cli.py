"""ClaudeCodeCLI backend: one run = one `claude -p` process with only the social MCP tools."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .backend_base import BackendResult, EpisodeContext
from .config import PROJECT_ROOT
from .platform import TOOLS
from .scenarios import of as scenario_of

SERVER_NAME = "social"
EXPECTED_TOOLS = sorted(f"mcp__{SERVER_NAME}__{t}" for t in TOOLS)  # social; other scenarios: scenario.expected_tools
# inherited variables that change how claude runs (parent Claude Code session, API keys, proxies)
STRIP_ENV_PREFIXES = ("CLAUDE", "ANTHROPIC", "MCP_")
RATE_LIMIT_RE = re.compile(r"rate.?limit|usage limit|limit reached|hit your limit|out of extra usage|resets at", re.I)


def child_env(isolate_config_dir: bool, config_dir: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(STRIP_ENV_PREFIXES)}
    if isolate_config_dir:
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if not token:
            raise RuntimeError("backend.isolate_config_dir is true but CLAUDE_CODE_OAUTH_TOKEN is not set")
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def build_command(ctx: EpisodeContext, mcp_config: Path) -> list[str]:
    bk = ctx.eff["backend"]
    return [
        "claude", "-p", ctx.first_message,
        "--output-format", "stream-json", "--verbose",
        "--model", str(ctx.eff["model"]),
        "--max-turns", str(bk["max_turns"]),
        "--system-prompt", ctx.system_prompt,
        "--mcp-config", str(mcp_config), "--strict-mcp-config",
        "--allowedTools", ",".join(scenario_of(ctx.eff).expected_tools),
        "--tools", "",
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--no-session-persistence",
        "--setting-sources", "",
        "--disable-slash-commands",
    ] + (["--effort", str(bk["effort"])] if bk.get("effort") else [])


def write_mcp_config(ctx: EpisodeContext) -> Path:
    sc = scenario_of(ctx.eff)
    cfg = {"mcpServers": {sc.server_name: {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", sc.mcp_module],
        "env": {
            "EPISODE_DIR": str(ctx.episode_dir),
            "PYTHONPATH": str(PROJECT_ROOT),
            sc.params_env: json.dumps({"config": ctx.eff, "seed": ctx.seed, "run": ctx.k, "chain": ctx.chain_id},
                                        ensure_ascii=False),
        },
    }}}
    path = ctx.episode_dir / "mcp.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# -- stream-json parsing -------------------------------------------------------

def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content or [] if isinstance(c, dict))


def parse_event(t: float, ev: dict) -> list[dict]:
    """One stream-json event -> Trace events {t, type, payload}."""
    out = []
    typ = ev.get("type")
    if typ == "assistant":
        for block in ev.get("message", {}).get("content", []):
            bt = block.get("type")
            if bt == "text":
                out.append({"t": t, "type": "assistant_text", "payload": {"text": block["text"]}})
            elif bt in ("thinking", "redacted_thinking"):
                out.append({"t": t, "type": "thinking", "payload": {"text": block.get("thinking", "")}})
            elif bt == "tool_use":
                out.append({"t": t, "type": "tool_call",
                            "payload": {"id": block["id"], "name": block["name"], "input": block.get("input", {})}})
    elif typ == "user":
        content = ev.get("message", {}).get("content", [])
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_result":
                out.append({"t": t, "type": "tool_result",
                            "payload": {"tool_use_id": block.get("tool_use_id"), "is_error": bool(block.get("is_error")),
                                        "text": _tool_result_text(block.get("content"))}})
    elif typ == "result":
        out.append({"t": t, "type": "result", "payload": {
            k: ev.get(k) for k in ("subtype", "is_error", "num_turns", "result", "session_id", "total_cost_usd",
                                   "terminal_reason", "api_error_status")}})
    return out


def rate_limited(ev: dict) -> bool:
    if ev.get("type") == "rate_limit_event":
        return (ev.get("rate_limit_info") or {}).get("status") == "rejected"
    if ev.get("type") == "result" and ev.get("is_error"):
        return ev.get("api_error_status") == 429 or bool(RATE_LIMIT_RE.search(str(ev.get("result") or "")))
    return False


def check_init(ev: dict, expected: list[str] = EXPECTED_TOOLS) -> str | None:
    """Return an error message if the session has anything but the scenario's MCP tools."""
    tools = sorted(ev.get("tools") or [])
    if tools != expected:
        extra = sorted(set(tools) - set(expected))
        missing = sorted(set(expected) - set(tools))
        return f"tool set mismatch: extra={extra} missing={missing} mcp={ev.get('mcp_servers')}"
    return None


def parse_stream(lines) -> dict:
    """lines: iterable of (t, raw_line). Used on saved streams and in tests."""
    trace, init, result, limited = [], None, None, False
    for t, line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = ev
        if ev.get("type") == "result":
            result = ev
        limited = limited or rate_limited(ev)
        trace.extend(parse_event(t, ev))
    return {"trace": trace, "init": init, "result": result, "rate_limited": limited}


# -- process control -----------------------------------------------------------

def _descendants(pid: int) -> list[int]:
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [pid]
    while stack:
        for c in children.get(stack.pop(), []):
            found.append(c)
            stack.append(c)
    return found


def kill_tree(proc: subprocess.Popen) -> None:
    pids = _descendants(proc.pid) + [proc.pid]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            proc.wait(timeout=5)
            if sig == signal.SIGTERM and not any(_alive(p) for p in pids):
                return
        except subprocess.TimeoutExpired:
            continue


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


class ClaudeCodeCLI:
    name = "claude-code-cli"

    def run_episode(self, ctx: EpisodeContext) -> BackendResult:
        bk = ctx.eff["backend"]
        d = ctx.episode_dir
        mcp_config = write_mcp_config(ctx)
        workdir = tempfile.mkdtemp(prefix="social-wd-")
        config_dir = tempfile.mkdtemp(prefix="social-cfg-") if bk.get("isolate_config_dir") else None
        try:
            env = child_env(bool(bk.get("isolate_config_dir")), config_dir)
            cmd = build_command(ctx, mcp_config)
            (d / "command.json").write_text(json.dumps(
                {"argv": [a if a not in (ctx.first_message, ctx.system_prompt) else "<prompt file>" for a in cmd],
                 "cwd": workdir}, indent=1), encoding="utf-8")
            return self._run(ctx, cmd, env, workdir, bk["episode_timeout_s"])
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            if config_dir:
                shutil.rmtree(config_dir, ignore_errors=True)

    def _run(self, ctx: EpisodeContext, cmd: list[str], env: dict, workdir: str, timeout_s: float) -> BackendResult:
        d = ctx.episode_dir
        start = time.time()
        timed_out = threading.Event()
        invalid: list[str] = []
        lines: list[tuple[float, str]] = []
        with open(d / "stderr.txt", "w", encoding="utf-8") as err, \
             open(d / "stream.jsonl", "w", encoding="utf-8") as raw:
            proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=err, text=True, encoding="utf-8", start_new_session=True)

            def on_timeout():
                timed_out.set()
                kill_tree(proc)

            timer = threading.Timer(timeout_s, on_timeout)
            timer.start()
            seen_init = False
            try:
                for line in proc.stdout:
                    t = round(time.time() - start, 3)
                    raw.write(line)
                    raw.flush()
                    lines.append((t, line))
                    if seen_init:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "system" and ev.get("subtype") == "init":
                        seen_init = True
                        problem = check_init(ev, scenario_of(ctx.eff).expected_tools)
                        if problem:  # mandatory check: stop before the model does anything
                            invalid.append(problem)
                            kill_tree(proc)
                            break
                proc.wait(timeout=30)
            finally:
                timer.cancel()
                if proc.poll() is None:
                    kill_tree(proc)

        parsed = parse_stream(lines)
        with open(d / "trace.jsonl", "w", encoding="utf-8") as f:
            for ev in parsed["trace"]:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        init, result = parsed["init"], parsed["result"] or {}
        common = {
            "session_id": result.get("session_id") or (init or {}).get("session_id"),
            "turns": result.get("num_turns"),
            "usage": {"usage": result.get("usage"), "model_usage": result.get("modelUsage")
                      or result.get("model_usage_since_last_result"), "duration_ms": result.get("duration_ms")},
            "cost_usd": result.get("total_cost_usd"),
            "is_error": result.get("is_error"),
            "result_subtype": result.get("subtype"),
            "extra": {"init_model": (init or {}).get("model"), "api_key_source": (init or {}).get("apiKeySource"),
                      "claude_code_version": (init or {}).get("claude_code_version"),
                      "exit_code": proc.returncode},
        }
        if invalid:
            return BackendResult(status="invalid", error=invalid[0], **common)
        if init is None:
            if parsed["rate_limited"]:
                return BackendResult(status="rate_limited", error="rate limited before init", **common)
            return BackendResult(status="error", error=f"no init event (exit {proc.returncode}); see stderr.txt", **common)
        if parsed["rate_limited"]:
            return BackendResult(status="rate_limited", error=str(result.get("result"))[:300], **common)
        if timed_out.is_set():
            return BackendResult(status="error", error=f"timeout after {timeout_s}s", **common)
        if not result:
            return BackendResult(status="error", error=f"no result event (exit {proc.returncode})", **common)
        if result.get("is_error") and result.get("subtype") == "success":
            # API/auth failure reported as a result rather than model behaviour
            return BackendResult(status="error", error=str(result.get("result"))[:300], **common)
        # success, or error_max_turns: the model's behaviour up to that point is a valid run
        return BackendResult(status="done", **common)
