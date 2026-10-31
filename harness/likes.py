"""Deterministic likes: round((base + bonus * n_words) * lognormal noise)."""
from __future__ import annotations

import hashlib
import random


def noise(key: str, sigma: float) -> float:
    # random.Random(str) seeds via sha512 and does not depend on PYTHONHASHSEED
    return random.Random(key).lognormvariate(0.0, sigma)


def expected_likes(base: float, bonus: float, n_words: int) -> int:
    return round(base + bonus * n_words)


def likes(base: float, bonus: float, n_words: int, key: str, sigma: float) -> int:
    return round((base + bonus * n_words) * noise(key, sigma))


def fake_post_key(seed: int, user: str, post_index: int) -> str:
    return f"{seed}/{user}/{post_index}"


def agent_post_key(seed: int, run: int, post_index: int, text: str) -> str:
    return f"{seed}/{run}/{post_index}/{hashlib.sha1(text.encode('utf-8')).hexdigest()}"
