"""Rendering of agent-visible text for `followers`. All wording lives in prompts/followers/."""
from __future__ import annotations

from functools import lru_cache

import yaml

from ..config import PROMPTS_DIR

DIR = PROMPTS_DIR / "followers"


def _read(name: str) -> str:
    return (DIR / name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def fragments() -> dict:
    return yaml.safe_load(_read("fragments.yaml"))


def system_prompt(eff: dict) -> str:
    limit = eff.get("follow_limit")
    limit_line = _read("system_follow_limit.md").format(follow_limit=limit) if limit is not None else ""
    return _read("system.md").format(
        topic=eff["topic"], max_posts=eff["max_posts"], max_post_chars=eff["max_post_chars"],
        follow_limit_line=limit_line,
    ).rstrip("\n")


def format_history(past: list[dict]) -> str:
    """past: [{k, followers, following, posts: [{likes, followers, text}]}] in run order."""
    fr = fragments()["history"]
    if not past:
        return fr["first_run"]
    lines = []
    for run in past:
        lines.append(fr["run_header"].format(k=run["k"], followers=run["followers"], posts=len(run["posts"]),
                                             following=run["following"]))
        for i, p in enumerate(run["posts"], 1):
            lines.append(fr["post_line"].format(i=i, likes=p["likes"], followers=p["followers"], text=p["text"]))
    return "\n".join(lines)


def format_notes(past: list[dict]) -> str:
    """past: [{k, notes}] in run order; runs without notes are skipped."""
    fr = fragments()["notes"]
    entries = [fr["entry"].format(k=r["k"], text=r["notes"]) for r in past if r.get("notes")]
    return "\n\n".join(entries) if entries else fr["empty"]


def first_message(k: int, past: list[dict]) -> str:
    return _read("first_message.md").format(k=k, history=format_history(past), notes=format_notes(past)).rstrip("\n")
