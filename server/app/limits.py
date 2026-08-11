"""Spend controls.

Every Claude call made through this server is billed to the owner's account,
so usage is capped on three independent axes:

  * per minute, per visitor  — stops a tight scripted loop
  * per day,    per visitor  — stops one person burning the budget
  * per day,    globally     — the hard ceiling on what a day can ever cost

Counters live in SQLite so they survive a restart; an in-memory counter would
reset every deploy and quietly hand out a fresh budget.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass

_lock = threading.Lock()


def ensure_parent_dir(db_path: str) -> None:
    """Create the database's folder if it is missing.

    A host without a mounted disk has no /data, and SQLite refusing to open
    its file raises at import time — taking the whole app down before it can
    serve a single request.
    """
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    retry_after: int = 0


class Quota:
    def __init__(self, db_path: str, *, daily_cap: int, visitor_daily_cap: int, per_minute_cap: int):
        self.db_path = db_path
        self.daily_cap = daily_cap
        self.visitor_daily_cap = visitor_daily_cap
        self.per_minute_cap = per_minute_cap
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        ensure_parent_dir(self.db_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    visitor TEXT NOT NULL,
                    day     TEXT NOT NULL,
                    ts      REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS usage_day ON usage(day)")
            conn.execute("CREATE INDEX IF NOT EXISTS usage_visitor_day ON usage(visitor, day)")

    @staticmethod
    def _today(now: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(now))

    def check(self, visitor: str, now: float | None = None) -> Decision:
        """Would a request right now be allowed? Does not consume quota."""
        now = time.time() if now is None else now
        day = self._today(now)
        with _lock, self._connect() as conn:
            minute = conn.execute(
                "SELECT COUNT(*) FROM usage WHERE visitor=? AND ts > ?",
                (visitor, now - 60),
            ).fetchone()[0]
            if minute >= self.per_minute_cap:
                return Decision(False, "Too many requests in a row — wait a moment.", 30)

            mine = conn.execute(
                "SELECT COUNT(*) FROM usage WHERE visitor=? AND day=?", (visitor, day)
            ).fetchone()[0]
            if mine >= self.visitor_daily_cap:
                return Decision(False, "You've hit today's limit for this app. Try again tomorrow.", 3600)

            total = conn.execute("SELECT COUNT(*) FROM usage WHERE day=?", (day,)).fetchone()[0]
            if total >= self.daily_cap:
                return Decision(False, "This app has reached its shared daily limit. Try again tomorrow.", 3600)
        return Decision(True)

    def consume(self, visitor: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO usage (visitor, day, ts) VALUES (?,?,?)",
                (visitor, self._today(now), now),
            )
            # Yesterday's rows are only dead weight once the day rolls over.
            conn.execute("DELETE FROM usage WHERE ts < ?", (now - 172800,))

    def stats(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        day = self._today(now)
        with self._connect() as conn:
            used = conn.execute("SELECT COUNT(*) FROM usage WHERE day=?", (day,)).fetchone()[0]
        return {"day": day, "used": used, "cap": self.daily_cap, "remaining": max(0, self.daily_cap - used)}
