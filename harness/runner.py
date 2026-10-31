"""Runs, chains and episodes: create, execute, resume, rerun, replay."""
from __future__ import annotations

import copy
import json
import secrets
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import db, scenarios
from .backend_base import BackendResult, EpisodeContext
from .backend_scripted import ScriptedBackend
from .config import PROJECT_ROOT, ConfigError, cell_configs, data_dir, prompt_hashes, scenario_of


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def git_hash() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def claude_version() -> str | None:
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# -- creation ----------------------------------------------------------------

def pilot_config(cfg: dict) -> dict:
    return scenarios.of(cfg).pilot_config(copy.deepcopy(cfg))


def create_run(cfg: dict, kind: str, overrides: dict | None = None, parent: str | None = None,
               policies: list | None = None, seeds: list | None = None) -> str:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    db.insert_run({
        "run_id": run_id, "created_at": now(), "kind": kind, "parent": parent, "config": cfg,
        "overrides": overrides, "prompt_hashes": prompt_hashes(scenario_of(cfg)), "scenario": scenario_of(cfg),
        "claude_version": None if kind == "dry-run" else claude_version(),
        "git_hash": git_hash(), "status": "pending",
    })
    for ci, (cell, eff) in enumerate(cell_configs(cfg, overrides)):
        chain_seeds = seeds if seeds is not None else list(range(eff["n_chains"]))
        for policy in policies or [None]:
            for seed in chain_seeds:
                chain_id = f"{run_id}-c{ci}" + (f"-{policy}" if policy else "") + f"-s{seed}"
                db.insert_chain({"chain_id": chain_id, "run_id": run_id, "cell_index": ci, "cell": cell,
                                 "seed": seed, "policy": policy, "config": eff, "status": "pending"})
    return run_id


# -- execution ---------------------------------------------------------------

def make_backend(policy: str | None):
    if policy:
        return ScriptedBackend()
    from .backend_cli import ClaudeCodeCLI
    return ClaudeCodeCLI()


def execute_run(run_id: str, chain_ids: list[str] | None = None) -> None:
    run = db.get_run(run_id)
    chains = [c for c in db.chains_of(run_id) if chain_ids is None or c["chain_id"] in chain_ids]
    parallelism = json.loads(run["config_json"]).get("parallelism", 1) or 1
    db.update("runs", "run_id", run_id, status="running")
    log(f"run {run_id}: {len(chains)} chains, parallelism {parallelism}")
    if parallelism > 1:
        with ThreadPoolExecutor(parallelism) as ex:
            list(ex.map(lambda c: run_chain(c["chain_id"]), chains))
    else:
        for c in chains:
            run_chain(c["chain_id"])
    statuses = {c["status"] for c in db.chains_of(run_id)}
    db.update("runs", "run_id", run_id, status="done" if statuses == {"done"} else "incomplete")
    log(f"run {run_id}: chain statuses {sorted(statuses)}")


def episode_dir(chain: dict, k: int) -> Path:
    return data_dir() / "episodes" / chain["run_id"] / chain["chain_id"] / str(k)


def past_runs(chain_id: str, sc: scenarios.Scenario) -> list[dict]:
    out = []
    for ep in db.episodes_of(chain_id, "done"):
        state = json.loads((Path(ep["dir"]) / "state.json").read_text(encoding="utf-8"))
        out.append(sc.past_run(ep, state))
    return out


def run_chain(chain_id: str) -> None:
    chain = db.get_chain(chain_id)
    eff = json.loads(chain["config_json"])
    try:
        scenarios.of(eff).check_chain(eff, chain["seed"])
    except ConfigError as e:
        db.update("chains", "chain_id", chain_id, status="error", error=str(e))
        log(f"{chain_id}: config error: {e}")
        return
    backend = make_backend(chain["policy"])
    done = db.episodes_of(chain_id, "done")
    db.update("chains", "chain_id", chain_id, status="running", error=None)
    for k in range(len(done) + 1, eff["n_iterations"] + 1):
        ep = run_episode(chain, k, eff, backend)
        if ep["status"] != "done":
            db.update("chains", "chain_id", chain_id, status=ep["status"], error=ep.get("error"))
            log(f"{chain_id}: stopped at run {k}: {ep['status']}: {ep.get('error')}")
            return
    db.update("chains", "chain_id", chain_id, status="done")


def _move_aside(d: Path, tag: str) -> None:
    if d.exists():
        d.rename(d.with_name(f"{d.name}.{tag}-{datetime.now().strftime('%Y%m%d%H%M%S')}"))


def run_episode(chain: dict, k: int, eff: dict, backend) -> dict:
    d = episode_dir(chain, k)
    _move_aside(d, "stale")  # leftovers of an interrupted attempt are kept, never overwritten
    sc = scenarios.of(eff)
    past = past_runs(chain["chain_id"], sc)
    ctx = EpisodeContext(chain_id=chain["chain_id"], k=k, seed=chain["seed"], eff=eff, episode_dir=d,
                         system_prompt=sc.system_prompt(eff), first_message=sc.first_message(k, past),
                         policy=chain["policy"])
    started = now()
    retries = 0
    while True:
        d.mkdir(parents=True, exist_ok=True)
        (d / "system_prompt.txt").write_text(ctx.system_prompt, encoding="utf-8")
        (d / "first_message.txt").write_text(ctx.first_message, encoding="utf-8")
        try:
            res = backend.run_episode(ctx)
        except Exception as e:  # harness bug: record and stop the chain
            res = BackendResult(status="error", error=f"{type(e).__name__}: {e}")
        if res.status != "rate_limited":
            break
        retries += 1
        bk = eff["backend"]
        _move_aside(d, "ratelimited")
        if retries > bk.get("rate_limit_max_retries", 8):
            res = BackendResult(status="error", error="rate limit: retries exhausted")
            d.mkdir(parents=True, exist_ok=True)
            break
        log(f"{chain['chain_id']} run {k}: rate limited, pausing {bk['rate_limit_pause_s']}s (retry {retries})")
        time.sleep(bk["rate_limit_pause_s"])
    graded = sc.grade_dir(eff, d, res.status)
    state_notes = None
    if (d / "state.json").exists():
        state_notes = json.loads((d / "state.json").read_text(encoding="utf-8")).get("notes")
    row = {
        "episode_id": f"{chain['chain_id']}/{k}", "chain_id": chain["chain_id"], "k": k, "dir": str(d),
        "status": res.status, "model": None if chain["policy"] else (res.extra.get("init_model") or eff["model"]),
        "session_id": res.session_id,
        "turns": res.turns, "usage": res.usage, "cost_usd": res.cost_usd,
        "is_error": None if res.is_error is None else int(res.is_error), "result_subtype": res.result_subtype,
        "error": res.error, "started_at": started, "finished_at": now(), "notes": state_notes,
        **sc.columns(graded),
    }
    db.upsert_episode(row)
    refresh_chain_summary(chain["chain_id"])
    log(f"{chain['chain_id']} run {k}: {res.status} {sc.summary(graded)} turns={res.turns}")
    return row


def refresh_chain_summary(chain_id: str) -> None:
    sc = scenarios.of(json.loads(db.get_chain(chain_id)["config_json"]))
    eps = [sc.chain_input(e) for e in db.episodes_of(chain_id, "done")]
    db.update("chains", "chain_id", chain_id, summary_json=json.dumps(sc.grade_chain(eps)))


# -- commands ----------------------------------------------------------------

def resume(target: str) -> None:
    chain = db.get_chain(target)
    if chain:
        run_chain(target)
        return
    run = db.get_run(target)
    if not run:
        raise SystemExit(f"no chain or run {target}")
    pending = [c["chain_id"] for c in db.chains_of(target) if c["status"] != "done"]
    execute_run(target, pending)


def rerun(target: str, overrides: dict) -> str:
    chain = db.get_chain(target)
    if chain:
        run = db.get_run(chain["run_id"])
        cfg = json.loads(run["config_json"])
        cfg["cells"] = [json.loads(chain["cell_json"])]
        seeds = None if "n_chains" in overrides else [chain["seed"]]
        policies = [chain["policy"]] if chain["policy"] else None
    else:
        run = db.get_run(target)
        if not run:
            raise SystemExit(f"no chain or run {target}")
        cfg = json.loads(run["config_json"])
        seeds = None
        policies = list(scenarios.of(cfg).policies) if run["kind"] == "dry-run" else None
    # the run's own overrides stay in effect, new ones go on top
    merged = {**json.loads(run["overrides_json"] or "{}"), **overrides}
    new_id = create_run(cfg, run["kind"], merged, parent=target, policies=policies, seeds=seeds)
    log(f"rerun of {target} -> new run {new_id}")
    execute_run(new_id)
    return new_id


def replay(target: str) -> list[dict]:
    """Regrade saved episodes (dir path, episode id, chain id or run id) without calling the model."""
    p = Path(target)
    if p.exists():
        eps = db.fetch("SELECT * FROM episodes WHERE dir = ?", (str(p.resolve()),))
    elif db.get_episode(target):
        eps = [db.get_episode(target)]
    elif db.get_chain(target):
        eps = db.episodes_of(target)
    elif db.get_run(target):
        eps = db.fetch("SELECT e.* FROM episodes e JOIN chains c ON e.chain_id = c.chain_id WHERE c.run_id = ? "
                       "ORDER BY e.chain_id, e.k", (target,))
    else:
        raise SystemExit(f"nothing to replay for {target}")
    changed = []
    for ep in eps:
        eff = json.loads(db.get_chain(ep["chain_id"])["config_json"])
        if not (Path(ep["dir"]) / "state.json").exists():
            continue
        sc = scenarios.of(eff)
        cols = sc.columns(sc.grade_dir(eff, ep["dir"], ep["status"]))
        old = (ep["outcome"], ep["milestones_json"])
        db.upsert_episode({**{k: v for k, v in ep.items() if not k.endswith("_json")},
                           "usage": json.loads(ep["usage_json"]) if ep["usage_json"] else None, **cols})
        if old != (cols["outcome"], json.dumps(cols["milestones"], ensure_ascii=False)):
            changed.append({"episode_id": ep["episode_id"], "old": old[0], "new": cols["outcome"]})
    for chain_id in {ep["chain_id"] for ep in eps}:
        refresh_chain_summary(chain_id)
    log(f"replayed {len(eps)} episodes, {len(changed)} changed outcome/milestones")
    return changed

