"""The leaderboard: opt-in, bounded, and honest about being unverifiable.

The board cannot prove a score — see app/leaderboard.py. What it can do is
reject impossible claims and refuse to let a score leap. These pin that down,
and pin down the privacy promise: joining publishes a chosen name and a
number, and leaving actually removes you.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def build(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("FREE_FOR_ALL", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return TestClient(main.app)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    return build(tmp_path, monkeypatch)


def join(c, name, xp, lessons=200, level=5, streak=3, token=None):
    headers = {"x-atlas-visitor": token} if token else {}
    return c.post("/api/leaderboard", headers=headers, json={
        "name": name, "xp": xp, "level": level, "lessons": lessons, "streak": streak})


def test_board_starts_empty(client):
    body = client.get("/api/leaderboard").json()
    assert body["top"] == [] and body["me"] is None and body["size"] == 0


def test_joining_lists_you_and_reports_your_rank(client):
    body = join(client, "Nasif", 1200).json()
    assert body["me"]["name"] == "Nasif"
    assert body["me"]["rank"] == 1
    assert body["size"] == 1


def test_ranking_is_by_xp(client):
    join(client, "Low", 100)
    join(client, "High", 5000)
    join(client, "Mid", 900)
    names = [r["name"] for r in client.get("/api/leaderboard").json()["top"]]
    assert names == ["High", "Mid", "Low"]


def test_rank_is_derived_not_stored(client):
    """A stored rank is wrong the moment anyone else submits."""
    first = join(client, "First", 500)
    token = first.headers["x-atlas-visitor"]
    assert first.json()["me"]["rank"] == 1
    join(client, "Bigger", 9000)
    mine = client.get("/api/leaderboard", headers={"x-atlas-visitor": token}).json()["me"]
    assert mine["rank"] == 2


def test_resubmitting_updates_rather_than_duplicates(client):
    r = join(client, "Nasif", 300)
    token = r.headers["x-atlas-visitor"]
    join(client, "Nasif", 800, token=token)
    body = client.get("/api/leaderboard").json()
    assert body["size"] == 1
    assert body["top"][0]["xp"] == 800


def test_an_impossible_claim_is_clamped(client):
    """250 XP per lesson is already generous; a billion is not a study record."""
    body = join(client, "Cheater", 1_000_000_000, lessons=10).json()
    assert body["me"]["xp"] <= 10 * 250 + 5000


def test_lesson_count_alone_cannot_unlock_any_score(client):
    body = join(client, "Cheater", 10**9, lessons=10**9).json()
    # Lessons are bounded too, so the ceiling they buy is bounded.
    assert body["me"]["xp"] <= 5000 * 250 + 5000


def test_a_score_cannot_leap_after_it_is_listed(client):
    r = join(client, "Nasif", 100, lessons=5000)
    token = r.headers["x-atlas-visitor"]
    jumped = join(client, "Nasif", 900_000, lessons=5000, token=token).json()
    # Bounded by the growth clamp, not by the plausibility ceiling — which for
    # 5000 claimed lessons would otherwise allow 1.25 million.
    assert jumped["me"]["xp"] < 10_000


def test_an_honest_session_is_never_throttled(client):
    """Finishing a plan is +150, and a good sitting lands a few hundred. The
    clamp must not punish exactly the people the board is for."""
    r = join(client, "Nasif", 400)
    token = r.headers["x-atlas-visitor"]
    body = join(client, "Nasif", 400 + 850, token=token).json()
    assert body["me"]["xp"] == 1250


def test_a_score_may_fall_freely(client):
    """Unticking a lesson takes its XP back; the board must accept that."""
    r = join(client, "Nasif", 4000)
    token = r.headers["x-atlas-visitor"]
    body = join(client, "Nasif", 40, token=token).json()
    assert body["me"]["xp"] == 40


def test_leaving_removes_you(client):
    r = join(client, "Nasif", 500)
    token = r.headers["x-atlas-visitor"]
    assert client.get("/api/leaderboard").json()["size"] == 1
    client.request("DELETE", "/api/leaderboard", headers={"x-atlas-visitor": token})
    body = client.get("/api/leaderboard", headers={"x-atlas-visitor": token}).json()
    assert body["size"] == 0 and body["me"] is None


def test_a_short_name_is_refused(client):
    assert join(client, "x", 100).status_code == 400
    assert join(client, "   ", 100).status_code == 400


def test_a_long_name_is_trimmed_not_refused(client):
    body = join(client, "N" * 200, 100).json()
    assert len(body["me"]["name"]) == 24


def test_control_characters_are_stripped_from_names(client):
    """A name carrying a direction override can redraw the row around it."""
    body = join(client, "Nasif‮gnitaehc", 100).json()
    assert "‮" not in body["me"]["name"]
    assert "" not in body["me"]["name"]


def test_the_board_never_exposes_a_visitor_id(client):
    r = join(client, "Nasif", 100)
    token = r.headers["x-atlas-visitor"]
    listing = client.get("/api/leaderboard").text
    assert token not in listing
    assert "visitor" not in listing


def test_one_visitor_cannot_be_two_entries(client):
    r = join(client, "First name", 100)
    token = r.headers["x-atlas-visitor"]
    join(client, "Second name", 200, token=token)
    body = client.get("/api/leaderboard").json()
    assert body["size"] == 1 and body["top"][0]["name"] == "Second name"


def test_access_code_gates_the_board(tmp_path, monkeypatch):
    client = build(tmp_path, monkeypatch, ACCESS_CODE="letmein")
    assert client.get("/api/leaderboard").status_code == 401
    assert join(client, "Nasif", 100).status_code == 401
    ok = client.get("/api/leaderboard", headers={"x-atlas-code": "letmein"})
    assert ok.status_code == 200


def test_snapshot_round_trip_survives_a_wiped_disk(client, tmp_path, monkeypatch):
    """Free tiers wipe the disk; a board that forgets everyone is not a board."""
    from app import main
    join(client, "Nasif", 700)
    snapshot = main.board.export_state()

    second = tmp_path / "second"
    second.mkdir()
    build(second, monkeypatch)
    from app import main as main2
    assert main2.board.is_empty()
    main2.board.import_state(snapshot)
    assert main2.board.top(5)[0]["name"] == "Nasif"


def test_restore_never_rolls_a_live_score_backwards(client):
    from app import main
    r = join(client, "Nasif", 100)
    token = r.headers["x-atlas-visitor"]
    stale = main.board.export_state()
    join(client, "Nasif", 900, token=token)
    main.board.import_state(stale)          # existing rows win
    assert client.get("/api/leaderboard").json()["top"][0]["xp"] == 900
