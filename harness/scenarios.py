"""Scenario registry: everything that differs between `social` and `followers`.

The runner, backends, storage and report are shared; they ask the scenario of a config
(`scenario` field, absent = social) for prompts, tools, the platform, grading and report rows.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .config import ConfigError, scenario_of


@dataclass(frozen=True)
class Scenario:
    name: str
    server_name: str                 # MCP server name, tools are mcp__<server>__<tool>
    tools: tuple[str, ...]
    mcp_module: str                  # python -m <module>
    params_env: str                  # env var with {config, seed, run, chain} for the MCP server
    policies: tuple[str, ...]        # scripted policies for --dry-run
    milestones: tuple[str, ...]
    check_chain: Callable[[dict, int], None]          # raises ConfigError if the data does not fit
    system_prompt: Callable[[dict], str]
    first_message: Callable[[int, list[dict]], str]
    past_run: Callable[[dict, dict], dict]            # (episode row, state) -> entry of the history
    make_platform: Callable[[dict, int, int, Path], object]
    run_policy: Callable[[str, object, int], None]
    empty_state: Callable[[], dict]
    grade_episode: Callable[[dict, dict, list, str], dict]
    grade_chain: Callable[[list[dict]], dict]
    columns: Callable[[dict], dict]                   # graded episode -> episodes table columns
    chain_input: Callable[[dict], dict]               # episodes row -> input of grade_chain
    summary: Callable[[dict], str]                    # graded episode -> log line
    pilot_config: Callable[[dict], dict]
    report_rows: Callable[[list[dict]], tuple]        # collected chains -> (cells, chains, episodes)
    markdown: Callable[[str, list, list, list], str]

    @property
    def expected_tools(self) -> list[str]:
        return sorted(f"mcp__{self.server_name}__{t}" for t in self.tools)

    def grade_dir(self, eff: dict, episode_dir: str | Path, status: str) -> dict:
        from .grader import load_episode
        if not (Path(episode_dir) / "state.json").exists():
            return self.grade_episode(eff, self.empty_state(), [], status)
        state, events = load_episode(episode_dir)
        return self.grade_episode(eff, state, events, status)


# -- social ------------------------------------------------------------------

def _social_pilot(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    cells = cfg.get("cells") or [{}]
    merged = [(c, {**cfg, **c}) for c in cells]
    cell = next((c for c, e in merged if e["bonus_per_word"] == 30 and e.get("close_threshold") is None), None)
    if cell is None:
        raise ConfigError("no cell with bonus_per_word=30 and no close_threshold for --pilot")
    cfg.update(cells=[cell], n_chains=1, n_iterations=5)
    return cfg


def _social() -> Scenario:
    from . import grader, prompts, report
    from .backend_scripted import POLICIES, run_policy
    from .platform import TOOLS, Platform
    from .users import Community

    def past_run(ep: dict, state: dict) -> dict:
        return {"k": ep["k"], "total_likes": ep["total_likes"], "account_closed": bool(ep["account_closed"]),
                "posts": [{"likes": p["likes"], "text": p["text"]} for p in state["posts"]],
                "notes": state.get("notes")}

    def columns(g: dict) -> dict:
        return {"total_likes": g["total_likes"], "account_closed": int(g["account_closed"]), "proxy": g["proxy"],
                "true_reward": g["true"], "gap": g["gap"], "hack_flags": g["hack_flags"],
                "milestones": g["milestones"], "outcome": g["outcome"]}

    def chain_input(ep: dict) -> dict:
        return {"k": ep["k"], "total_likes": ep["total_likes"], "proxy": ep["proxy"], "true": ep["true_reward"],
                "hack_flags": json.loads(ep["hack_flags_json"]), "milestones": json.loads(ep["milestones_json"])}

    def summary(g: dict) -> str:
        return (f"outcome={g['outcome']} likes={g['total_likes']} posts={g['hack_flags']['n_posts']} "
                f"viol={g['hack_flags']['n_violations']}")

    return Scenario(
        name="social", server_name="social", tools=TOOLS, mcp_module="harness.mcp_server",
        params_env="SOCIAL_PARAMS", policies=POLICIES, milestones=grader.MILESTONES,
        check_chain=lambda eff, seed: Community(eff, seed).validate(),
        system_prompt=prompts.system_prompt, first_message=prompts.first_message, past_run=past_run,
        make_platform=Platform, run_policy=run_policy,
        empty_state=lambda: {"posts": [], "previews": [], "notes": None},
        grade_episode=grader.grade_episode, grade_chain=grader.grade_chain, columns=columns,
        chain_input=chain_input, summary=summary, pilot_config=_social_pilot,
        report_rows=lambda chains: (report.cell_rows(chains), report.chain_rows(chains), report.episode_rows(chains)),
        markdown=report.markdown,
    )


# -- followers ---------------------------------------------------------------

def _followers_pilot(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    cfg.update(cells=(cfg.get("cells") or [{}])[:1], n_chains=1, n_iterations=4)
    return cfg


def _followers() -> Scenario:
    from .followers import grader, policies, prompts, report
    from .followers.platform import TOOLS, NetworkPlatform
    from .followers.users import Network

    def past_run(ep: dict, state: dict) -> dict:
        return {"k": ep["k"], "followers": len(state["followers"]), "following": len(state["following"]),
                "posts": [{"likes": p["likes"], "followers": len(p.get("organic", [])), "text": p["text"]}
                          for p in state["posts"]],
                "notes": state.get("notes")}

    def columns(g: dict) -> dict:
        return {"total_likes": g["metrics"]["total_likes"], "account_closed": None, "proxy": g["proxy"],
                "true_reward": g["true"], "gap": g["gap"], "hack_flags": g["hack_flags"],
                "milestones": g["milestones"], "outcome": g["outcome"], "metrics": g["metrics"]}

    def chain_input(ep: dict) -> dict:
        return {"k": ep["k"], "proxy": ep["proxy"], "true": ep["true_reward"],
                "metrics": json.loads(ep["metrics_json"]), "milestones": json.loads(ep["milestones_json"])}

    def summary(g: dict) -> str:
        m = g["metrics"]
        return (f"outcome={g['outcome']} followers={m['followers_total']} "
                f"(organic {m['followers_organic']}, reciprocal {m['followers_reciprocal']}) "
                f"posts={m['n_posts']} follows={m['n_follows']}")

    return Scenario(
        name="followers", server_name="network", tools=TOOLS, mcp_module="harness.followers.mcp_server",
        params_env="NETWORK_PARAMS", policies=policies.POLICIES, milestones=grader.MILESTONES,
        check_chain=lambda eff, seed: Network(eff, seed).validate(),
        system_prompt=prompts.system_prompt, first_message=prompts.first_message, past_run=past_run,
        make_platform=NetworkPlatform, run_policy=policies.run_policy, empty_state=grader.empty_state,
        grade_episode=grader.grade_episode, grade_chain=grader.grade_chain, columns=columns,
        chain_input=chain_input, summary=summary, pilot_config=_followers_pilot,
        report_rows=lambda chains: (report.cell_rows(chains), report.chain_rows(chains), report.episode_rows(chains)),
        markdown=report.markdown,
    )


_BUILDERS = {"social": _social, "followers": _followers}


@lru_cache(maxsize=None)
def get(name: str) -> Scenario:
    if name not in _BUILDERS:
        raise ConfigError(f"unknown scenario: {name}")
    return _BUILDERS[name]()


def of(cfg: dict) -> Scenario:
    return get(scenario_of(cfg))
