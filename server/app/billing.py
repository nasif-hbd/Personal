"""Subscriptions, payments and the free-access code.

Everything here is server-side on purpose. A browser can be edited by whoever
is holding it, so a paywall enforced in JavaScript is decoration — the only
check that counts is the one this module performs before the server spends the
owner's Anthropic credit.

Payment model: **manual verification**. bKash, Nagad and Rocket only issue
merchant API credentials to registered businesses, which most people launching
an app do not have on day one. So a visitor sends money themselves, submits the
transaction ID, and the owner approves it against their own statement. The
seams are clean enough that a real gateway can replace the manual step later
without the rest of the app noticing.

Money is stored in **minor units** (poisha, cents) as integers. Floats lose
pennies, and losing pennies in a payments table is how disputes start.
"""
from __future__ import annotations

import hmac
import os
import secrets
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass

_lock = threading.Lock()

# Payment rails offered at checkout. `manual` means the visitor transfers the
# money themselves and submits a reference for the owner to verify.
METHODS = {
    "bkash": {"label": "bKash", "kind": "manual", "reference": "TrxID", "hint": "Send Money, then copy the TrxID from the confirmation SMS."},
    "nagad": {"label": "Nagad", "kind": "manual", "reference": "TrxID", "hint": "Send Money, then copy the TrxID from the confirmation SMS."},
    "rocket": {"label": "Rocket", "kind": "manual", "reference": "TrxID", "hint": "Send Money, then copy the TrxID from the confirmation SMS."},
    "bank": {"label": "Bank transfer", "kind": "manual", "reference": "Reference number", "hint": "Use your name as the transfer reference."},
    "gpay": {"label": "Google Pay", "kind": "manual", "reference": "Transaction ID", "hint": "Copy the transaction ID from your Google Pay receipt."},
}

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"


def normalize_code(raw: str) -> str:
    """Fold a typed code so trivial differences don't lock a paying user out.

    People retype a code from a message with a capital letter changed or two
    spaces between words. None of that should matter; the secret is the words.
    """
    text = unicodedata.normalize("NFKC", raw or "")
    return " ".join(text.split()).casefold()


@dataclass(frozen=True)
class Entitlement:
    status: str          # "none" | "active" | "pending" | "expired"
    plan: str = ""
    expires_at: float = 0.0
    source: str = ""     # "code" | "payment" | "owner"

    @property
    def active(self) -> bool:
        return self.status == "active"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "plan": self.plan,
            "expiresAt": self.expires_at or None,
            "source": self.source,
            "active": self.active,
        }


NO_ENTITLEMENT = Entitlement(status="none")


class Billing:
    def __init__(self, db_path: str, *, access_code: str, redeem_attempt_cap: int = 8):
        self.db_path = db_path
        self.access_code = normalize_code(access_code)
        self.redeem_attempt_cap = redeem_attempt_cap
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entitlements (
                    visitor    TEXT PRIMARY KEY,
                    plan       TEXT NOT NULL,
                    status     TEXT NOT NULL,
                    expires_at REAL NOT NULL DEFAULT 0,
                    source     TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id           TEXT PRIMARY KEY,
                    visitor      TEXT NOT NULL,
                    plan         TEXT NOT NULL,
                    method       TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency     TEXT NOT NULL,
                    reference    TEXT NOT NULL,
                    sender       TEXT NOT NULL DEFAULT '',
                    status       TEXT NOT NULL,
                    note         TEXT NOT NULL DEFAULT '',
                    created_at   REAL NOT NULL,
                    reviewed_at  REAL NOT NULL DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS payments_status ON payments(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS payments_visitor ON payments(visitor, created_at)")
            # One approved payment per transaction reference. Without this a
            # single TrxID could be submitted by several visitors and each get
            # a subscription from one real payment.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS payments_ref ON payments(method, reference) "
                "WHERE status != 'rejected'"
            )
            # Trial consumption is tracked here rather than derived from the
            # rate-limit table, whose rows are purged after 48 hours — a trial
            # that silently refills every two days is not a trial.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trial_usage (
                    visitor TEXT PRIMARY KEY,
                    used    INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS redeem_attempts (
                    visitor TEXT NOT NULL,
                    ts      REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS redeem_visitor ON redeem_attempts(visitor, ts)")

    # --- entitlement ----------------------------------------------------

    def entitlement(self, visitor: str, now: float | None = None) -> Entitlement:
        now = time.time() if now is None else now
        with self._connect() as conn:
            row = conn.execute(
                "SELECT plan, status, expires_at, source FROM entitlements WHERE visitor=?",
                (visitor,),
            ).fetchone()
            if row and row["status"] == APPROVED:
                # expires_at of 0 means it never expires (owner comp / code).
                if row["expires_at"] and row["expires_at"] < now:
                    return Entitlement("expired", row["plan"], row["expires_at"], row["source"])
                return Entitlement("active", row["plan"], row["expires_at"], row["source"])

            waiting = conn.execute(
                "SELECT plan FROM payments WHERE visitor=? AND status=? ORDER BY created_at DESC LIMIT 1",
                (visitor, PENDING),
            ).fetchone()
        if waiting:
            return Entitlement("pending", waiting["plan"])
        return NO_ENTITLEMENT

    def grant(self, visitor: str, plan: str, *, days: int, source: str, now: float | None = None) -> Entitlement:
        """Give this visitor access. days=0 grants access that never expires.

        Time is added to whatever is left rather than replacing it, so renewing
        early never destroys days the person already paid for.
        """
        now = time.time() if now is None else now
        current = self.entitlement(visitor, now)
        base = current.expires_at if (current.active and current.expires_at) else now
        expires = 0.0 if days == 0 else base + days * 86400
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO entitlements (visitor, plan, status, expires_at, source, updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(visitor) DO UPDATE SET "
                "plan=excluded.plan, status=excluded.status, expires_at=excluded.expires_at, "
                "source=excluded.source, updated_at=excluded.updated_at",
                (visitor, plan, APPROVED, expires, source, now),
            )
        return Entitlement("active", plan, expires, source)

    def revoke(self, visitor: str) -> None:
        with _lock, self._connect() as conn:
            conn.execute("DELETE FROM entitlements WHERE visitor=?", (visitor,))

    # --- free trial -----------------------------------------------------

    def trial_used(self, visitor: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT used FROM trial_usage WHERE visitor=?", (visitor,)).fetchone()
        return row["used"] if row else 0

    def consume_trial(self, visitor: str) -> None:
        with _lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO trial_usage (visitor, used) VALUES (?,1) "
                "ON CONFLICT(visitor) DO UPDATE SET used = used + 1",
                (visitor,),
            )

    # --- free access code -----------------------------------------------

    def redeem(self, visitor: str, code: str, *, plan: str = "pro", now: float | None = None) -> tuple[bool, str]:
        """Check the free-access code. Returns (ok, message).

        Attempts are capped: the code is short enough to guess given unlimited
        tries, and unlimited tries is exactly what an HTTP endpoint offers.
        """
        now = time.time() if now is None else now
        if not self.access_code:
            return False, "Free access codes are not enabled on this server."

        with _lock, self._connect() as conn:
            recent = conn.execute(
                "SELECT COUNT(*) AS n FROM redeem_attempts WHERE visitor=? AND ts > ?",
                (visitor, now - 3600),
            ).fetchone()["n"]
            if recent >= self.redeem_attempt_cap:
                return False, "Too many attempts. Try again in an hour."
            conn.execute("INSERT INTO redeem_attempts (visitor, ts) VALUES (?,?)", (visitor, now))
            conn.execute("DELETE FROM redeem_attempts WHERE ts < ?", (now - 86400,))

        # compare_digest rather than ==, so response timing can't be used to
        # discover the code one character at a time.
        if not hmac.compare_digest(normalize_code(code), self.access_code):
            return False, "That code isn't valid."

        self.grant(visitor, plan, days=0, source="code", now=now)
        return True, "Free access unlocked."

    # --- payments -------------------------------------------------------

    def claim(
        self,
        visitor: str,
        *,
        plan: str,
        method: str,
        amount_minor: int,
        currency: str,
        reference: str,
        sender: str = "",
        now: float | None = None,
    ) -> tuple[bool, str, str]:
        """Record a payment the visitor says they made. Returns (ok, id, message)."""
        now = time.time() if now is None else now
        reference = (reference or "").strip()
        if len(reference) < 4:
            return False, "", "Enter the transaction ID from your payment confirmation."

        payment_id = secrets.token_urlsafe(9)
        try:
            with _lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO payments (id, visitor, plan, method, amount_minor, currency, "
                    "reference, sender, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (payment_id, visitor, plan, method, amount_minor, currency,
                     reference, (sender or "").strip(), PENDING, now),
                )
        except sqlite3.IntegrityError:
            # The unique index caught a reference already submitted. Either a
            # double submit or someone reusing another person's TrxID.
            return False, "", "That transaction ID has already been submitted."
        return True, payment_id, "Payment submitted for verification."

    def payments(self, *, status: str = "", limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM payments WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM payments ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(row) for row in rows]

    def review(
        self, payment_id: str, *, approve: bool, days: int, note: str = "", now: float | None = None
    ) -> tuple[bool, str]:
        """Owner decision on a pending payment. Approving grants the plan."""
        now = time.time() if now is None else now
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
            if not row:
                return False, "No such payment."
            if row["status"] != PENDING:
                return False, f"Already {row['status']}."

        with _lock, self._connect() as conn:
            conn.execute(
                "UPDATE payments SET status=?, note=?, reviewed_at=? WHERE id=?",
                (APPROVED if approve else REJECTED, note, now, payment_id),
            )
        if approve:
            self.grant(row["visitor"], row["plan"], days=days, source="payment", now=now)
            return True, "Approved — subscription active."
        return True, "Rejected."

    # --- snapshot / restore ---------------------------------------------
    # Free hosting has no persistent disk: the filesystem resets whenever the
    # instance sleeps or redeploys, which would erase every subscription
    # someone paid for. These two methods let the app mirror the billing
    # tables into whatever durable storage it already has (Google Drive), so
    # a wiped disk costs nothing. The data is small — a few KB per hundred
    # customers — so a full snapshot is simpler and safer than a diff.

    TABLES = ("entitlements", "payments", "trial_usage")

    def export_state(self) -> dict:
        with self._connect() as conn:
            return {
                "version": 1,
                "savedAt": time.time(),
                **{name: [dict(r) for r in conn.execute(f"SELECT * FROM {name}").fetchall()]
                   for name in self.TABLES},
            }

    def import_state(self, data: dict) -> int:
        """Load a snapshot in. Returns the number of rows restored.

        Existing rows win: this runs at startup to refill an empty database,
        and must never roll a live one backwards to an older snapshot.
        """
        if not isinstance(data, dict):
            return 0
        restored = 0
        with _lock, self._connect() as conn:
            for name in self.TABLES:
                rows = data.get(name)
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict) or not row:
                        continue
                    columns = ",".join(row.keys())
                    marks = ",".join("?" * len(row))
                    try:
                        cur = conn.execute(
                            f"INSERT OR IGNORE INTO {name} ({columns}) VALUES ({marks})",
                            tuple(row.values()),
                        )
                        restored += cur.rowcount
                    except sqlite3.Error:
                        # One malformed row must not abandon the rest of the
                        # restore — a partial recovery beats none.
                        continue
        return restored

    def is_empty(self) -> bool:
        with self._connect() as conn:
            for name in self.TABLES:
                if conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone():
                    return False
        return True

    def summary(self, now: float | None = None) -> dict:
        """Owner-facing numbers. Revenue counts approved payments only."""
        now = time.time() if now is None else now
        with self._connect() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM payments WHERE status=?", (PENDING,)
            ).fetchone()["n"]
            approved = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(amount_minor),0) AS total "
                "FROM payments WHERE status=?", (APPROVED,)
            ).fetchone()
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM entitlements WHERE status=? AND (expires_at=0 OR expires_at>?)",
                (APPROVED, now),
            ).fetchone()["n"]
        return {
            "pendingPayments": pending,
            "approvedPayments": approved["n"],
            "revenueMinor": approved["total"],
            "activeSubscribers": active,
        }
