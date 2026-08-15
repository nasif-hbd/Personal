"""An opt-in leaderboard.

## What this can and cannot do

Scores are computed in each person's own browser and posted here. **That
cannot be made cheat-proof.** Anyone can open the page they are holding and
send this endpoint whatever number they like — the same reason the paywall
lives on the server and not in the client. There is no server-side record of
someone's lessons to check a claim against, because plans are stored as an
opaque per-visitor blob and re-deriving XP here would mean parsing and
trusting that blob too.

So this is built as a *motivational* board, not a competitive ranking:

  * bounds reject impossible claims, which stops casual inflation
  * a growth clamp stops a score leaping overnight
  * entries are opt-in and carry a display name the person chose

If you ever want a board worth competing on, the score has to be computed
from something the server witnessed — lesson completions posted as events as
they happen, rate-limited and timestamped — not handed over as a total. That
is a different and much larger feature; this one is honest about being a
friendly scoreboard.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time

from .limits import ensure_parent_dir

_lock = threading.Lock()

MAX_NAME = 24
# Nothing in the app can plausibly produce more than this. The ceiling is
# generous on purpose: it exists to reject nonsense like 10^9, not to
# second-guess an unusually diligent learner.
MAX_LESSONS = 5000
XP_PER_LESSON_CEILING = 250
XP_FLOOR_ALLOWANCE = 5000
# How fast a listed score may climb. The absolute ceiling above already bounds
# what a claim can be worth; this stops someone who claims a huge lesson count
# from arriving at the top of the board in one request, by making them keep
# submitting for weeks instead.
#
# The burst allowance matters as much as the rate: finishing a plan is +150 on
# its own, and a good session of several lessons lands a few hundred at once. A
# pure hourly rate would throttle exactly the people the board exists to
# celebrate, so an honest sitting always fits inside the burst.
MAX_XP_PER_HOUR = 4000
XP_BURST = 2000

# Control characters and the direction-overrides that let a name redraw the
# row around it. Stripped rather than rejected, so an honest name with a stray
# character still works.
_STRIP = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")


class BoardError(Exception):
    """Message safe to show the person who sent the request."""


def clean_name(raw: str) -> str:
    name = _STRIP.sub("", raw or "")
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) < 2:
        raise BoardError("Pick a name with at least 2 characters.")
    return name[:MAX_NAME]


def plausible_xp(xp: int, lessons: int) -> int:
    """Clamp a claim to what the recorded work could possibly be worth."""
    lessons = max(0, min(MAX_LESSONS, int(lessons)))
    ceiling = lessons * XP_PER_LESSON_CEILING + XP_FLOOR_ALLOWANCE
    return max(0, min(int(xp), ceiling))


class Leaderboard:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        ensure_parent_dir(self.db_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS board (
                    visitor TEXT PRIMARY KEY,
                    name    TEXT NOT NULL,
                    xp      INTEGER NOT NULL DEFAULT 0,
                    level   INTEGER NOT NULL DEFAULT 1,
                    lessons INTEGER NOT NULL DEFAULT 0,
                    streak  INTEGER NOT NULL DEFAULT 0,
                    at      REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS board_xp ON board(xp DESC)")

    # --- writing ---------------------------------------------------------
    def submit(self, visitor: str, *, name: str, xp: int, level: int,
               lessons: int, streak: int, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        name = clean_name(name)
        xp = plausible_xp(xp, lessons)
        lessons = max(0, min(MAX_LESSONS, int(lessons)))
        streak = max(0, min(3650, int(streak)))
        level = max(1, min(999, int(level)))

        with _lock, self._connect() as conn:
            row = conn.execute(
                "SELECT xp, at FROM board WHERE visitor=?", (visitor,)
            ).fetchone()
            if row:
                prev_xp, prev_at = row
                hours = max(0.0, (now - prev_at) / 3600)
                # A score may fall freely — untick a lesson and it should. It
                # is only the climb that gets a speed limit.
                allowed = prev_xp + XP_BURST + MAX_XP_PER_HOUR * hours
                xp = int(min(xp, allowed))
                level = min(level, 999)
            conn.execute(
                "INSERT INTO board (visitor, name, xp, level, lessons, streak, at)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(visitor) DO UPDATE SET"
                " name=excluded.name, xp=excluded.xp, level=excluded.level,"
                " lessons=excluded.lessons, streak=excluded.streak, at=excluded.at",
                (visitor, name, xp, level, lessons, streak, now),
            )
        return self.me(visitor)

    def leave(self, visitor: str) -> None:
        """Remove this visitor. Opting in must be reversible."""
        with _lock, self._connect() as conn:
            conn.execute("DELETE FROM board WHERE visitor=?", (visitor,))

    # --- reading ---------------------------------------------------------
    @staticmethod
    def _row(r) -> dict:
        return {"name": r[0], "xp": r[1], "level": r[2], "lessons": r[3], "streak": r[4]}

    def top(self, limit: int = 25) -> list[dict]:
        limit = max(1, min(100, limit))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, xp, level, lessons, streak FROM board"
                " ORDER BY xp DESC, at ASC LIMIT ?", (limit,)
            ).fetchall()
        return [{**self._row(r), "rank": i + 1} for i, r in enumerate(rows)]

    def me(self, visitor: str) -> dict | None:
        """This visitor's entry and rank, or None if they aren't listed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, xp, level, lessons, streak FROM board WHERE visitor=?",
                (visitor,),
            ).fetchone()
            if not row:
                return None
            # Rank is derived, never stored — a stored rank is wrong the
            # moment anybody else submits.
            ahead = conn.execute(
                "SELECT COUNT(*) FROM board WHERE xp > ?", (row[1],)
            ).fetchone()[0]
        return {**self._row(row), "rank": ahead + 1}

    def size(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM board").fetchone()[0]

    # --- free-tier durability -------------------------------------------
    def export_state(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT visitor, name, xp, level, lessons, streak, at FROM board"
            ).fetchall()
        return {"board": [list(r) for r in rows]}

    def import_state(self, data: dict) -> None:
        rows = (data or {}).get("board") or []
        with _lock, self._connect() as conn:
            for r in rows:
                # Existing rows win: a stale snapshot must not roll a live
                # score backwards.
                conn.execute(
                    "INSERT OR IGNORE INTO board"
                    " (visitor, name, xp, level, lessons, streak, at) VALUES (?,?,?,?,?,?,?)",
                    tuple(r)[:7],
                )

    def is_empty(self) -> bool:
        return self.size() == 0
