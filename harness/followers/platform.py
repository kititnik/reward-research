"""Platform core of `followers`, shared by the MCP server `network` and the scripted policies.

State goes to <episode>/state.json, every tool call to <episode>/events.jsonl. Every follower is a
record {user, source, run, t, ...}; the source (organic | reciprocal) is never shown to the agent
except through the wording of notifications.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from .growth import organic_count, pick, post_key, post_likes
from .prompts import fragments
from .users import Network, canonical

TOOLS = ("post", "read_feed", "get_profile", "follow", "unfollow", "notifications", "my_stats", "leaderboard",
         "save_notes")
AGENT_POST_ID_BASE = 1000
RECENT_POSTS = 3


class ToolFailure(Exception):
    """A tool call that the agent sees as an error."""


class NetworkPlatform:
    def __init__(self, eff: dict, seed: int, run: int, episode_dir: str | Path):
        self.eff = eff
        self.seed = seed
        self.run = run
        self.dir = Path(episode_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.network = Network(eff, seed)
        self.r = fragments()["responses"]
        self.state_path = self.dir / "state.json"
        self.events_path = self.dir / "events.jsonl"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = {"seed": seed, "run": run, "posts": [], "following": [], "follow_log": [],
                          "followers": [], "removed_followers": [], "notifications": [], "notes": None}
            self._save()

    # -- infrastructure -------------------------------------------------
    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def _log(self, tool: str, args: dict, response: str, is_error: bool) -> None:
        rec = {"t": time.time(), "tool": tool, "args": args, "response": response, "is_error": is_error}
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def call(self, tool: str, **args) -> str:
        """Run a tool, log it, return the response; raises ToolFailure for agent-visible errors."""
        if tool not in TOOLS:
            raise ValueError(f"unknown tool {tool}")
        try:
            response = getattr(self, f"_{tool}")(**args)
        except ToolFailure as e:
            self._log(tool, args, str(e), True)
            raise
        self._log(tool, args, response, False)
        return response

    def _you(self) -> str:
        return self.r["you"]

    def _is_you(self, name: str) -> bool:
        return canonical(name) == canonical(self._you())

    def _notify(self, key: str, **kw) -> None:
        self.state["notifications"].append({"t": time.time(), "text": self.r[key].format(**kw)})

    def _resolve(self, name: str) -> str:
        """Display name of a main user or reader; ToolFailure if unknown."""
        u = self.network.get(name)
        if u is not None:
            return u.name
        if self.network.is_reader(name):
            return canonical(name)
        raise ToolFailure(self.r["unknown_user"].format(user=name))

    # -- derived values ---------------------------------------------------
    @property
    def follower_names(self) -> set[str]:
        return {f["user"] for f in self.state["followers"]}

    @property
    def total_likes(self) -> int:
        return sum(p["likes"] for p in self.state["posts"])

    @property
    def n_follow_actions(self) -> int:
        return sum(1 for e in self.state["follow_log"] if e["action"] == "follow")

    def user_counts(self, name: str) -> tuple[int, int]:
        """(followers, following) of another user including the agent's relations with them."""
        u = self.network.get(name)
        followers, following = (u.followers, u.following) if u else self.network.reader_profile(name)
        return followers + (name in self.state["following"]), following + (name in self.follower_names)

    # -- tools ----------------------------------------------------------
    def _post(self, text: str) -> str:
        eff = self.eff
        if len(self.state["posts"]) >= eff["max_posts"]:
            raise ToolFailure(self.r["post_limit"])
        if not text.strip():
            raise ToolFailure(self.r["post_empty"])
        if len(text) > eff["max_post_chars"]:
            raise ToolFailure(self.r["post_too_long"].format(n=len(text), max=eff["max_post_chars"]))
        index = len(self.state["posts"])
        key = post_key(self.seed, self.run, index, text)
        post_id = AGENT_POST_ID_BASE + index + 1
        now = time.time()
        organic = pick(self.network.readers, organic_count(eff, key), f"organic-users/{key}", self.follower_names)
        n_likes = post_likes(eff, key)
        likers = pick(self.network.readers, n_likes, f"likers/{key}", set())
        self.state["posts"].append({"id": post_id, "text": text, "likes": n_likes, "organic": organic, "t": now})
        events = [("n_followed_after_post", u) for u in organic] + [("n_liked", u) for u in likers]
        random.Random(f"order/{key}").shuffle(events)
        for user in organic:
            self.state["followers"].append({"user": user, "source": "organic", "run": self.run, "t": now,
                                            "post_id": post_id})
        for kind, user in events:
            self._notify(kind, user=user, id=post_id)
        self._save()
        return self.r["posted"].format(id=post_id)

    def _read_feed(self) -> str:
        return "\n".join(self.r["feed_line"].format(id=p.id, user=p.user, text=p.text, likes=p.likes)
                         for p in self.network.feed())

    def _profile(self, name: str, followers: int, following: int, posts: list[tuple[int, str, int]]) -> str:
        lines = [self.r["profile"].format(user=name, followers=followers, following=following, posts=len(posts))]
        recent = posts[-RECENT_POSTS:]
        lines.append(self.r["profile_recent"])
        lines += [self.r["profile_post_line"].format(id=i, text=t, likes=n) for i, t, n in recent] \
            or [self.r["profile_no_posts"]]
        return "\n".join(lines)

    def _get_profile(self, user: str) -> str:
        if self._is_you(user):
            return self._profile(self._you(), len(self.state["followers"]), len(self.state["following"]),
                                 [(p["id"], p["text"], p["likes"]) for p in self.state["posts"]])
        name = self._resolve(user)
        followers, following = self.user_counts(name)
        u = self.network.get(name)
        posts = [(p.id, p.text, p.likes) for p in u.posts] if u else []
        return self._profile(name, followers, following, posts)

    def _follow(self, user: str) -> str:
        if self._is_you(user):
            raise ToolFailure(self.r["follow_self"])
        name = self._resolve(user)
        if name in self.state["following"]:
            raise ToolFailure(self.r["already_following"].format(user=name))
        limit = self.eff.get("follow_limit")
        if limit is not None and self.n_follow_actions >= limit:
            raise ToolFailure(self.r["follow_limit"])
        now = time.time()
        self.state["following"].append(name)
        self.state["follow_log"].append({"action": "follow", "user": name, "t": now})
        if self.eff["reciprocal"] and name not in self.follower_names:
            self.state["followers"].append({"user": name, "source": "reciprocal", "run": self.run, "t": now})
            self._notify("n_followed_back", user=name)
        self._save()
        return self.r["followed"].format(user=name)

    def _unfollow(self, user: str) -> str:
        name = self._resolve(user) if not self._is_you(user) else user
        if name not in self.state["following"]:
            raise ToolFailure(self.r["not_following"].format(user=name))
        now = time.time()
        self.state["following"].remove(name)
        self.state["follow_log"].append({"action": "unfollow", "user": name, "t": now})
        back = [f for f in self.state["followers"] if f["user"] == name and f["source"] == "reciprocal"]
        for f in back:  # the follow-back caused by this follow goes away with it
            self.state["followers"].remove(f)
            self.state["removed_followers"].append({**f, "removed_t": now})
            self._notify("n_unfollowed", user=name)
        self._save()
        return self.r["unfollowed"].format(user=name)

    def _notifications(self) -> str:
        return "\n".join(n["text"] for n in self.state["notifications"]) or self.r["notifications_empty"]

    def _my_stats(self) -> str:
        return self.r["my_stats"].format(followers=len(self.state["followers"]),
                                         following=len(self.state["following"]),
                                         posts=len(self.state["posts"]), likes=self.total_likes)

    def _leaderboard(self) -> str:
        rows = [(u.name, *self.user_counts(u.name), len(u.posts)) for u in self.network.users]
        rows.append((self._you(), len(self.state["followers"]), len(self.state["following"]),
                     len(self.state["posts"])))
        rows.sort(key=lambda r: (-r[1], r[0]))
        return "\n".join(self.r["leaderboard_line"].format(place=i, user=n, followers=f, following=g, posts=p)
                         for i, (n, f, g, p) in enumerate(rows[: self.eff.get("leaderboard_size", 20)], 1))

    def _save_notes(self, text: str) -> str:
        self.state["notes"] = text[: self.eff["notes_max_chars"]]
        self.state["notes_truncated"] = len(text) > self.eff["notes_max_chars"]
        self._save()
        return self.r["notes_saved"]
