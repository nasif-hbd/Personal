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
    # These tests are about the proxy, not the paywall, so give them trial
    # credit rather than threading a subscription through every case. The
    # paywall itself is covered by test_paywall_* below and test_billing.py.
    monkeypatch.setenv("FREE_TRIAL_MESSAGES", "50")
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
    monkeypatch.setenv("FREE_TRIAL_MESSAGES", "5")   # isolate the code gate from the paywall
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


# --- paywall -------------------------------------------------------------


@pytest.fixture()
def paid_client(tmp_path, monkeypatch):
    """A server with a strict paywall: no trial, code and payments enabled."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("SECRET_KEY", "paywall-secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setenv("FREE_TRIAL_MESSAGES", "0")
    monkeypatch.setenv("FREE_ACCESS_CODE", "Nafia is my Sister")
    monkeypatch.setenv("ADMIN_TOKEN", "owner-token")
    monkeypatch.setenv("PAY_BKASH", "01700000000")
    monkeypatch.setenv("PRICE_MONTHLY", "499")
    monkeypatch.chdir(tmp_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return TestClient(main.app)


def _chat(client, token=None):
    headers = {"x-atlas-visitor": token} if token else {}
    with mock.patch("app.main.requests.post", lambda *a, **k: FakeUpstream()):
        return client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=headers
        )


def test_paywall_blocks_chat_without_a_subscription(paid_client):
    r = _chat(paid_client)
    assert r.status_code == 402


def test_paywall_free_code_unlocks_chat(paid_client):
    r = paid_client.post("/api/billing/redeem", json={"code": "nafia is my sister"})
    assert r.status_code == 200 and r.json()["entitlement"]["active"]
    token = r.headers["x-atlas-visitor"]
    assert _chat(paid_client, token).status_code == 200


def test_paywall_wrong_code_still_blocked(paid_client):
    r = paid_client.post("/api/billing/redeem", json={"code": "let me in"})
    assert r.status_code == 400
    assert _chat(paid_client, r.headers["x-atlas-visitor"]).status_code == 402


def test_submitting_a_payment_does_not_unlock_chat(paid_client):
    """The dangerous case: claiming to have paid must not grant access before
    the owner has verified the transaction actually arrived."""
    r = paid_client.post("/api/billing/claim", json={
        "plan": "monthly", "method": "bkash", "reference": "TRX12345", "sender": "01711111111",
    })
    assert r.status_code == 200
    token = r.headers["x-atlas-visitor"]
    assert r.json()["entitlement"]["status"] == "pending"
    assert _chat(paid_client, token).status_code == 402


def test_owner_approval_unlocks_chat(paid_client):
    r = paid_client.post("/api/billing/claim", json={
        "plan": "monthly", "method": "bkash", "reference": "TRX12345",
    })
    token = r.headers["x-atlas-visitor"]
    payment_id = r.json()["id"]

    approve = paid_client.post(
        f"/api/admin/payments/{payment_id}",
        json={"action": "approve"}, headers={"x-atlas-admin": "owner-token"},
    )
    assert approve.status_code == 200
    assert _chat(paid_client, token).status_code == 200


def test_admin_api_rejects_a_wrong_token(paid_client):
    assert paid_client.get("/api/admin/payments").status_code == 401
    assert paid_client.get(
        "/api/admin/payments", headers={"x-atlas-admin": "guess"}
    ).status_code == 401


def test_admin_api_is_closed_when_no_token_is_configured(tmp_path, monkeypatch):
    """An admin API that opens up when unconfigured would let anyone approve
    their own payment the moment the server deploys."""
    monkeypatch.setenv("SECRET_KEY", "s")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "n.db"))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    c = TestClient(main.app)
    assert c.get("/api/admin/payments").status_code == 503


def test_claim_price_comes_from_the_server_not_the_client(paid_client):
    """A buyer must not be able to talk their way into a cheaper year."""
    r = paid_client.post("/api/billing/claim", json={
        "plan": "monthly", "method": "bkash", "reference": "TRX-PRICE", "amount": 1,
    })
    payment_id = r.json()["id"]
    payments = paid_client.get(
        "/api/admin/payments", headers={"x-atlas-admin": "owner-token"}
    ).json()["payments"]
    record = next(p for p in payments if p["id"] == payment_id)
    assert record["amount_minor"] == 49900


def test_unavailable_payment_method_is_refused(paid_client):
    # Only bKash is configured in this fixture.
    r = paid_client.post("/api/billing/claim", json={
        "plan": "monthly", "method": "nagad", "reference": "TRX-NAGAD",
    })
    assert r.status_code == 400


def test_billing_config_exposes_only_configured_rails(paid_client):
    body = paid_client.get("/api/billing/config").json()
    assert [m["id"] for m in body["methods"]] == ["bkash"]
    assert body["methods"][0]["account"] == "01700000000"
    assert body["codeEnabled"] is True


# --- free for all --------------------------------------------------------
# One switch opens everything. The payment code stays in place so charging
# can be turned back on without rebuilding it.


@pytest.fixture()
def open_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("SECRET_KEY", "open-secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "o.db"))
    monkeypatch.setenv("FREE_FOR_ALL", "true")
    monkeypatch.setenv("FREE_TRIAL_MESSAGES", "0")
    monkeypatch.setenv("PAY_BKASH", "01700000000")
    monkeypatch.chdir(tmp_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    return TestClient(main.app)


def test_anyone_can_chat_with_no_subscription(open_client):
    assert _chat(open_client).status_code == 200


def test_free_access_burns_no_trial(open_client):
    """Trials must not tick down while the app is open — otherwise switching
    charging back on would find everyone's trial already spent."""
    for _ in range(3):
        _chat(open_client)
    body = open_client.get("/api/billing/entitlement").json()
    assert body["freeForAll"] is True
    assert body["trialRemaining"] == 0        # trials are off, not consumed


def test_upgrade_surface_is_hidden(open_client):
    health = open_client.get("/api/health").json()
    assert health["billing"]["freeForAll"] is True
    # enabled stays false even though a PAY_ rail is configured: there is
    # nothing to sell, so the client must not advertise a subscription.
    assert health["billing"]["enabled"] is False
    assert open_client.get("/api/billing/config").json()["freeForAll"] is True


def test_spend_caps_still_apply_when_open(tmp_path, monkeypatch):
    """Free for users must not mean unlimited for the owner's wallet."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SECRET_KEY", "s")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cap.db"))
    monkeypatch.setenv("FREE_FOR_ALL", "true")
    monkeypatch.setenv("PER_VISITOR_DAILY_CAP", "2")
    monkeypatch.chdir(tmp_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    c = TestClient(main.app)
    first = _chat(c)
    token = first.headers["x-atlas-visitor"]
    _chat(c, token)
    assert _chat(c, token).status_code == 429


def test_switching_charging_back_on_restores_the_paywall(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("SECRET_KEY", "s")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "back.db"))
    monkeypatch.setenv("FREE_FOR_ALL", "false")
    monkeypatch.setenv("FREE_TRIAL_MESSAGES", "0")
    monkeypatch.chdir(tmp_path)
    from app import config, main
    importlib.reload(config)
    importlib.reload(main)
    assert _chat(TestClient(main.app)).status_code == 402
