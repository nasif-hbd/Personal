"""Tiny SQLite storage layer. No ORM — the schema is small enough that one
buys nothing but indirection."""
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent / "agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    os TEXT NOT NULL,
    name TEXT NOT NULL,
    last_seen REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    result TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    next_run_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row


def init_db() -> None:
    with _lock:
        _conn.executescript(SCHEMA)
        _conn.commit()


def execute(query: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = _conn.execute(query, params)
        _conn.commit()
        return cur


def query(query_str: str, params: tuple = ()) -> list[dict]:
    with _lock:
        cur = _conn.execute(query_str, params)
        return [dict(row) for row in cur.fetchall()]


def query_one(query_str: str, params: tuple = ()) -> dict | None:
    rows = query(query_str, params)
    return rows[0] if rows else None
