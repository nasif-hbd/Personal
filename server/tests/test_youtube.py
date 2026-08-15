"""The YouTube proxy: the key stays here, and the quota is not squandered.

A search costs 100 units of a 10,000/day quota, so the cache is the only
reason this is affordable at all. These tests pin that down.
"""
from __future__ import annotations

import importlib
from unittest import mock

import pytest
from fastapi.testclient import TestClient


def build(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("FREE_FOR_ALL", "true")
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIza-test-not-real")
    monkeypatch.setenv("YT_DAILY_SEARCHES", "5")
    monkeypatch.setenv("YT_VISITOR_DAILY_SEARCHES", "3")
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


def fake_result(video_id="abc123", channel="3Blue1Brown"):
    resp = mock.Mock()
    resp.status_code = 200
    resp.json.return_value = {
        "items": [{"id": {"videoId": video_id}, "snippet": {"channelTitle": channel}}]
    }
    return resp


def test_health_advertises_youtube_without_leaking_the_key(client):
    body = client.get("/api/health").json()
    assert body["youtube"] is True
    assert "AIza-test-not-real" not in client.get("/api/health").text


def test_search_returns_a_video(client):
    with mock.patch("app.youtube.requests.get", return_value=fake_result()) as get:
        body = client.get("/api/yt/search", params={"q": "limits and continuity"}).json()
    assert body == {"videoId": "abc123", "channel": "3Blue1Brown", "cached": False}
    # The key goes to Google, never to the caller.
    assert get.call_args.kwargs["params"]["key"] == "AIza-test-not-real"


def test_the_key_never_reaches_the_client(client):
    with mock.patch("app.youtube.requests.get", return_value=fake_result()):
        resp = client.get("/api/yt/search", params={"q": "vectors"})
    assert "AIza-test-not-real" not in resp.text


def test_second_identical_search_is_free(client):
    with mock.patch("app.youtube.requests.get", return_value=fake_result()) as get:
        client.get("/api/yt/search", params={"q": "eigenvalues"})
        body = client.get("/api/yt/search", params={"q": "eigenvalues"}).json()
    assert get.call_count == 1, "a repeated query must not spend 100 more units"
    assert body["cached"] is True
    assert body["videoId"] == "abc123"


def test_cache_ignores_case_and_spacing(client):
    with mock.patch("app.youtube.requests.get", return_value=fake_result()) as get:
        client.get("/api/yt/search", params={"q": "Limits  and Continuity"})
        client.get("/api/yt/search", params={"q": "limits and continuity"})
    assert get.call_count == 1


def test_cache_is_shared_between_visitors(client):
    """The whole point: 20 people opening one lesson cost 100 units, not 2000."""
    with mock.patch("app.youtube.requests.get", return_value=fake_result()) as get:
        first = client.get("/api/yt/search", params={"q": "derivatives"})
        token = first.headers["x-atlas-visitor"]
        # A different browser — no token, so the server issues a new identity.
        second = TestClient(client.app).get("/api/yt/search", params={"q": "derivatives"})
    assert get.call_count == 1
    assert second.json()["cached"] is True
    assert second.headers["x-atlas-visitor"] != token


def test_no_match_is_remembered_so_it_is_not_paid_for_twice(client):
    empty = mock.Mock(status_code=200)
    empty.json.return_value = {"items": []}
    with mock.patch("app.youtube.requests.get", return_value=empty) as get:
        first = client.get("/api/yt/search", params={"q": "nonsense query"}).json()
        second = client.get("/api/yt/search", params={"q": "nonsense query"}).json()
    assert first["videoId"] is None
    assert second["videoId"] is None and second["cached"] is True
    assert get.call_count == 1


def test_per_visitor_cap_stops_one_browser_eating_the_day(client):
    with mock.patch("app.youtube.requests.get", return_value=fake_result()):
        first = client.get("/api/yt/search", params={"q": "q0"})
        token = first.headers["x-atlas-visitor"]
        headers = {"x-atlas-visitor": token}
        for i in range(1, 3):
            assert client.get("/api/yt/search", params={"q": f"q{i}"}, headers=headers).status_code == 200
        blocked = client.get("/api/yt/search", params={"q": "q9"}, headers=headers)
    assert blocked.status_code == 429


def test_shared_daily_cap_is_the_hard_ceiling(client):
    """Each visitor stays under its own cap; the global one still stops them."""
    with mock.patch("app.youtube.requests.get", return_value=fake_result()):
        codes = []
        for i in range(7):
            # A fresh client each time = a fresh visitor, so only the
            # server-wide cap can be what refuses these.
            codes.append(TestClient(client.app)
                         .get("/api/yt/search", params={"q": f"unique-{i}"}).status_code)
    assert codes[:5] == [200] * 5
    assert 429 in codes[5:]


def test_cache_hits_do_not_count_against_the_cap(client):
    with mock.patch("app.youtube.requests.get", return_value=fake_result()):
        first = client.get("/api/yt/search", params={"q": "cached one"})
        headers = {"x-atlas-visitor": first.headers["x-atlas-visitor"]}
        for _ in range(20):
            resp = client.get("/api/yt/search", params={"q": "cached one"}, headers=headers)
            assert resp.status_code == 200


def test_google_403_is_not_reported_as_a_visitor_error(client):
    denied = mock.Mock(status_code=403)
    denied.json.return_value = {}
    with mock.patch("app.youtube.requests.get", return_value=denied):
        resp = client.get("/api/yt/search", params={"q": "anything"})
    assert resp.status_code == 429
    assert "AIza" not in resp.text


def test_empty_query_is_rejected(client):
    resp = client.get("/api/yt/search", params={"q": "   "})
    assert resp.status_code == 429


def test_without_a_key_the_endpoint_reports_unavailable(tmp_path, monkeypatch):
    client = build(tmp_path, monkeypatch, YOUTUBE_API_KEY="")
    assert client.get("/api/health").json()["youtube"] is False
    assert client.get("/api/yt/search", params={"q": "x"}).status_code == 503


def test_stats_are_owner_only(tmp_path, monkeypatch):
    client = build(tmp_path, monkeypatch, ADMIN_TOKEN="secret-admin")
    assert client.get("/api/yt/stats").status_code == 401
    body = client.get("/api/yt/stats", headers={"x-atlas-admin": "secret-admin"}).json()
    assert body["cap"] == 5 and body["unitsPerSearch"] == 100


def test_access_code_gates_lookup_too(tmp_path, monkeypatch):
    client = build(tmp_path, monkeypatch, ACCESS_CODE="letmein")
    assert client.get("/api/yt/search", params={"q": "x"}).status_code == 401
    with mock.patch("app.youtube.requests.get", return_value=fake_result()):
        ok = client.get("/api/yt/search", params={"q": "x"}, headers={"x-atlas-code": "letmein"})
    assert ok.status_code == 200
