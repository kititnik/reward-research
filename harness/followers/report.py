"""Report rows and Markdown for `followers` (shared helpers come from harness.report)."""
from __future__ import annotations

import json
from collections import Counter
from statistics import median

from .. import db
from ..report import QUOTE_CHARS, _mean, _per_k, _table, wilson
from .grader import MILESTONES

OUTCOMES = ("honest", "mixed", "follow_dominant", "no_posts", "invalid", "error")
PER_K = ("followers_total", "followers_organic", "followers_reciprocal", "n_posts", "n_quality_posts", "n_follows")


def cell_label(eff: dict) -> str:
    return f"reciprocal={str(eff['reciprocal']).lower()} follow_limit={eff.get('follow_limit')}"


def cell_rows(chains: list[dict]) -> list[dict]:
    groups: dict = {}
    for c in chains:
        groups.setdefault((c["cell_index"], c["policy"] or ""), []).append(c)
    rows = []
    for (ci, policy), cs in sorted(groups.items()):
        eff = cs[0]["config"]
        eps = [e for c in cs for e in c["episodes"]]
        row = {"cell": ci, "label": cell_label(eff), "reciprocal": eff["reciprocal"],
               "follow_limit": eff.get("follow_limit"), "policy": policy, "n_chains": len(cs), "n_runs": len(eps)}
        for m in MILESTONES:
            hit = [f for f in (c["summary"].get(f"first_run_{m}") for c in cs) if f is not None]
            ci_ = wilson(len(hit), len(cs))
            row[f"{m}_share"] = len(hit) / len(cs) if cs else None
            row[f"{m}_ci"] = f"[{ci_[0]:.2f}, {ci_[1]:.2f}]" if ci_ else ""
            row[f"{m}_median_first_run"] = median(hit) if hit else None
        for key in PER_K:
            row[f"{key}_by_k"] = _per_k(cs, lambda e, key=key: e["metrics"][key])
        row["mean_proxy"] = _mean([e["proxy"] for e in eps])
        row["mean_true"] = _mean([e["true_reward"] for e in eps])
        row["mean_gap"] = _mean([e["gap"] for e in eps])
        counts = Counter(e["outcome"] for e in eps)
        row["outcomes"] = " ".join(f"{o}:{counts[o] / len(eps):.2f}" for o in OUTCOMES if counts[o]) if eps else ""
        rows.append(row)
    return rows


def chain_rows(chains: list[dict]) -> list[dict]:
    rows = []
    for c in chains:
        first = c["summary"].get("first_run_M0")
        quotes = []
        if first:
            for e in c["episodes"]:
                if first - 1 <= e["k"] <= first + 1 and e["notes"]:
                    text = e["notes"].replace("\n", " ")
                    quotes.append(f"run {e['k']}: {text[:QUOTE_CHARS]}{'…' if len(text) > QUOTE_CHARS else ''}")
        rows.append({
            "chain_id": c["chain_id"], "cell": c["cell_index"], "label": cell_label(c["config"]),
            "policy": c["policy"] or "", "seed": c["seed"], "status": c["status"], "error": c["error"] or "",
            **{f"first_run_{m}": c["summary"].get(f"first_run_{m}") for m in MILESTONES},
            "outcomes": " ".join(e["outcome"] for e in c["episodes"]),
            "followers": " ".join(str(e["metrics"]["followers_total"]) for e in c["episodes"]),
            "organic/reciprocal": " ".join(f"{e['metrics']['followers_organic']}/{e['metrics']['followers_reciprocal']}"
                                           for e in c["episodes"]),
            "posts": " ".join(str(e["metrics"]["n_posts"]) for e in c["episodes"]),
            "follows": " ".join(str(e["metrics"]["n_follows"]) for e in c["episodes"]),
            "notes_quotes": quotes,
        })
    return rows


def episode_rows(chains: list[dict]) -> list[dict]:
    rows = []
    for c in chains:
        for e in db.episodes_of(c["chain_id"]):
            flags = json.loads(e["hack_flags_json"]) if e["hack_flags_json"] else {}
            flags.pop("post_quality", None)
            ms = json.loads(e["milestones_json"]) if e["milestones_json"] else {}
            metrics = json.loads(e["metrics_json"]) if e["metrics_json"] else {}
            rows.append({
                "episode_id": e["episode_id"], "cell": c["cell_index"], "policy": c["policy"] or "",
                "seed": c["seed"], "k": e["k"], "status": e["status"], "outcome": e["outcome"],
                **metrics, "proxy": e["proxy"], "true": e["true_reward"], "gap": e["gap"], "turns": e["turns"],
                "cost_usd": e["cost_usd"], **{m: int(bool(ms.get(m))) for m in MILESTONES},
                **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in flags.items()},
            })
    return rows


def markdown(run_id: str, cells: list[dict], chains: list[dict], eps: list[dict]) -> str:
    run = db.get_run(run_id)
    lines = [f"# Report {run_id} (followers)", "",
             f"kind: {run['kind']} · status: {run['status']} · claude: {run['claude_version'] or '–'} · "
             f"git: {run['git_hash'] or '–'} · parent: {run['parent'] or '–'}", "", "## Cells", ""]
    has_policy = any(r["policy"] for r in cells)
    pol = ["policy"] if has_policy else []
    cols = ["cell", "label", *pol, "n_chains", "n_runs"]
    for m in MILESTONES:
        cols += [f"{m}_share", f"{m}_ci", f"{m}_median_first_run"]
    lines += _table(cells, cols)
    lines += ["", "### Per run k (means over chains)", ""]
    lines += _table(cells, ["cell", "label", *pol, *[f"{k}_by_k" for k in PER_K]])
    lines += ["", "### Rewards and outcomes", ""]
    lines += _table(cells, ["cell", "label", *pol, "mean_proxy", "mean_true", "mean_gap", "outcomes"])
    lines += ["", "## Chains", ""]
    lines += _table(chains, ["chain_id", "label", *pol, "status", *[f"first_run_{m}" for m in MILESTONES],
                             "outcomes", "followers", "organic/reciprocal", "posts", "follows"])
    quoted = [c for c in chains if c["notes_quotes"]]
    if quoted:
        lines += ["", "## Notes around the first follow", ""]
        for c in quoted:
            lines.append(f"**{c['chain_id']}** ({c['label']})")
            lines += [f"> {q}" for q in c["notes_quotes"]]
            lines.append("")
    errors = [c for c in chains if c["status"] != "done"]
    if errors:
        lines += ["", "## Unfinished chains", ""]
        lines += [f"- {c['chain_id']}: {c['status']} {c['error']}" for c in errors]
    return "\n".join(lines) + "\n"
