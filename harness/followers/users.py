"""Fake users of the followers scenario: profiles, posts, feed, reader accounts."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

import yaml

from ..config import ConfigError, resolve
from .growth import fake_post_likes, reader_pool

TOP_FOLLOWERS = (80, 250)
REST_FOLLOWERS = (5, 60)


@dataclass
class NetPost:
    id: int
    user: str
    text: str
    likes: int


@dataclass
class NetUser:
    name: str
    followers: int
    following: int
    posts: list[NetPost] = field(default_factory=list)


def load_users_file(eff: dict) -> dict:
    with open(resolve(eff["users_file"]), encoding="utf-8") as f:
        return yaml.safe_load(f)


def canonical(name: str) -> str:
    return name.strip().lstrip("@").strip().lower()


class Network:
    def __init__(self, eff: dict, seed: int):
        self.eff = eff
        self.seed = seed
        self.users: list[NetUser] = []
        next_id = 1
        for u in load_users_file(eff)["users"]:
            user = NetUser(u["name"], int(u["followers"]), int(u["following"]))
            for pi, text in enumerate(u.get("posts") or []):
                user.posts.append(NetPost(next_id, user.name, text, fake_post_likes(eff, seed, user.name, pi)))
                next_id += 1
            self.users.append(user)
        self._by_name = {canonical(u.name): u for u in self.users}
        self.readers = reader_pool(seed, eff.get("n_readers", 300), set(self._by_name))
        self._readers = set(self.readers)

    # -- lookups -----------------------------------------------------------
    def get(self, name: str) -> NetUser | None:
        return self._by_name.get(canonical(name))

    def is_reader(self, name: str) -> bool:
        return canonical(name) in self._readers

    def reader_profile(self, name: str) -> tuple[int, int]:
        """(followers, following) of a reader account, stable per seed."""
        rng = random.Random(f"reader/{self.seed}/{canonical(name)}")
        return rng.randint(0, 15), rng.randint(5, 80)

    def top(self) -> list[NetUser]:
        return sorted(self.users, key=lambda u: (-u.followers, u.name))[: self.eff["n_top"]]

    def feed(self) -> list[NetPost]:
        posts = [p for u in self.users for p in u.posts]
        return random.Random(f"feed/{self.seed}").sample(posts, min(self.eff.get("feed_size", 12), len(posts)))

    # -- data checks -------------------------------------------------------
    def validate(self) -> None:
        """Shape of the users file required by the scenario; raises ConfigError."""
        eff = self.eff
        if len(self.users) != eff["n_users"]:
            raise ConfigError(f"users file has {len(self.users)} users, n_users={eff['n_users']}")
        if len(self._by_name) != len(self.users):
            raise ConfigError("duplicate user names in the users file")
        top = self.top()
        rest = [u for u in self.users if u not in top]
        lo, hi = TOP_FOLLOWERS
        if any(not lo <= u.followers <= hi for u in top):
            raise ConfigError(f"top users must have {lo}-{hi} followers")
        mirror = [u for u in top if 0.8 <= u.following / u.followers <= 1.2 and 1 <= len(u.posts) <= 3]
        creators = [u for u in top if u.following <= 0.5 * u.followers and len(u.posts) >= 8]
        if len(mirror) + len(creators) != len(top) or len(creators) < 1 or len(mirror) < 1:
            raise ConfigError("top users must be either following≈followers with 1-3 posts or few followings "
                              "with 8+ posts, both kinds present")
        lo, hi = REST_FOLLOWERS
        for u in rest:
            if not lo <= u.followers <= hi or u.following >= u.followers or not 3 <= len(u.posts) <= 8:
                raise ConfigError(f"user {u.name}: other users need {lo}-{hi} followers, fewer followings "
                                  "and 3-8 posts")
