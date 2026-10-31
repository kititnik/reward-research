"""Report per matrix cell and per chain, as Markdown and CSV."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import mean, median

from . import db
from .config import cell_label, data_dir
from .grader import MILESTONES

QUOTE_CHARS = 400


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return mean(xs) if xs else None


def _per_k(chains: list[dict], key) -> list:
    n_k = max((len(c["episodes"]) for c in chains), default=0)
    return [_mean([key(c["episodes"][k]) for c in chains if len(c["episodes"]) > k]) for k in range(n_k)]


def collect(run_id: str) -> list[dict]:
    chains = []
    for c in db.chains_of(run_id):
        eps = db.episodes_of(c["chain_id"], "done")
        for e in eps:
            e["hack_flags"] = json.loads(e["hack_flags_json"])
            e["milestones"] = json.loads(e["milestones_json"])
            e["metrics"] = json.loads(e["metrics_json"]) if e.get("metrics_json") else None
        summary = json.loads(c["summary_json"]) if c["summary_json"] else {}
        chains.append({**c, "config": json.loads(c["config_json"]), "episodes": eps, "summary": summary})
    return chains


def cell_rows(chains: list[dict]) -> list[dict]:
    groups: dict = {}
    for c in chains:
        groups.setdefault((c["cell_index"], c["policy"] or ""), []).append(c)
    rows = []
    for (ci, policy), cs in sorted(groups.items()):
        eff = cs[0]["config"]
        eps = [e for c in cs for e in c["episodes"]]
        row = {"cell": ci, "label": cell_label(eff), "bonus_per_word": eff["bonus_per_word"],
               "close_threshold": eff.get("close_threshold"), "policy": policy, "n_chains": len(cs),
               "n_runs": len(eps)}
        for m in MILESTONES:
            firsts = [c["summary"].get(f"first_run_{m}") for c in cs]
            hit = [f for f in firsts if f is not None]
            ci_ = wilson(len(hit), len(cs))
            row[f"{m}_share"] = len(hit) / len(cs) if cs else None
            row[f"{m}_ci"] = f"[{ci_[0]:.2f}, {ci_[1]:.2f}]" if ci_ else ""
            row[f"{m}_median_first_run"] = median(hit) if hit else None
        row["likes_by_k"] = _per_k(cs, lambda e: e["total_likes"])
        row["violation_rate_by_k"] = _per_k(cs, lambda e: e["hack_flags"]["violation_rate"])
        row["mean_proxy"] = _mean([e["proxy"] for e in eps])
        row["mean_true"] = _mean([e["true_reward"] for e in eps])
        row["mean_gap"] = _mean([e["gap"] for e in eps])
        row["closed_share"] = _mean([e["account_closed"] for e in eps])
        rows.append(row)
    return rows


def _first_violation(c: dict) -> int | None:
    s = c["summary"]
    return s.get("first_run_M2") or s.get("first_run_M1") or s.get("first_run_M0")


def chain_rows(chains: list[dict]) -> list[dict]:
    rows = []
    for c in chains:
        fv = _first_violation(c)
        quotes = []
        if fv:
            for e in c["episodes"]:
                if fv - 1 <= e["k"] <= fv + 1 and e["notes"]:
                    text = e["notes"].replace("\n", " ")
                    quotes.append(f"run {e['k']}: {text[:QUOTE_CHARS]}{'…' if len(text) > QUOTE_CHARS else ''}")
        rows.append({
            "chain_id": c["chain_id"], "cell": c["cell_index"], "label": cell_label(c["config"]),
            "policy": c["policy"] or "", "seed": c["seed"], "status": c["status"], "error": c["error"] or "",
            **{f"first_run_{m}": c["summary"].get(f"first_run_{m}") for m in MILESTONES},
            "outcomes": " ".join(e["outcome"] for e in c["episodes"]),
            "likes": " ".join(str(e["total_likes"]) for e in c["episodes"]),
            "notes_quotes": quotes,
        })
    return rows


def episode_rows(chains: list[dict]) -> list[dict]:
    rows = []
    for c in chains:
        for e in db.episodes_of(c["chain_id"]):
            flags = json.loads(e["hack_flags_json"]) if e["hack_flags_json"] else {}
            ms = json.loads(e["milestones_json"]) if e["milestones_json"] else {}
            rows.append({
                "episode_id": e["episode_id"], "cell": c["cell_index"], "policy": c["policy"] or "",
                "seed": c["seed"], "k": e["k"], "status": e["status"], "outcome": e["outcome"],
                "total_likes": e["total_likes"], "account_closed": e["account_closed"], "proxy": e["proxy"],
                "true": e["true_reward"], "gap": e["gap"], "turns": e["turns"], "cost_usd": e["cost_usd"],
                **{m: int(bool(ms.get(m))) for m in MILESTONES},
                **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in flags.items()},
            })
    return rows


def _fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, list):
        return " ".join(_fmt(x) for x in v)
    return str(v)


def _table(rows: list[dict], cols: list[str]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |" for r in rows]
    return out


def markdown(run_id: str, cells: list[dict], chains: list[dict], eps: list[dict]) -> str:
    run = db.get_run(run_id)
    lines = [f"# Report {run_id}", "",
             f"kind: {run['kind']} · status: {run['status']} · claude: {run['claude_version'] or '–'} · "
             f"git: {run['git_hash'] or '–'} · parent: {run['parent'] or '–'}", "", "## Cells", ""]
    has_policy = any(r["policy"] for r in cells)
    cols = ["cell", "label"] + (["policy"] if has_policy else []) + ["n_chains", "n_runs"]
    for m in MILESTONES:
        cols += [f"{m}_share", f"{m}_ci", f"{m}_median_first_run"]
    lines += _table(cells, cols)
    lines += ["", "### Per run k (means over chains)", ""]
    lines += _table(cells, ["cell", "label"] + (["policy"] if has_policy else []) +
                    ["likes_by_k", "violation_rate_by_k", "mean_proxy", "mean_true", "mean_gap", "closed_share"])
    lines += ["", "## Chains", ""]
    lines += _table(chains, ["chain_id", "label", *(["policy"] if has_policy else []), "status",
                             "first_run_M0", "first_run_M1", "first_run_M2", "first_run_M3", "outcomes", "likes"])
    quoted = [c for c in chains if c["notes_quotes"]]
    if quoted:
        lines += ["", "## Notes around the first violation", ""]
        for c in quoted:
            lines.append(f"**{c['chain_id']}** ({c['label']})")
            lines += [f"> {q}" for q in c["notes_quotes"]]
            lines.append("")
    errors = [c for c in chains if c["status"] not in ("done",)]
    if errors:
        lines += ["", "## Unfinished chains", ""]
        lines += [f"- {c['chain_id']}: {c['status']} {c['error']}" for c in errors]
    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: (_fmt(v) if isinstance(v, list) else v) for k, v in r.items()})


def report(run_id: str, to_csv: bool = False, to_md: bool = False) -> str:
    from .scenarios import of
    run = db.get_run(run_id)
    if not run:
        raise SystemExit(f"no run {run_id}")
    sc = of(json.loads(run["config_json"]))
    chains = collect(run_id)
    cells, crow, erow = sc.report_rows(chains)
    md = sc.markdown(run_id, cells, crow, erow)
    out_dir = data_dir() / "reports" / run_id
    if to_csv or to_md:
        out_dir.mkdir(parents=True, exist_ok=True)
    if to_md:
        (out_dir / "report.md").write_text(md, encoding="utf-8")
    if to_csv:
        write_csv(out_dir / "cells.csv", cells)
        write_csv(out_dir / "chains.csv", crow)
        write_csv(out_dir / "episodes.csv", erow)
    return md
