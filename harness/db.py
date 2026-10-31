"""SQLite storage: runs, chains, episodes."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,              -- real | dry-run | pilot
    parent TEXT,                     -- source chain/run for rerun
    config_json TEXT NOT NULL,       -- full config incl. cells and overrides
    overrides_json TEXT,
    prompt_hashes_json TEXT NOT NULL,
    claude_version TEXT,
    git_hash TEXT,
    status TEXT NOT NULL,
    scenario TEXT NOT NULL DEFAULT 'social'
);
CREATE TABLE IF NOT EXISTS chains (
    chain_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    cell_index INTEGER NOT NULL,
    cell_json TEXT NOT NULL,
    seed INTEGER NOT NULL,
    policy TEXT,                     -- scripted policy for dry-run, NULL for the model
    config_json TEXT NOT NULL,       -- effective config of this chain
    status TEXT NOT NULL,            -- pending | running | done | invalid | error
    error TEXT,
    summary_json TEXT                -- grade_chain() over completed runs
);
CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,     -- <chain_id>/<k>
    chain_id TEXT NOT NULL REFERENCES chains(chain_id),
    k INTEGER NOT NULL,
    dir TEXT NOT NULL,
    status TEXT NOT NULL,            -- done | invalid | error
    model TEXT,
    session_id TEXT,
    turns INTEGER,
    usage_json TEXT,
    cost_usd REAL,
    is_error INTEGER,
    result_subtype TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    total_likes INTEGER,
    account_closed INTEGER,
    proxy REAL,
    true_reward REAL,
    gap REAL,
    hack_flags_json TEXT,
    milestones_json TEXT,
    outcome TEXT,
    notes TEXT,
    metrics_json TEXT                -- scenario-specific counters (followers: sources, follows, quality)
);
"""

# columns added after the first results were stored: (table, column, declaration)
MIGRATIONS = (
    ("runs", "scenario", "TEXT NOT NULL DEFAULT 'social'"),
    ("episodes", "metrics_json", "TEXT"),
)

_lock = threading.Lock()


def db_path() -> Path:
    return data_dir() / "results.db"


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(SCHEMA)
            _migrate(conn)
            yield conn
            conn.commit()
        finally:
            conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _dumps(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


def insert_run(row: dict) -> None:
    with connect() as c:
        c.execute("INSERT INTO runs (run_id, created_at, kind, parent, config_json, overrides_json, prompt_hashes_json, "
                  "claude_version, git_hash, status, scenario) VALUES (:run_id, :created_at, :kind, :parent, "
                  ":config_json, :overrides_json, :prompt_hashes_json, :claude_version, :git_hash, :status, :scenario)",
                  {"scenario": "social", **row, "config_json": _dumps(row["config"]),
                   "overrides_json": _dumps(row.get("overrides")), "prompt_hashes_json": _dumps(row["prompt_hashes"])})


def insert_chain(row: dict) -> None:
    with connect() as c:
        c.execute("INSERT INTO chains VALUES (:chain_id, :run_id, :cell_index, :cell_json, :seed, :policy, "
                  ":config_json, :status, NULL, NULL)",
                  {**row, "cell_json": _dumps(row["cell"]), "config_json": _dumps(row["config"])})


def update(table: str, key: str, value: str, **fields) -> None:
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE {table} SET {cols} WHERE {key} = ?", [*fields.values(), value])


def upsert_episode(row: dict) -> None:
    row = dict(row)
    for k in ("usage", "hack_flags", "milestones", "metrics"):
        row[f"{k}_json"] = _dumps(row.pop(k, None))
    cols = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    with connect() as c:
        c.execute(f"INSERT OR REPLACE INTO episodes ({cols}) VALUES ({marks})", row)


def fetch(sql: str, params=()) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def get_run(run_id: str) -> dict | None:
    rows = fetch("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    return rows[0] if rows else None


def get_chain(chain_id: str) -> dict | None:
    rows = fetch("SELECT * FROM chains WHERE chain_id = ?", (chain_id,))
    return rows[0] if rows else None


def chains_of(run_id: str) -> list[dict]:
    return fetch("SELECT * FROM chains WHERE run_id = ? ORDER BY cell_index, policy, seed", (run_id,))


def episodes_of(chain_id: str, status: str | None = None) -> list[dict]:
    if status:
        return fetch("SELECT * FROM episodes WHERE chain_id = ? AND status = ? ORDER BY k", (chain_id, status))
    return fetch("SELECT * FROM episodes WHERE chain_id = ? ORDER BY k", (chain_id,))


def get_episode(episode_id: str) -> dict | None:
    rows = fetch("SELECT * FROM episodes WHERE episode_id = ?", (episode_id,))
    return rows[0] if rows else None
