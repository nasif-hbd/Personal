"""Resolving a lesson title to a YouTube video, using the owner's key.

The key never reaches a browser. That is the whole point: a YouTube Data
key pasted into `index.html` is served publicly from GitHub Pages, and the
first person to read it owns your quota.

## Why the cache is not an optimisation

`search.list` costs **100 units** against a default quota of **10,000 units
per day**. That is one hundred searches for the entire application, for
everybody, per day — not per visitor. Without a cache, twenty people opening
the same lesson would spend twenty per cent of the day's budget on one answer.

So results are cached by normalised query, permanently and shared across all
visitors. The catalogue is a fixed set of lessons, so in practice each one is
resolved once ever and the quota is spent on genuinely new titles. A miss
costs 100 units; a hit costs nothing and returns instantly.

Negative results are cached too, for a day. A title YouTube cannot match will
not match on the next visitor either, and retrying it is the most expensive
way to learn nothing.
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time

import requests

from .limits import ensure_parent_dir

_lock = threading.Lock()

API = "https://www.googleapis.com/youtube/v3/search"

# A search costs 100 units; the default daily quota is 10,000.
UNITS_PER_SEARCH = 100

# How long to remember that a query matched nothing, in seconds. Long enough
# that a broken title is not retried all day, short enough that a video
# uploaded later is eventually found.
MISS_TTL = 86400


class YouTubeError(Exception):
    """Raised with a message safe to show a visitor — never the key."""


def normalise(query: str) -> str:
    """Collapse a query to its cache key.

    Case and whitespace must not split the cache: "Limits  and Continuity"
    and "limits and continuity" are the same 100 units.
    """
    return re.sub(r"\s+", " ", (query or "").strip().lower())


class YouTubeSearch:
    def __init__(self, db_path: str, api_key: str, *, daily_cap: int, visitor_daily_cap: int):
        self.db_path = db_path
        self.api_key = api_key
        self.daily_cap = daily_cap
        self.visitor_daily_cap = visitor_daily_cap
        self._init_db()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        ensure_parent_dir(self.db_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS yt_cache (
                    q        TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    channel  TEXT NOT NULL DEFAULT '',
                    at       REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS yt_usage (
                    visitor TEXT NOT NULL,
                    day     TEXT NOT NULL,
                    ts      REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS yt_usage_day ON yt_usage(day)")

    @staticmethod
    def _today(now: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(now))

    def lookup(self, key: str, now: float) -> dict | None:
        """A cached answer, or None. An expired miss counts as no answer."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT video_id, channel, at FROM yt_cache WHERE q=?", (key,)
            ).fetchone()
        if not row:
            return None
        video_id, channel, at = row
        if not video_id and now - at > MISS_TTL:
            return None
        return {"videoId": video_id or None, "channel": channel}

    def _remember(self, key: str, video_id: str, channel: str, now: float) -> None:
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO yt_cache (q, video_id, channel, at) VALUES (?,?,?,?)",
                (key, video_id or "", channel or "", now),
            )

    def _spend(self, visitor: str, now: float) -> None:
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO yt_usage (visitor, day, ts) VALUES (?,?,?)",
                (visitor, self._today(now), now),
            )
            conn.execute("DELETE FROM yt_usage WHERE ts < ?", (now - 172800,))

    def _check_caps(self, visitor: str, now: float) -> None:
        day = self._today(now)
        with self._connect() as conn:
            mine = conn.execute(
                "SELECT COUNT(*) FROM yt_usage WHERE visitor=? AND day=?", (visitor, day)
            ).fetchone()[0]
            if mine >= self.visitor_daily_cap:
                raise YouTubeError("You've looked up enough videos for today. Try again tomorrow.")
            total = conn.execute(
                "SELECT COUNT(*) FROM yt_usage WHERE day=?", (day,)
            ).fetchone()[0]
            if total >= self.daily_cap:
                raise YouTubeError("Video lookup has hit its shared daily limit. Try again tomorrow.")

    def search(self, query: str, visitor: str, now: float | None = None) -> dict:
        """Resolve a query to {videoId, channel, cached}.

        videoId is None when nothing matched — a real answer, not an error,
        and one worth caching so it is not paid for twice.
        """
        now = time.time() if now is None else now
        key = normalise(query)
        if not key:
            raise YouTubeError("Nothing to search for.")
        if not self.enabled:
            raise YouTubeError("This server has no YouTube key configured.")

        hit = self.lookup(key, now)
        if hit is not None:
            return {**hit, "cached": True}

        # Only a genuine miss costs quota, so the caps bound real spending
        # rather than counting cache hits.
        self._check_caps(visitor, now)
        try:
            resp = requests.get(API, timeout=12, params={
                "part": "snippet", "type": "video", "maxResults": 1,
                "q": query, "key": self.api_key,
            })
        except requests.RequestException:
            raise YouTubeError("Couldn't reach YouTube just now.")

        self._spend(visitor, now)

        if resp.status_code == 403:
            # Quota exhausted or the key is restricted in a way that blocks
            # this server. Both are the owner's problem, not the visitor's,
            # and neither should leak the key or Google's raw message.
            raise YouTubeError("YouTube search is unavailable right now (quota or key restriction).")
        if resp.status_code != 200:
            raise YouTubeError(f"YouTube search failed (HTTP {resp.status_code}).")

        items = (resp.json() or {}).get("items") or []
        item = items[0] if items else {}
        video_id = ((item.get("id") or {}).get("videoId")) or ""
        channel = ((item.get("snippet") or {}).get("channelTitle")) or ""
        self._remember(key, video_id, channel, now)
        return {"videoId": video_id or None, "channel": channel, "cached": False}

    def stats(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        day = self._today(now)
        with self._connect() as conn:
            used = conn.execute("SELECT COUNT(*) FROM yt_usage WHERE day=?", (day,)).fetchone()[0]
            cached = conn.execute("SELECT COUNT(*) FROM yt_cache").fetchone()[0]
        return {
            "day": day, "used": used, "cap": self.daily_cap,
            "remaining": max(0, self.daily_cap - used),
            "cached": cached, "unitsPerSearch": UNITS_PER_SEARCH,
        }
