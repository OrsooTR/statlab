"""SQLite persistence layer.

A single connection factory with WAL mode; writes are serialised through a module
lock (SQLite allows many readers / one writer). All analytical payloads are stored
as JSON columns — they are documents, not relational data.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import DB_PATH, ensure_dirs

_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS ct_simulations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  name TEXT NOT NULL,
  strategy TEXT NOT NULL,
  params_json TEXT NOT NULL,
  spins INTEGER NOT NULL,
  runs INTEGER NOT NULL,
  bankroll REAL NOT NULL,
  results_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fb_matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  league TEXT NOT NULL,
  season TEXT NOT NULL,
  date TEXT NOT NULL,
  home TEXT NOT NULL,
  away TEXT NOT NULL,
  fthg INTEGER, ftag INTEGER, ftr TEXT,
  hthg INTEGER, htag INTEGER,
  hs INTEGER, as_ INTEGER, hst INTEGER, ast INTEGER,
  hc INTEGER, ac INTEGER, hy INTEGER, ay INTEGER, hr INTEGER, ar INTEGER,
  hf INTEGER, af INTEGER,
  b365h REAL, b365d REAL, b365a REAL,
  b365ch REAL, b365cd REAL, b365ca REAL,
  avg_over25 REAL, avg_under25 REAL,
  extra_json TEXT,
  UNIQUE(league, date, home, away)
);
CREATE INDEX IF NOT EXISTS idx_fb_matches_league_season ON fb_matches(league, season);
CREATE INDEX IF NOT EXISTS idx_fb_matches_date ON fb_matches(date);
CREATE TABLE IF NOT EXISTS fb_predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  league TEXT NOT NULL,
  home TEXT NOT NULL,
  away TEXT NOT NULL,
  match_date TEXT,
  prediction_json TEXT NOT NULL,
  actual_result TEXT
);
CREATE TABLE IF NOT EXISTS fb_backtests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  league TEXT NOT NULL,
  seasons TEXT NOT NULL,
  metrics_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fb_slips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  slip_json TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _write_lock, _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def read_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_conn() -> Iterator[sqlite3.Connection]:
    with _write_lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False, default=_json_default)


def _json_default(o: Any):
    # NumPy scalars arrive from the engines; coerce them transparently.
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"not JSON serialisable: {type(o)}")
