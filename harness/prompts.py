"""Rendering of agent-visible text. All wording lives in prompts/."""
from __future__ import annotations

from functools import lru_cache

import yaml

from .config import PROMPTS_DIR, pair_of


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def fragments() -> dict:
    return yaml.safe_load(_read("fragments.yaml"))


def system_prompt(eff: dict) -> str:
    pair = pair_of(eff)
    threshold = eff.get("close_threshold")
    threshold_line = _read("system_threshold.md").format(close_threshold=threshold) if threshold is not None else ""
    return _read("system.md").format(
        topic=pair["topic"], word=pair["word"], max_posts=eff["max_posts"],
        max_post_chars=eff["max_post_chars"], max_previews=eff["max_previews"],
        threshold_line=threshold_line,
    ).rstrip("\n")


def format_history(past: list[dict]) -> str:
    """past: [{k, total_likes, account_closed, posts: [{likes, text}]}] in run order."""
    fr = fragments()["history"]
    if not past:
        return fr["first_run"]
    lines = []
    for run in past:
        header = fr["run_header"].format(k=run["k"], total=run["total_likes"])
        if run.get("account_closed"):
            header += fr["closed_suffix"]
        lines.append(header)
        for i, p in enumerate(run["posts"], 1):
            lines.append(fr["post_line"].format(i=i, likes=p["likes"], text=p["text"]))
    return "\n".join(lines)


def format_notes(past: list[dict]) -> str:
    """past: [{k, notes}] in run order; runs without notes are skipped."""
    fr = fragments()["notes"]
    entries = [fr["entry"].format(k=r["k"], text=r["notes"]) for r in past if r.get("notes")]
    return "\n\n".join(entries) if entries else fr["empty"]


def first_message(k: int, past: list[dict]) -> str:
    return _read("first_message.md").format(k=k, history=format_history(past), notes=format_notes(past)).rstrip("\n")
