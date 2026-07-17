"""
Automated tests for cloud/server.py, run against a real (in-memory-backed)
instance via FastAPI's TestClient — no separate process, no network.

Run:
    pip install -r ../cloud/requirements.txt pytest httpx
    AGENT_API_KEY=testkey pytest
"""
import os
import sys
import time

os.environ.setdefault("AGENT_API_KEY", "testkey")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cloud"))

import pytest
from fastapi.testclient import TestClient

import db as db_module
import server

KEY = os.environ["AGENT_API_KEY"]
HEADERS = {"X-API-Key": KEY}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point the shared db module at a throwaway sqlite file per test, and
    reset the in-memory rate-limit buckets so tests don't leak state into
    each other via server.py's module-level dict."""
    test_db_path = tmp_path / "test_agent.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db_path)
    import sqlite3

    conn = sqlite3.connect(test_db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(db_module, "_conn", conn)
    db_module.init_db()
    server._rate_buckets.clear()
    yield


@pytest.fixture
def client():
    return TestClient(server.app)


def test_health_needs_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_missing_api_key_is_rejected(client):
    resp = client.get("/agents")
    assert resp.status_code == 401


def test_register_and_list_agent(client):
    resp = client.post(
        "/agents/register", headers=HEADERS,
        json={"agent_id": "win-1", "os": "windows", "name": "Test PC", "tags": "home,laptop"},
    )
    assert resp.status_code == 200
    agents = client.get("/agents", headers=HEADERS).json()
    assert len(agents) == 1
    assert agents[0]["id"] == "win-1"
    assert agents[0]["tags"] == "home,laptop"


def test_command_queue_round_trip(client):
    client.post("/agents/register", headers=HEADERS, json={"agent_id": "a1", "os": "windows", "name": "A"})
    created = client.post(
        "/commands", headers=HEADERS,
        json={"agent_id": "a1", "type": "launch_app", "payload": {"app_name": "notepad"}},
    )
    command_id = created.json()["command_id"]

    polled = client.get("/agents/a1/poll", headers=HEADERS).json()
    assert polled["command"]["id"] == command_id
    assert polled["command"]["type"] == "launch_app"

    # A second poll before ack must not hand out the same command again —
    # it was already flipped to in_progress.
    assert client.get("/agents/a1/poll", headers=HEADERS).json()["command"] is None

    ack = client.post(
        f"/agents/a1/ack", headers=HEADERS,
        json={"command_id": command_id, "status": "done", "result": "launched notepad"},
    )
    assert ack.status_code == 200

    rows = client.get("/commands", headers=HEADERS, params={"agent_id": "a1"}).json()
    assert rows[0]["status"] == "done"


def test_failed_command_can_be_retried_but_not_others(client):
    client.post("/agents/register", headers=HEADERS, json={"agent_id": "a1", "os": "windows", "name": "A"})
    cmd_id = client.post(
        "/commands", headers=HEADERS,
        json={"agent_id": "a1", "type": "notify", "payload": {"title": "x", "message": "y"}},
    ).json()["command_id"]

    # Not failed yet — retry should be rejected.
    assert client.post(f"/commands/{cmd_id}/retry", headers=HEADERS).status_code == 400

    client.get("/agents/a1/poll", headers=HEADERS)
    client.post(
        "/agents/a1/ack", headers=HEADERS,
        json={"command_id": cmd_id, "status": "failed", "result": "boom"},
    )

    retried = client.post(f"/commands/{cmd_id}/retry", headers=HEADERS)
    assert retried.status_code == 200
    assert retried.json()["retries"] == 1

    rows = client.get("/commands", headers=HEADERS, params={"agent_id": "a1"}).json()
    assert rows[0]["status"] == "queued"


def test_broadcast_targets_by_tag_and_os(client):
    client.post("/agents/register", headers=HEADERS, json={"agent_id": "w1", "os": "windows", "name": "W", "tags": "home"})
    client.post("/agents/register", headers=HEADERS, json={"agent_id": "m1", "os": "macos", "name": "M", "tags": "home"})
    client.post("/agents/register", headers=HEADERS, json={"agent_id": "w2", "os": "windows", "name": "W2", "tags": "office"})

    by_tag = client.post(
        "/commands/broadcast", headers=HEADERS,
        json={"target": "tag:home", "type": "notify", "payload": {"title": "x", "message": "y"}},
    ).json()
    assert sorted(by_tag["targeted_agents"]) == ["m1", "w1"]

    by_os = client.post(
        "/commands/broadcast", headers=HEADERS,
        json={"target": "os:windows", "type": "notify", "payload": {"title": "x", "message": "y"}},
    ).json()
    assert sorted(by_os["targeted_agents"]) == ["w1", "w2"]

    by_all = client.post(
        "/commands/broadcast", headers=HEADERS,
        json={"target": "all", "type": "notify", "payload": {"title": "x", "message": "y"}},
    ).json()
    assert len(by_all["targeted_agents"]) == 3


def test_broadcast_with_no_matching_agents_is_404(client):
    resp = client.post(
        "/commands/broadcast", headers=HEADERS,
        json={"target": "tag:nonexistent", "type": "notify", "payload": {}},
    )
    assert resp.status_code == 404


def test_command_ttl_expires_instead_of_firing_late(client):
    client.post("/agents/register", headers=HEADERS, json={"agent_id": "a1", "os": "windows", "name": "A"})
    client.post(
        "/commands", headers=HEADERS,
        json={"agent_id": "a1", "type": "notify", "payload": {"title": "x", "message": "y"}, "ttl_seconds": 1},
    )
    time.sleep(1.2)
    polled = client.get("/agents/a1/poll", headers=HEADERS).json()
    assert polled["command"] is None

    rows = client.get("/commands", headers=HEADERS, params={"agent_id": "a1"}).json()
    assert rows[0]["status"] == "expired"


def test_file_upload_list_download_round_trip(client):
    import base64

    client.post("/agents/register", headers=HEADERS, json={"agent_id": "a1", "os": "windows", "name": "A"})
    content = base64.b64encode(b"hello world").decode()
    uploaded = client.post(
        "/files/upload", headers=HEADERS,
        json={"agent_id": "a1", "filename": "note.txt", "content_type": "text/plain", "content_base64": content},
    ).json()
    file_id = uploaded["file_id"]
    assert uploaded["size_bytes"] == len(b"hello world")

    listed = client.get("/files", headers=HEADERS, params={"agent_id": "a1"}).json()
    assert listed[0]["id"] == file_id

    downloaded = client.get(f"/files/{file_id}", headers=HEADERS)
    assert downloaded.content == b"hello world"
    assert downloaded.headers["content-type"] == "text/plain; charset=utf-8" or downloaded.headers["content-type"] == "text/plain"


def test_download_missing_file_is_404(client):
    resp = client.get("/files/999", headers=HEADERS)
    assert resp.status_code == 404


def test_audit_log_merges_sources(client):
    client.post("/agents/register", headers=HEADERS, json={"agent_id": "a1", "os": "windows", "name": "A"})
    client.post(
        "/commands", headers=HEADERS,
        json={"agent_id": "a1", "type": "notify", "payload": {"title": "x", "message": "y"}},
    )
    client.post("/webhooks/github", headers=HEADERS, json={"action": "push"})

    audit = client.get("/audit", headers=HEADERS).json()
    kinds = {row["kind"] for row in audit}
    assert "command" in kinds
    assert "webhook" in kinds


def test_notify_external_without_config_is_501(client):
    resp = client.post("/notify/external", headers=HEADERS, json={"message": "hi"})
    assert resp.status_code == 501


def test_sms_without_twilio_config_is_501(client):
    resp = client.post("/sms/send", headers=HEADERS, json={"to": "+15555550100", "body": "hi"})
    assert resp.status_code == 501


def test_rate_limit_blocks_after_threshold(client, monkeypatch):
    monkeypatch.setattr(server, "RATE_LIMIT_REQUESTS", 3)
    for _ in range(3):
        assert client.get("/health").status_code == 200  # /health has no auth, doesn't count
    for _ in range(3):
        resp = client.get("/agents", headers=HEADERS)
        assert resp.status_code == 200
    limited = client.get("/agents", headers=HEADERS)
    assert limited.status_code == 429
