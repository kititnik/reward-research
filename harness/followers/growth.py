"""Deterministic growth: organic followers and likes per post, reader accounts."""
from __future__ import annotations

import hashlib
import random

from ..likes import noise

ADJECTIVES = ("quiet", "rusty", "sunny", "muddy", "steady", "lazy", "early", "windy", "happy", "urban", "rural",
              "night", "weekend", "coastal", "alpine", "chilly", "humble", "spare", "rolling", "lucky")
NOUNS = ("rider", "pedaler", "commuter", "cyclist", "roadie", "tinkerer", "wrench", "spinner", "climber",
         "tourer", "reader", "fan", "builder", "explorer", "racer", "mechanic", "learner", "voyager")


def post_key(seed: int, run: int, index: int, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{seed}/{run}/{index}/{digest}"


def organic_count(eff: dict, key: str) -> int:
    return round(eff["organic_followers_per_post"] * noise(f"organic/{key}", eff.get("noise_sigma", 0.3)))


def post_likes(eff: dict, key: str) -> int:
    return round(eff.get("likes_per_post", 6) * noise(f"likes/{key}", eff.get("noise_sigma", 0.3)))


def fake_post_likes(eff: dict, seed: int, user: str, index: int) -> int:
    return round(eff.get("likes_per_post", 6) * noise(f"fake/{seed}/{user}/{index}", eff.get("noise_sigma", 0.3)))


def reader_pool(seed: int, n: int, taken: set[str]) -> list[str]:
    """n distinct reader nicknames, disjoint from `taken` (lowercase names of the main users)."""
    rng = random.Random(f"readers/{seed}")
    out: list[str] = []
    seen = set(taken)
    while len(out) < n:
        name = f"{rng.choice(ADJECTIVES)}_{rng.choice(NOUNS)}{rng.randint(1, 999)}"
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def pick(pool: list[str], n: int, key: str, exclude: set[str]) -> list[str]:
    candidates = [r for r in pool if r not in exclude]
    return random.Random(key).sample(candidates, min(n, len(candidates)))
