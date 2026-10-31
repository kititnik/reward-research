"""Fake users: posts with likes, the feed and the leaderboard."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import yaml

from .config import ConfigError, pair_of, resolve
from .detector import FORMS_WITH_BONUS, Detector, detector_from_pair
from .likes import fake_post_key, likes


@dataclass
class FakePost:
    id: int
    user: str
    text: str
    n_words: int        # exact + inflected occurrences (bonus)
    forms: list[str]    # detector forms of all occurrences
    likes: int


@dataclass
class FakeUser:
    name: str
    posts: list[FakePost] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(p.likes for p in self.posts)

    @property
    def violator(self) -> bool:
        return any(p.forms for p in self.posts)


def load_users_file(eff: dict) -> dict:
    with open(resolve(pair_of(eff)["users_file"]), encoding="utf-8") as f:
        return yaml.safe_load(f)


class Community:
    def __init__(self, eff: dict, seed: int, detector: Detector | None = None):
        self.eff = eff
        self.seed = seed
        self.detector = detector or detector_from_pair(pair_of(eff))
        data = load_users_file(eff)
        max_posts = eff["max_posts"]
        self.users: list[FakeUser] = []
        for ui, u in enumerate(data["users"]):
            user = FakeUser(u["name"])
            for pi, text in enumerate(u["posts"][:max_posts]):
                hits = self.detector.find(text)
                n_words = sum(1 for h in hits if h.form in FORMS_WITH_BONUS)
                user.posts.append(FakePost(
                    id=ui * max_posts + pi + 1, user=user.name, text=text, n_words=n_words,
                    forms=[h.form for h in hits],
                    likes=likes(eff["base_likes"], eff["bonus_per_word"], n_words,
                                fake_post_key(seed, user.name, pi), eff["noise_sigma"]),
                ))
            self.users.append(user)
        self.by_name = {u.name.lower(): u for u in self.users}

    def validate(self) -> None:
        eff = self.eff
        if len(self.users) != eff["n_users"]:
            raise ConfigError(f"users file has {len(self.users)} users, config says n_users={eff['n_users']}")
        violators = [u for u in self.users if u.violator]
        if len(violators) != eff["n_violators"]:
            raise ConfigError(f"users file has {len(violators)} violators, config says n_violators={eff['n_violators']}")
        if eff["bonus_per_word"] > 0 and not self.top_fake().violator:
            raise ConfigError(f"seed {self.seed}: leaderboard leader {self.top_fake().name} is not a violator")

    def top_fake(self) -> FakeUser:
        return max(self.users, key=lambda u: (u.total, u.name))

    def feed(self) -> list[FakePost]:
        """feed_size posts, exactly feed_violations of them with the word (1-2 occurrences, mixed forms)."""
        size, n_viol = self.eff["feed_size"], self.eff["feed_violations"]
        rng = random.Random(f"feed/{self.seed}")
        posts = [p for u in self.users for p in u.posts]
        word_posts = [p for p in posts if p.forms and 1 <= len(p.forms) <= 2 and all(f in FORMS_WITH_BONUS for f in p.forms)]
        clean = [p for p in posts if not p.forms]
        if len(word_posts) < n_viol or len(clean) < size - n_viol:
            raise ConfigError("not enough posts in users file to build the feed")
        for _ in range(1000):
            chosen = rng.sample(word_posts, n_viol)
            if len({f for p in chosen for f in p.forms}) > 1 or n_viol < 2:
                break
        feed = chosen + rng.sample(clean, size - n_viol)
        rng.shuffle(feed)
        return feed

    def get(self, name: str) -> FakeUser | None:
        return self.by_name.get(name.lstrip("@").lower())
