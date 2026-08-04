"""End-to-end tests against the real FastAPI app, with Anthropic mocked out.

These confirm the behaviour that actually protects the owner's account and
keeps one visitor's data away from another's.
"""
from __future__ import annotations

import importlib
import os
from unittest import mock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("DAILY_REQUEST_CAP", "4")
    monkeypatch.setenv("PER_VISITOR_DAILY_CAP", "2")
    monkeypatch.setenv("PER_MINUTE_CAP", "99")
    monkeypatch.setenv("ALLOWED_MODELS", "claude-sonnet-5")
    monkeypatch.chdir(tmp_path)          # LocalStorage writes here

    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return TestClient(main.app)


class FakeUpstream:
    """Stands in for the streaming Anthropic response."""
    status_code = 200
    text = ""

    def __enter__(self): return self
    def __exit__(self, *a): return False
    def iter_content(self, chunk_size=None):
        yield b'event: content_block_delta\ndata: {"delta":{"text":"hi"}}\n\n'


def test_health_reports_configuration(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["chat"] is True
    assert body["quota"]["cap"] == 4


def test_chat_never_leaks_the_owner_key(client):
    captured = {}

    def fake_post(url, **kw):
        captured.update(kw)
        return FakeUpstream()

    with mock.patch("app.main.requests.post", fake_post):
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    # The key goes upstream...
    assert captured["headers"]["x-api-key"] == "sk-test-not-real"
    # ...and never comes back to the browser.
    assert "sk-test-not-real" not in r.text
    assert not any("sk-test" in v for v in r.headers.values())


def test_client_cannot_choose_an_unlisted_model(client):
    captured = {}

    def fake_post(url, **kw):
        captured.update(kw)
        return FakeUpstream()

    with mock.patch("app.main.requests.post", fake_post):
        client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "hi"}],
            "model": "claude-opus-5",          # pricier, not allowlisted
        })
    assert captured["json"]["model"] == "claude-sonnet-5"


def test_client_cannot_inflate_max_tokens(client):
    captured = {}

    def fake_post(url, **kw):
        captured.update(kw)
        return FakeUpstream()

    with mock.patch("app.main.requests.post", fake_post):
        client.post("/api/chat", json={
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 999999,              # ignored: not part of the schema
        })
    assert captured["json"]["max_tokens"] == 12000


def test_visitor_daily_cap_returns_429(client):
    with mock.patch("app.main.requests.post", lambda *a, **k: FakeUpstream()):
        first = client.post("/api/chat", json={"messages": [{"role": "user", "content": "1"}]})
        token = first.headers["x-atlas-visitor"]
        headers = {"x-atlas-visitor": token}
        client.post("/api/chat", json={"messages": [{"role": "user", "content": "2"}]}, headers=headers)
        third = client.post("/api/chat", json={"messages": [{"role": "user", "content": "3"}]}, headers=headers)
    assert third.status_code == 429
    assert "Retry-After" in third.headers


def test_upstream_failure_is_reported_not_swallowed(client):
    class Failing(FakeUpstream):
        status_code = 500
        text = "internal"

    with mock.patch("app.main.requests.post", lambda *a, **k: Failing()):
        r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert "Upstream error 500" in r.text


def test_state_is_private_to_each_visitor(client):
    a = client.put("/api/state", json={"state": {"plans": ["mine"]}})
    token_a = a.headers["x-atlas-visitor"]
    b = client.put("/api/state", json={"state": {"plans": ["theirs"]}})
    token_b = b.headers["x-atlas-visitor"]
    assert token_a != token_b

    got_a = client.get("/api/state", headers={"x-atlas-visitor": token_a}).json()
    got_b = client.get("/api/state", headers={"x-atlas-visitor": token_b}).json()
    assert got_a["state"] == {"plans": ["mine"]}
    assert got_b["state"] == {"plans": ["theirs"]}


def test_forged_token_gets_a_fresh_identity_not_someone_elses_data(client):
    real = client.put("/api/state", json={"state": {"plans": ["secret"]}})
    stolen_id = real.headers["x-atlas-visitor"].rpartition(".")[0]

    forged = client.get("/api/state", headers={"x-atlas-visitor": f"{stolen_id}.deadbeef"})
    assert forged.json()["state"] == {}          # signature failed → new identity


def test_oversized_state_is_rejected(client):
    r = client.put("/api/state", json={"state": {"blob": "x" * 2_100_000}})
    assert r.status_code == 413


def test_access_code_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SECRET_KEY", "s")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "c.db"))
    monkeypatch.setenv("ACCESS_CODE", "letmein")
    monkeypatch.chdir(tmp_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    c = TestClient(main.app)

    assert c.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}]}).status_code == 401
    with mock.patch("app.main.requests.post", lambda *a, **k: FakeUpstream()):
        ok = c.post("/api/chat",
                    json={"messages": [{"role": "user", "content": "x"}]},
                    headers={"x-atlas-code": "letmein"})
    assert ok.status_code == 200
