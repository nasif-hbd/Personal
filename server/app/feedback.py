"""Feedback: stored here first, emailed second.

The store is the system of record and the email is a notification. That
ordering is the whole design — mail is the part most likely to break (a
rotated app password, a provider outage, a host blocking port 587), and a
message that only ever existed in an SMTP conversation is gone when that
fails. Everything lands in SQLite before any send is attempted, so the
owner can always read it in the admin console even if no mail ever arrives.

Two transports, tried in order:

  * **Resend** (`RESEND_API_KEY`) — ordinary HTTPS, so it works anywhere the
    server can already reach the internet. Preferred, because a PaaS that
    blocks outbound SMTP is common and gives no useful error when it does.
  * **SMTP** (`SMTP_HOST`/`SMTP_USER`/`SMTP_PASS`) — for Gmail this needs an
    App Password, not the account password.

With neither configured, feedback is still collected; it just waits in the
console instead of arriving in an inbox.

A public endpoint that emails the owner is a spam cannon pointed at their
inbox, so submissions are capped per visitor per day and bounded in size.
"""
from __future__ import annotations

import json
import smtplib
import sqlite3
import threading
import time
import uuid
from email.message import EmailMessage
from email.utils import formataddr

import requests

from .limits import ensure_parent_dir

_lock = threading.Lock()

MAX_MESSAGE = 4000
MAX_CONTACT = 120
RESEND_URL = "https://api.resend.com/emails"


class FeedbackError(Exception):
    """Message safe to show the person who sent it."""


class Feedback:
    def __init__(self, db_path: str, *, daily_cap: int = 5):
        self.db_path = db_path
        self.daily_cap = daily_cap
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        ensure_parent_dir(self.db_path)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id      TEXT PRIMARY KEY,
                    visitor TEXT NOT NULL,
                    kind    TEXT NOT NULL DEFAULT 'other',
                    message TEXT NOT NULL,
                    contact TEXT NOT NULL DEFAULT '',
                    meta    TEXT NOT NULL DEFAULT '{}',
                    mailed  INTEGER NOT NULL DEFAULT 0,
                    at      REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS feedback_at ON feedback(at DESC)")

    @staticmethod
    def _today(now: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(now))

    def record(self, visitor: str, *, kind: str, message: str, contact: str = "",
               meta: dict | None = None, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        message = (message or "").strip()
        contact = (contact or "").strip()[:MAX_CONTACT]
        kind = (kind or "other").strip().lower()[:24] or "other"
        if len(message) < 4:
            raise FeedbackError("Tell us a little more than that.")
        if len(message) > MAX_MESSAGE:
            raise FeedbackError(f"That's longer than {MAX_MESSAGE} characters — trim it a little.")

        day_start = now - 86400
        with _lock, self._connect() as conn:
            recent = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE visitor=? AND at > ?",
                (visitor, day_start),
            ).fetchone()[0]
            if recent >= self.daily_cap:
                raise FeedbackError("Thanks — you've sent a few already today. Try again tomorrow.")
            entry_id = uuid.uuid4().hex[:16]
            conn.execute(
                "INSERT INTO feedback (id, visitor, kind, message, contact, meta, mailed, at)"
                " VALUES (?,?,?,?,?,?,0,?)",
                (entry_id, visitor, kind, message, contact, json.dumps(meta or {}), now),
            )
        return {"id": entry_id, "kind": kind, "message": message, "contact": contact, "at": now}

    def mark_mailed(self, entry_id: str) -> None:
        with _lock, self._connect() as conn:
            conn.execute("UPDATE feedback SET mailed=1 WHERE id=?", (entry_id,))

    def recent(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(200, limit))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, kind, message, contact, meta, mailed, at FROM feedback"
                " ORDER BY at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            try:
                meta = json.loads(r[4])
            except ValueError:
                meta = {}
            out.append({"id": r[0], "kind": r[1], "message": r[2], "contact": r[3],
                        "meta": meta, "mailed": bool(r[5]), "at": r[6]})
        return out

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]

    # --- durability ------------------------------------------------------
    def export_state(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, visitor, kind, message, contact, meta, mailed, at FROM feedback"
            ).fetchall()
        return {"feedback": [list(r) for r in rows]}

    def import_state(self, data: dict) -> None:
        rows = (data or {}).get("feedback") or []
        with _lock, self._connect() as conn:
            for r in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO feedback"
                    " (id, visitor, kind, message, contact, meta, mailed, at)"
                    " VALUES (?,?,?,?,?,?,?,?)", tuple(r)[:8])

    def is_empty(self) -> bool:
        return self.count() == 0


def _body(entry: dict) -> str:
    meta = entry.get("meta") or {}
    lines = [
        entry["message"],
        "",
        "—",
        f"Kind: {entry.get('kind', 'other')}",
        f"Reply to: {entry.get('contact') or 'not given'}",
    ]
    for key in ("view", "plans", "level", "app", "screen"):
        if meta.get(key):
            lines.append(f"{key.title()}: {meta[key]}")
    lines.append(f"Reference: {entry['id']}")
    return "\n".join(lines)


def _subject(entry: dict) -> str:
    first = entry["message"].strip().splitlines()[0][:60]
    return f"[Mindora {entry.get('kind','feedback')}] {first}"


def send_email(settings, entry: dict) -> bool:
    """Best effort. Returns True when a transport accepted the message.

    Never raises: the feedback is already stored, and failing the request
    would tell the sender their message was lost when it wasn't.
    """
    to = (settings.feedback_to or "").strip()
    if not to:
        return False
    subject, body = _subject(entry), _body(entry)
    # A reply-to pointing at the sender makes the mail directly answerable,
    # but only when they actually left an address.
    reply_to = entry.get("contact") if "@" in (entry.get("contact") or "") else ""

    if settings.resend_api_key:
        try:
            payload = {
                "from": settings.feedback_from or "Mindora <onboarding@resend.dev>",
                "to": [to], "subject": subject, "text": body,
            }
            if reply_to:
                payload["reply_to"] = reply_to
            r = requests.post(RESEND_URL, timeout=12, json=payload, headers={
                "authorization": f"Bearer {settings.resend_api_key}",
                "content-type": "application/json",
            })
            if r.status_code < 300:
                return True
            print(f"[feedback] resend refused: {r.status_code} {r.text[:200]}", flush=True)
        except Exception as exc:                                  # noqa: BLE001
            print(f"[feedback] resend failed: {exc}", flush=True)

    if settings.smtp_host and settings.smtp_user:
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = formataddr(("Mindora", settings.smtp_user))
            msg["To"] = to
            if reply_to:
                msg["Reply-To"] = reply_to
            msg.set_content(body)
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                s.starttls()
                s.login(settings.smtp_user, settings.smtp_pass)
                s.send_message(msg)
            return True
        except Exception as exc:                                  # noqa: BLE001
            # Gmail rejects the account password — this is where a missing App
            # Password shows up, and it is worth saying so in the log.
            print(f"[feedback] smtp failed: {exc}", flush=True)

    return False
