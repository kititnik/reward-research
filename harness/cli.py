"""python -m harness {run,resume,rerun,report,replay}"""
from __future__ import annotations

import argparse

from . import runner, scenarios
from .config import load_config, parse_set
from .report import report


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="start a new run from a config")
    p.add_argument("config")
    p.add_argument("--dry-run", action="store_true", help="scripted policies instead of claude")
    p.add_argument("--pilot", action="store_true",
                   help="social: 1 chain x 5 runs, bonus 30 and no threshold; followers: 1 chain x 4 runs, first cell")
    p.add_argument("--set", nargs="*", default=[], metavar="KEY=VALUE")

    p = sub.add_parser("resume", help="continue a chain (or all unfinished chains of a run)")
    p.add_argument("target")

    p = sub.add_parser("rerun", help="new chains from a saved chain/run config with overrides")
    p.add_argument("target")
    p.add_argument("--set", nargs="*", default=[], metavar="KEY=VALUE")

    p = sub.add_parser("report")
    p.add_argument("run_id")
    p.add_argument("--csv", action="store_true")
    p.add_argument("--md", action="store_true")

    p = sub.add_parser("replay", help="regrade saved logs (episode dir/id, chain id or run id)")
    p.add_argument("target")

    a = ap.parse_args(argv)
    if a.cmd == "run":
        cfg = load_config(a.config)
        if a.pilot:
            cfg = runner.pilot_config(cfg)
        kind = "dry-run" if a.dry_run else ("pilot" if a.pilot else "real")
        overrides = parse_set(a.set) or None
        policies = list(scenarios.of(cfg).policies) if a.dry_run else None
        run_id = runner.create_run(cfg, kind, overrides, policies=policies)
        runner.execute_run(run_id)
        print(report(run_id, to_csv=True, to_md=True))
        print(f"run_id: {run_id}")
    elif a.cmd == "resume":
        runner.resume(a.target)
    elif a.cmd == "rerun":
        run_id = runner.rerun(a.target, parse_set(a.set))
        print(report(run_id, to_csv=True, to_md=True))
        print(f"run_id: {run_id}")
    elif a.cmd == "report":
        print(report(a.run_id, to_csv=a.csv, to_md=a.md))
    elif a.cmd == "replay":
        for ch in runner.replay(a.target):
            print(f"{ch['episode_id']}: {ch['old']} -> {ch['new']}")
