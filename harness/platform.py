"""Platform core shared by the MCP server and the scripted dry-run policies.

State goes to <episode>/state.json, every tool call to <episode>/events.jsonl.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import pair_of
from .detector import FORMS_WITH_BONUS, detector_from_pair
from .likes import agent_post_key, expected_likes, likes
from .prompts import fragments
from .users import Community

TOOLS = ("post", "preview_post", "read_feed", "get_user_posts", "leaderboard", "my_posts", "save_notes")
AGENT_POST_ID_BASE = 1000


class ToolFailure(Exception):
    """A tool call that the agent sees as an error."""


def platform_filter(text: str) -> tuple[bool, str]:
    """Extension point for a platform check on posts. Currently allows everything."""
    return True, ""


class Platform:
    def __init__(self, eff: dict, seed: int, run: int, episode_dir: str | Path):
        self.eff = eff
        self.seed = seed
        self.run = run
        self.dir = Path(episode_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.detector = detector_from_pair(pair_of(eff))
        self.community = Community(eff, seed, self.detector)
        self.r = fragments()["responses"]
        self.state_path = self.dir / "state.json"
        self.events_path = self.dir / "events.jsonl"
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            self.state = {"seed": seed, "run": run, "posts": [], "previews": [], "notes": None}
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

    def _hits(self, text: str) -> list[dict]:
        return [h.to_dict() for h in self.detector.find(text)]

    @staticmethod
    def _n_words(hits: list[dict]) -> int:
        return sum(1 for h in hits if h["form"] in FORMS_WITH_BONUS)

    def _you(self) -> str:
        return self.r["you"]

    def _user_listing(self, name: str, posts: list[tuple[int, str, int]]) -> str:
        lines = [self.r["user_header"].format(user=name)]
        lines += [self.r["user_post_line"].format(id=i, text=t, likes=n) for i, t, n in posts] or [self.r["user_no_posts"]]
        lines.append(self.r["user_total"].format(total=sum(n for _, _, n in posts)))
        return "\n".join(lines)

    @property
    def total_likes(self) -> int:
        return sum(p["likes"] for p in self.state["posts"])

    # -- tools ----------------------------------------------------------
    def _post(self, text: str) -> str:
        eff = self.eff
        if len(self.state["posts"]) >= eff["max_posts"]:
            raise ToolFailure(self.r["post_limit"])
        if not text.strip():
            raise ToolFailure(self.r["post_empty"])
        if len(text) > eff["max_post_chars"]:
            raise ToolFailure(self.r["post_too_long"].format(n=len(text), max=eff["max_post_chars"]))
        allowed, reason = platform_filter(text)
        if not allowed:
            raise ToolFailure(self.r["post_rejected"].format(reason=reason))
        index = len(self.state["posts"])
        hits = self._hits(text)
        n_words = self._n_words(hits)
        n_likes = likes(eff["base_likes"], eff["bonus_per_word"], n_words,
                        agent_post_key(self.seed, self.run, index, text), eff["noise_sigma"])
        post_id = AGENT_POST_ID_BASE + index + 1
        self.state["posts"].append({"id": post_id, "text": text, "likes": n_likes, "n_words": n_words,
                                    "hits": hits, "t": time.time()})
        self._save()
        return self.r["posted"].format(id=post_id)

    def _preview_post(self, text: str) -> str:
        eff = self.eff
        if len(self.state["previews"]) >= eff["max_previews"]:
            raise ToolFailure(self.r["preview_limit"])
        hits = self._hits(text)
        expected = expected_likes(eff["base_likes"], eff["bonus_per_word"], self._n_words(hits))
        self.state["previews"].append({"text": text, "expected": expected, "hits": hits, "t": time.time()})
        self._save()
        left = eff["max_previews"] - len(self.state["previews"])
        return self.r["preview"].format(likes=expected, left=left)

    def _read_feed(self) -> str:
        return "\n".join(self.r["feed_line"].format(id=p.id, user=p.user, text=p.text, likes=p.likes)
                         for p in self.community.feed())

    def _get_user_posts(self, user: str) -> str:
        if user.lstrip("@").lower() == self._you().lower():
            return self._my_posts()
        u = self.community.get(user)
        if u is None:
            raise ToolFailure(self.r["unknown_user"].format(user=user))
        return self._user_listing(u.name, [(p.id, p.text, p.likes) for p in u.posts])

    def _leaderboard(self) -> str:
        rows = [(u.name, u.total) for u in self.community.users] + [(self._you(), self.total_likes)]
        rows.sort(key=lambda r: (-r[1], r[0]))
        return "\n".join(self.r["leaderboard_line"].format(place=i, user=n, likes=s) for i, (n, s) in enumerate(rows, 1))

    def _my_posts(self) -> str:
        return self._user_listing(self._you(), [(p["id"], p["text"], p["likes"]) for p in self.state["posts"]])

    def _save_notes(self, text: str) -> str:
        self.state["notes"] = text[: self.eff["notes_max_chars"]]
        self.state["notes_truncated"] = len(text) > self.eff["notes_max_chars"]
        self._save()
        return self.r["notes_saved"]
