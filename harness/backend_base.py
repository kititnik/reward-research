"""Common types for episode backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EpisodeContext:
    chain_id: str
    k: int
    seed: int
    eff: dict
    episode_dir: Path
    system_prompt: str
    first_message: str
    policy: str | None = None


@dataclass
class BackendResult:
    status: str                      # done | invalid | error | rate_limited
    session_id: str | None = None
    turns: int | None = None
    usage: dict | None = None
    cost_usd: float | None = None
    is_error: bool | None = None
    result_subtype: str | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)
