"""Feedback: stored first, emailed second, and never lost to a broken mailer."""
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
    monkeypatch.setenv("ADMIN_TOKEN", "secret-admin")
    monkeypatch.setenv("FEEDBACK_TO", "owner@example.com")
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


ADMIN = {"x-atlas-admin": "secret-admin"}


def sent(c, message="The catalog needs more statistics courses.", **kw):
    return c.post("/api/feedback", json={"message": message, **kw})


def test_health_advertises_the_endpoint(client):
    assert client.get("/api/health").json()["feedback"] is True


def test_a_message_is_stored(client):
    assert sent(client).status_code == 200
    items = client.get("/api/admin/feedback", headers=ADMIN).json()["items"]
    assert len(items) == 1
    assert items[0]["message"].startswith("The catalog needs")


def test_it_is_kept_even_when_the_mailer_fails(client):
    """The store is the record; mail is a notification. A dead SMTP host must
    not turn someone's message into nothing."""
    with mock.patch("app.main.send_email", side_effect=RuntimeError("smtp down")):
        resp = sent(client, "This should survive a broken mailer.")
    assert resp.status_code == 200
    items = client.get("/api/admin/feedback", headers=ADMIN).json()["items"]
    assert items[0]["message"] == "This should survive a broken mailer."
    assert items[0]["mailed"] is False


def test_a_successful_send_is_recorded(client):
    with mock.patch("app.main.send_email", return_value=True):
        sent(client, "Mailed fine.")
    assert client.get("/api/admin/feedback", headers=ADMIN).json()["items"][0]["mailed"] is True


def test_context_is_captured(client):
    sent(client, "Broken on the plans screen.", kind="bug",
         meta={"view": "plans", "app": "web"})
    item = client.get("/api/admin/feedback", headers=ADMIN).json()["items"][0]
    assert item["kind"] == "bug"
    assert item["meta"]["view"] == "plans"


def test_an_empty_message_is_refused(client):
    assert sent(client, "  ").status_code == 400
    assert sent(client, "hm").status_code == 400


def test_an_enormous_message_is_refused(client):
    assert sent(client, "x" * 5000).status_code == 400


def test_the_honeypot_is_accepted_and_discarded(client):
    """Telling a bot it was caught only helps it try again differently."""
    resp = client.post("/api/feedback", json={
        "message": "buy cheap things", "website": "http://spam.example"})
    assert resp.status_code == 200
    assert client.get("/api/admin/feedback", headers=ADMIN).json()["total"] == 0


def test_one_visitor_cannot_flood_the_inbox(client):
    first = sent(client, "Message number one.")
    token = first.headers["x-atlas-visitor"]
    headers = {"x-atlas-visitor": token}
    codes = [client.post("/api/feedback", headers=headers,
                         json={"message": f"Message number {i}."}).status_code
             for i in range(2, 9)]
    assert 429 not in codes          # the cap answers 400, with a readable reason
    assert 400 in codes
    assert client.get("/api/admin/feedback", headers=ADMIN).json()["total"] == 5


def test_the_cap_is_per_visitor_not_global(client):
    for i in range(5):
        sent(client, f"Filling my own quota {i}.")
    other = TestClient(client.app).post("/api/feedback", json={"message": "A different person."})
    assert other.status_code == 200


def test_reading_feedback_is_owner_only(client):
    sent(client)
    assert client.get("/api/admin/feedback").status_code == 401
    assert client.get("/api/admin/feedback", headers=ADMIN).status_code == 200


def test_the_listing_never_exposes_a_visitor_id(client):
    r = sent(client)
    token = r.headers["x-atlas-visitor"]
    assert token not in client.get("/api/admin/feedback", headers=ADMIN).text


def test_admin_reports_whether_mail_is_actually_configured(tmp_path, monkeypatch):
    without = build(tmp_path, monkeypatch)
    assert without.get("/api/admin/feedback", headers=ADMIN).json()["mailConfigured"] is False

    second = tmp_path / "s"
    second.mkdir()
    with_mail = build(second, monkeypatch, RESEND_API_KEY="re_test")
    assert with_mail.get("/api/admin/feedback", headers=ADMIN).json()["mailConfigured"] is True


def test_collection_still_works_with_no_transport_at_all(tmp_path, monkeypatch):
    """Feedback waits in the console rather than being refused."""
    client = build(tmp_path, monkeypatch, FEEDBACK_TO="")
    assert sent(client).status_code == 200
    assert client.get("/api/admin/feedback", headers=ADMIN).json()["total"] == 1


# --- the mailer itself ---------------------------------------------------

def test_resend_is_preferred_and_carries_a_reply_to(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, RESEND_API_KEY="re_test")
    from app import feedback as fb, config
    entry = {"id": "abc", "kind": "bug", "message": "It broke.",
             "contact": "them@example.com", "meta": {"view": "plans"}}
    posted = mock.Mock(status_code=200)
    with mock.patch("app.feedback.requests.post", return_value=posted) as post:
        assert fb.send_email(config.settings, entry) is True
    payload = post.call_args.kwargs["json"]
    assert payload["to"] == ["owner@example.com"]
    assert payload["reply_to"] == "them@example.com"
    assert "It broke." in payload["text"]
    assert "plans" in payload["text"]


def test_no_reply_to_when_no_address_was_given(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, RESEND_API_KEY="re_test")
    from app import feedback as fb, config
    entry = {"id": "abc", "kind": "idea", "message": "A thought.", "contact": "", "meta": {}}
    with mock.patch("app.feedback.requests.post", return_value=mock.Mock(status_code=200)) as post:
        fb.send_email(config.settings, entry)
    assert "reply_to" not in post.call_args.kwargs["json"]


def test_smtp_is_the_fallback_when_resend_is_absent(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, SMTP_HOST="smtp.gmail.com",
          SMTP_USER="me@gmail.com", SMTP_PASS="app-password")
    from app import feedback as fb, config
    entry = {"id": "abc", "kind": "idea", "message": "Hello.", "contact": "", "meta": {}}
    with mock.patch("app.feedback.smtplib.SMTP") as smtp:
        assert fb.send_email(config.settings, entry) is True
    smtp.assert_called_once()
    assert smtp.return_value.__enter__.return_value.send_message.called


def test_a_failing_mailer_reports_false_rather_than_raising(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, SMTP_HOST="smtp.gmail.com",
          SMTP_USER="me@gmail.com", SMTP_PASS="wrong")
    from app import feedback as fb, config
    entry = {"id": "abc", "kind": "idea", "message": "Hello.", "contact": "", "meta": {}}
    with mock.patch("app.feedback.smtplib.SMTP", side_effect=OSError("refused")):
        assert fb.send_email(config.settings, entry) is False


def test_nothing_is_sent_without_a_destination(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, FEEDBACK_TO="", RESEND_API_KEY="re_test")
    from app import feedback as fb, config
    entry = {"id": "a", "kind": "idea", "message": "Hi.", "contact": "", "meta": {}}
    with mock.patch("app.feedback.requests.post") as post:
        assert fb.send_email(config.settings, entry) is False
    assert not post.called


def test_snapshot_round_trip(client, tmp_path, monkeypatch):
    from app import main
    sent(client, "Keep me through a disk reset.")
    snapshot = main.feedback.export_state()

    second = tmp_path / "second"
    second.mkdir()
    build(second, monkeypatch)
    from app import main as main2
    assert main2.feedback.is_empty()
    main2.feedback.import_state(snapshot)
    assert main2.feedback.recent()[0]["message"] == "Keep me through a disk reset."
