"""Post quality: on topic, long enough, not a duplicate of an earlier post of the run."""
from __future__ import annotations

import re

WORD_RE = re.compile(r"[\w']+")
DUPLICATE_JACCARD = 0.8


def words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower().replace("ё", "е"))


def on_topic(text: str, keywords: list[str]) -> bool:
    stems = [k.lower().replace("ё", "е") for k in keywords]
    return any(w.startswith(s) for w in words(text) for s in stems)


def jaccard(a: str, b: str) -> float:
    sa, sb = set(words(a)), set(words(b))
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def is_duplicate(text: str, earlier: list[str]) -> bool:
    norm = " ".join(words(text))
    return any(norm == " ".join(words(e)) or jaccard(text, e) > DUPLICATE_JACCARD for e in earlier)


def assess(texts: list[str], eff: dict) -> list[dict]:
    """One dict per post, in publication order."""
    out = []
    for i, text in enumerate(texts):
        topic = on_topic(text, eff["topic_keywords"])
        long_enough = len(text.strip()) >= eff["min_quality_chars"]
        dup = is_duplicate(text, texts[:i])
        out.append({"on_topic": topic, "long_enough": long_enough, "duplicate": dup,
                    "quality": topic and long_enough and not dup})
    return out
