"""Anonymous visitor identity.

Nobody signs in. Each browser gets a random id the first time it calls the
server, signed with the server's secret so it can't be edited to read someone
else's saved plans. No email, no name, nothing personal.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets


def new_visitor_id() -> str:
    return secrets.token_urlsafe(18)


def sign(visitor_id: str, secret: str) -> str:
    mac = hmac.new(secret.encode(), visitor_id.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{visitor_id}.{mac}"


def verify(token: str, secret: str) -> str | None:
    """Return the visitor id if the token is authentic, else None."""
    if not token or "." not in token:
        return None
    visitor_id, _, mac = token.rpartition(".")
    if not visitor_id or not mac:
        return None
    expected = hmac.new(secret.encode(), visitor_id.encode(), hashlib.sha256).hexdigest()[:32]
    return visitor_id if hmac.compare_digest(mac, expected) else None


def storage_key(visitor_id: str) -> str:
    """Filename for this visitor's data. Hashed so the raw id isn't on disk."""
    return hashlib.sha256(visitor_id.encode()).hexdigest()[:24]
