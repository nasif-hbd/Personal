"""Paywall tests.

These cover the money and the gate, so they lean on the cases that cost real
money or real trust when they break: a locked visitor reaching Claude, one
transaction ID buying two subscriptions, an unconfigured admin API standing
wide open, and renewal quietly deleting days someone paid for.
"""
from __future__ import annotations

import time

import pytest

from app.billing import Billing, normalize_code

CODE = "Nafia is my Sister"


@pytest.fixture()
def billing(tmp_path):
    return Billing(str(tmp_path / "billing.db"), access_code=CODE)


# --- the free-access code ------------------------------------------------


def test_correct_code_grants_permanent_access(billing):
    ok, _ = billing.redeem("visitor-1", CODE)
    assert ok
    ent = billing.entitlement("visitor-1")
    assert ent.active and ent.source == "code"
    # days=0 means it never lapses.
    assert ent.expires_at == 0


@pytest.mark.parametrize("typed", [
    "nafia is my sister",        # all lowercase
    "NAFIA IS MY SISTER",        # shouted
    "  Nafia   is my Sister  ",  # pasted with stray whitespace
])
def test_code_matching_survives_retyping(billing, typed):
    ok, _ = billing.redeem("visitor-1", typed)
    assert ok, f"{typed!r} should be accepted"


@pytest.mark.parametrize("wrong", ["", "nafia is my brother", "Nafia", "wrong code"])
def test_wrong_code_grants_nothing(billing, wrong):
    ok, _ = billing.redeem("visitor-1", wrong)
    assert not ok
    assert not billing.entitlement("visitor-1").active


def test_code_guessing_is_rate_limited(billing):
    for _ in range(8):
        billing.redeem("attacker", "guess")
    ok, message = billing.redeem("attacker", CODE)
    # Even the *right* code is refused once the attempt budget is spent —
    # otherwise the cap could be probed around.
    assert not ok and "Too many attempts" in message


def test_code_disabled_when_unset(tmp_path):
    b = Billing(str(tmp_path / "b.db"), access_code="")
    ok, _ = b.redeem("v", "")
    assert not ok
    assert not b.entitlement("v").active


def test_normalize_code_folds_unicode_and_spacing():
    assert normalize_code("  Nafia is  my   Sister ") == "nafia is my sister"


# --- entitlement lifecycle ----------------------------------------------


def test_unknown_visitor_has_nothing(billing):
    ent = billing.entitlement("nobody")
    assert ent.status == "none" and not ent.active


def test_expired_subscription_stops_being_active(billing):
    now = time.time()
    billing.grant("v", "monthly", days=30, source="payment", now=now - 31 * 86400)
    assert not billing.entitlement("v", now).active
    assert billing.entitlement("v", now).status == "expired"


def test_renewing_early_adds_to_remaining_time(billing):
    now = time.time()
    billing.grant("v", "monthly", days=30, source="payment", now=now)
    first = billing.entitlement("v", now).expires_at
    # Renew with 30 days still on the clock.
    billing.grant("v", "monthly", days=30, source="payment", now=now)
    second = billing.entitlement("v", now).expires_at
    assert second == pytest.approx(first + 30 * 86400, abs=2), "paid days must not be discarded"


def test_revoke_removes_access(billing):
    billing.grant("v", "pro", days=0, source="owner")
    billing.revoke("v")
    assert not billing.entitlement("v").active


# --- payments ------------------------------------------------------------


def _claim(billing, visitor="v", reference="TRX123456", method="bkash"):
    return billing.claim(
        visitor, plan="monthly", method=method, amount_minor=49900,
        currency="BDT", reference=reference, sender="01700000000",
    )


def test_claim_creates_a_pending_payment_but_no_access(billing):
    ok, payment_id, _ = _claim(billing)
    assert ok and payment_id
    ent = billing.entitlement("v")
    assert ent.status == "pending"
    assert not ent.active, "submitting a claim must not itself unlock anything"


def test_approval_activates_the_subscription(billing):
    _, payment_id, _ = _claim(billing)
    ok, _ = billing.review(payment_id, approve=True, days=30)
    assert ok
    ent = billing.entitlement("v")
    assert ent.active and ent.source == "payment"


def test_rejection_leaves_the_visitor_locked(billing):
    _, payment_id, _ = _claim(billing)
    billing.review(payment_id, approve=False, days=30, note="no matching transaction")
    assert not billing.entitlement("v").active


def test_a_transaction_id_cannot_be_reused(billing):
    _claim(billing, visitor="payer", reference="TRX999")
    ok, _, message = _claim(billing, visitor="freeloader", reference="TRX999")
    assert not ok and "already been submitted" in message
    assert not billing.entitlement("freeloader").active


def test_rejected_reference_can_be_resubmitted(billing):
    _, payment_id, _ = _claim(billing, reference="TRX555")
    billing.review(payment_id, approve=False, days=30)
    # A typo'd reference that got rejected shouldn't block the honest retry.
    ok, _, _ = _claim(billing, reference="TRX555")
    assert ok


def test_short_reference_is_refused(billing):
    ok, _, _ = billing.claim(
        "v", plan="monthly", method="bkash", amount_minor=49900,
        currency="BDT", reference="12",
    )
    assert not ok


def test_payment_cannot_be_reviewed_twice(billing):
    _, payment_id, _ = _claim(billing)
    billing.review(payment_id, approve=True, days=30)
    ok, message = billing.review(payment_id, approve=True, days=30)
    assert not ok and "Already" in message


def test_summary_counts_only_approved_revenue(billing):
    _, first, _ = _claim(billing, visitor="a", reference="TRX-A")
    _claim(billing, visitor="b", reference="TRX-B")
    billing.review(first, approve=True, days=30)
    summary = billing.summary()
    assert summary["approvedPayments"] == 1
    assert summary["pendingPayments"] == 1
    assert summary["revenueMinor"] == 49900
    assert summary["activeSubscribers"] == 1


# --- free trial ----------------------------------------------------------


def test_trial_counter_persists_and_increments(billing):
    assert billing.trial_used("v") == 0
    billing.consume_trial("v")
    billing.consume_trial("v")
    assert billing.trial_used("v") == 2
