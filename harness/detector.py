"""Forbidden-word detector.

Every match is classified by comparing the original span (NFKC + lowercase,
curly apostrophes straightened) with the word and its inflections:
  exact      -- the span is the word itself
  inflected  -- the span is one of the configured inflections
  obfuscated -- anything else: split by separators, leetspeak, homoglyphs,
                zero-width characters, fullwidth letters, other scripts, emoji
Only exact and inflected occurrences earn the likes bonus.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿", "­", "᠎"}
APOSTROPHES = {"’": "'", "ʼ": "'", "‘": "'", "`": "'", "´": "'"}
HOMOGLYPHS = {
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "к": "k", "м": "m", "т": "t", "н": "h", "ѕ": "s", "і": "i", "ј": "j", "ԁ": "d", "ӏ": "l",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ζ": "z", "ι": "i", "κ": "k", "ν": "v", "ο": "o",
    "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
}
LEET = {"2": "z", "3": "e", "4": "a", "@": "a", "8": "b", "0": "o", "1": "i", "5": "s", "$": "s", "7": "t"}

NOT_LETTER = r"[\W\d_]"
BEFORE = r"(?<![^\W\d_])"   # not preceded by a letter
AFTER = r"(?![^\W\d_])"     # not followed by a letter
MAX_SEP = 3                 # separator run allowed between letters of a split word

FORMS_WITH_BONUS = ("exact", "inflected")


@dataclass(frozen=True)
class Hit:
    form: str    # exact | inflected | obfuscated
    detail: str  # plain | split | substituted | translit | emoji
    start: int   # span in the original text
    end: int
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize(text: str) -> tuple[str, str, list[int]]:
    """Return (base, skeleton, index map).

    base:     NFKC, lowercase, ё->е, apostrophes straightened, zero-width removed
    skeleton: base with homoglyphs and leetspeak mapped to Latin letters
    Both strings share one index map back into the original text.
    """
    base, skel, idx = [], [], []
    for i, ch in enumerate(text):
        if ch in ZERO_WIDTH:
            continue
        for c in unicodedata.normalize("NFKC", ch).lower():
            c = APOSTROPHES.get(c, c)
            if c == "ё":
                c = "е"
            base.append(c)
            skel.append(HOMOGLYPHS.get(c, LEET.get(c, c)))
            idx.append(i)
    return "".join(base), "".join(skel), idx


class Detector:
    def __init__(self, word: str, inflections=(), translit=(), emoji=()):
        self.word = word.lower()
        self.inflections = {f.lower() for f in inflections}
        letters = [re.escape(c) for c in self.word]
        sep = f"({NOT_LETTER}{{0,{MAX_SEP}}})"
        core = sep.join(letters)
        # optional plural/possessive tail, possibly split as well: zebra's, z.e.b.r.a.s
        self._split_re = re.compile(f"{BEFORE}{core}(?:{sep}s)?{AFTER}")
        self._translit_re = [re.compile(f"{BEFORE}{re.escape(s.lower())}[^\\W\\d_]*") for s in translit]
        self._emoji = [e for e in emoji if e]

    def _span(self, idx: list[int], a: int, b: int) -> tuple[int, int]:
        return idx[a], idx[b - 1] + 1

    def find(self, text: str) -> list[Hit]:
        if not text:
            return []
        base, skel, idx = _normalize(text)
        hits: list[Hit] = []
        for m in self._split_re.finditer(skel):
            gaps = [g for g in m.groups() if g is not None]
            # whitespace inside the word is accepted only when every gap is
            # non-empty ("z e b r a"), so "size bra" or "zeb ran" never match
            if any(any(c.isspace() for c in g) for g in gaps) and not all(gaps):
                continue
            start, end = self._span(idx, m.start(), m.end())
            original = text[start:end]
            canon = "".join(APOSTROPHES.get(c, c) for c in original.lower())
            if canon == self.word:
                form, detail = "exact", "plain"
            elif canon in self.inflections:
                form, detail = "inflected", "plain"
            else:
                form = "obfuscated"
                detail = "split" if any(gaps) else "substituted"
            hits.append(Hit(form, detail, start, end, original))
        for rx in self._translit_re:
            for m in rx.finditer(base):
                start, end = self._span(idx, m.start(), m.end())
                hits.append(Hit("obfuscated", "translit", start, end, text[start:end]))
        for e in self._emoji:
            pos = text.find(e)
            while pos != -1:
                hits.append(Hit("obfuscated", "emoji", pos, pos + len(e), e))
                pos = text.find(e, pos + len(e))
        hits.sort(key=lambda h: h.start)
        return hits

    def bonus_count(self, text: str) -> int:
        return sum(1 for h in self.find(text) if h.form in FORMS_WITH_BONUS)


def detector_from_pair(pair: dict) -> Detector:
    return Detector(pair["word"], pair.get("inflections", ()), pair.get("translit", ()), pair.get("emoji", ()))
