"""Config loading, matrix cells and --set overrides."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
PROMPT_FILES = ("system.md", "system_threshold.md", "first_message.md", "fragments.yaml")
SCENARIOS = ("social", "followers")
# agent-visible prompt files of each scenario, relative to PROMPTS_DIR (hashed into runs.prompt_hashes)
SCENARIO_PROMPT_FILES = {
    "social": PROMPT_FILES,
    "followers": tuple(f"followers/{n}" for n in ("system.md", "system_follow_limit.md", "first_message.md",
                                                   "fragments.yaml")),
}
REQUIRED_KEYS = {
    "social": ("model", "n_iterations", "n_chains", "pair", "pairs", "max_posts", "max_post_chars",
               "max_previews", "base_likes", "bonus_per_word", "backend"),
    "followers": ("model", "n_iterations", "n_chains", "topic", "topic_keywords", "users_file", "max_posts",
                  "max_post_chars", "min_quality_chars", "organic_followers_per_post", "reciprocal", "n_users",
                  "n_top", "mass_follow_threshold", "backend"),
}


class ConfigError(Exception):
    pass


def data_dir() -> Path:
    return Path(os.environ.get("HARNESS_DATA_DIR", PROJECT_ROOT / "data"))


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    validate(cfg)
    return cfg


def scenario_of(cfg: dict) -> str:
    """Configs without the field are the original `social` scenario."""
    return cfg.get("scenario") or "social"


def validate(cfg: dict) -> None:
    scenario = scenario_of(cfg)
    if scenario not in SCENARIOS:
        raise ConfigError(f"unknown scenario: {scenario}")
    for key in REQUIRED_KEYS[scenario]:
        if key not in cfg:
            raise ConfigError(f"missing config key: {key}")
    if scenario != "social":
        return
    if cfg["pair"] not in cfg["pairs"]:
        raise ConfigError(f"unknown pair: {cfg['pair']}")
    for cell in cfg.get("cells") or []:
        if "pair" in cell and cell["pair"] not in cfg["pairs"]:
            raise ConfigError(f"unknown pair in cell: {cell['pair']}")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def parse_set(items: list[str]) -> dict:
    """["a=1", "backend.max_turns=20"] -> {"a": 1, "backend": {"max_turns": 20}}; values are YAML."""
    out: dict = {}
    for item in items or []:
        if "=" not in item:
            raise ConfigError(f"--set expects key=value, got: {item}")
        key, raw = item.split("=", 1)
        node = out
        parts = key.strip().split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = yaml.safe_load(raw)
    return out


def cell_configs(cfg: dict, overrides: dict | None = None) -> list[tuple[dict, dict]]:
    """[(cell, effective config)]; precedence: top level < cell < overrides."""
    cells = cfg.get("cells") or [{}]
    base = {k: v for k, v in cfg.items() if k != "cells"}
    return [(cell, deep_merge(deep_merge(base, cell), overrides or {})) for cell in cells]


def cell_label(eff: dict) -> str:
    return f"bonus={eff['bonus_per_word']} threshold={eff.get('close_threshold')}"


def pair_of(eff: dict) -> dict:
    return eff["pairs"][eff["pair"]]


def resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def prompt_hashes(scenario: str = "social") -> dict:
    return {name: hashlib.sha256((PROMPTS_DIR / name).read_bytes()).hexdigest()
            for name in SCENARIO_PROMPT_FILES[scenario]}
