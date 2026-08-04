"""Tests for the parts that protect the owner: spend caps and identity."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from app.identity import new_visitor_id, sign, storage_key, verify
from app.limits import Quota
from app.storage import LocalStorage


@pytest.fixture()
def quota(tmp_path: Path) -> Quota:
    return Quota(str(tmp_path / "q.db"), daily_cap=5, visitor_daily_cap=3, per_minute_cap=2)


# --- spend caps ---------------------------------------------------------

def test_allows_until_per_minute_cap(quota: Quota):
    now = 1_000_000.0
    assert quota.check("a", now).allowed
    quota.consume("a", now)
    assert quota.check("a", now).allowed
    quota.consume("a", now)
    blocked = quota.check("a", now)
    assert not blocked.allowed and blocked.retry_after > 0


def test_per_minute_cap_frees_up_after_a_minute(quota: Quota):
    now = 1_000_000.0
    quota.consume("a", now)
    quota.consume("a", now)
    assert not quota.check("a", now).allowed
    assert quota.check("a", now + 61).allowed


def test_visitor_daily_cap(quota: Quota):
    now = 1_000_000.0
    # Spread across minutes so only the daily cap can trip.
    for i in range(3):
        quota.consume("a", now + i * 61)
    decision = quota.check("a", now + 400)
    assert not decision.allowed
    assert "today" in decision.reason.lower()


def test_one_visitor_cannot_exhaust_another(quota: Quota):
    now = 1_000_000.0
    for i in range(3):
        quota.consume("a", now + i * 61)
    assert not quota.check("a", now + 400).allowed
    assert quota.check("b", now + 400).allowed


def test_global_daily_cap_stops_everyone(quota: Quota):
    now = 1_000_000.0
    for i, visitor in enumerate(["a", "a", "a", "b", "b"]):
        quota.consume(visitor, now + i * 61)
    decision = quota.check("c", now + 500)
    assert not decision.allowed
    assert "shared daily limit" in decision.reason


def test_counters_reset_next_day(quota: Quota):
    now = 1_000_000.0
    for i in range(3):
        quota.consume("a", now + i * 61)
    assert not quota.check("a", now + 400).allowed
    assert quota.check("a", now + 86_400 * 1.5).allowed


def test_counts_survive_restart(tmp_path: Path):
    db = str(tmp_path / "q.db")
    now = 1_000_000.0
    first = Quota(db, daily_cap=5, visitor_daily_cap=3, per_minute_cap=2)
    for i in range(3):
        first.consume("a", now + i * 61)
    # A fresh process must not hand out a fresh budget.
    second = Quota(db, daily_cap=5, visitor_daily_cap=3, per_minute_cap=2)
    assert not second.check("a", now + 400).allowed


def test_stats_reports_remaining(quota: Quota):
    now = 1_000_000.0
    quota.consume("a", now)
    stats = quota.stats(now)
    assert stats["used"] == 1 and stats["cap"] == 5 and stats["remaining"] == 4


# --- identity -----------------------------------------------------------

def test_signed_token_round_trips():
    vid = new_visitor_id()
    assert verify(sign(vid, "s3cret"), "s3cret") == vid


def test_tampered_token_is_rejected():
    token = sign(new_visitor_id(), "s3cret")
    assert verify("someone-else." + token.rpartition(".")[2], "s3cret") is None


def test_token_from_another_server_is_rejected():
    assert verify(sign(new_visitor_id(), "secret-a"), "secret-b") is None


@pytest.mark.parametrize("bad", ["", "no-dot", ".", "abc."])
def test_malformed_tokens_are_rejected(bad):
    assert verify(bad, "s3cret") is None


def test_storage_key_hides_the_visitor_id():
    vid = new_visitor_id()
    key = storage_key(vid)
    assert vid not in key and len(key) == 24
    assert storage_key(vid) == key                     # stable
    assert storage_key(new_visitor_id()) != key        # unique


# --- storage ------------------------------------------------------------

def test_local_storage_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStorage(tmp)
        assert store.load("k") is None
        store.save("k", {"plans": [1, 2]})
        assert store.load("k") == {"plans": [1, 2]}


def test_visitors_get_separate_files():
    with tempfile.TemporaryDirectory() as tmp:
        store = LocalStorage(tmp)
        store.save("aaa", {"who": "a"})
        store.save("bbb", {"who": "b"})
        # The whole point of one-file-per-visitor: no clobbering.
        assert store.load("aaa") == {"who": "a"}
        assert store.load("bbb") == {"who": "b"}
        assert len(list(Path(tmp).glob("atlas-*.json"))) == 2


def test_corrupt_file_reads_as_empty_not_crash():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "atlas-k.json").write_text("{not json", encoding="utf-8")
        assert LocalStorage(tmp).load("k") is None
