"""Mindora backend — shared Claude access and shared storage.

Two jobs:

  POST /api/chat    proxies to Anthropic using the owner's key, so visitors
                    never enter one. Capped, because it bills to the owner.
  GET/PUT /api/state  stores each visitor's plans in the owner's Google Drive,
                    one file per visitor.

The owner's secrets stay on the server: the Anthropic key and the Google
refresh token are read from the environment and are never sent to a browser.
"""
from __future__ import annotations

import hmac
import json

import requests
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .billing import METHODS, Billing
from .config import settings
from .feedback import Feedback, FeedbackError, send_email
from .identity import new_visitor_id, sign, storage_key, verify
from .limits import Quota
from .leaderboard import BoardError, Leaderboard
from .storage import StorageError, build_storage
from .youtube import YouTubeError, YouTubeSearch

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
VISITOR_HEADER = "x-atlas-visitor"
ADMIN_HEADER = "x-atlas-admin"

app = FastAPI(title="Mindora backend", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "PUT", "POST", "DELETE", "OPTIONS"],
    allow_headers=["content-type", VISITOR_HEADER, "x-atlas-code", ADMIN_HEADER],
    expose_headers=[VISITOR_HEADER],
)

quota = Quota(
    settings.db_path,
    daily_cap=settings.daily_request_cap,
    visitor_daily_cap=settings.per_visitor_daily_cap,
    per_minute_cap=settings.per_minute_cap,
)
store = build_storage(settings)
billing = Billing(settings.db_path, access_code=settings.free_access_code)
board = Leaderboard(settings.db_path)
feedback = Feedback(settings.db_path, daily_cap=settings.feedback_daily_cap)
youtube = YouTubeSearch(
    settings.db_path,
    settings.youtube_key,
    daily_cap=settings.yt_daily_searches,
    visitor_daily_cap=settings.yt_visitor_daily_searches,
)

# --- surviving a disk-less host -----------------------------------------
# Free tiers give no persistent disk, so the SQLite file vanishes whenever the
# instance sleeps or redeploys. Mirroring the billing tables into the durable
# storage the app already has (Google Drive) means a wiped disk costs nothing —
# without it, everyone who paid would silently lose their subscription.
BILLING_BACKUP_KEY = "billing-backup"
BOARD_BACKUP_KEY = "leaderboard-backup"
FEEDBACK_BACKUP_KEY = "feedback-backup"


def snapshot_billing() -> None:
    """Mirror billing state to durable storage. Never raises.

    A failed backup must not fail the purchase that triggered it: the customer
    has already sent money, and the local database is still correct.
    """
    try:
        store.save(BILLING_BACKUP_KEY, billing.export_state())
    except Exception as exc:                                  # noqa: BLE001
        print(f"[billing] backup failed: {exc}", flush=True)


def restore_billing() -> None:
    """Refill an empty database from the last snapshot, once, at startup."""
    try:
        if not billing.is_empty():
            return
        data = store.load(BILLING_BACKUP_KEY)
        if not data:
            return
        rows = billing.import_state(data)
        if rows:
            print(f"[billing] restored {rows} rows after a disk reset", flush=True)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[billing] restore failed: {exc}", flush=True)


def snapshot_board() -> None:
    """Same deal as billing: a free tier wipes the disk, and a leaderboard
    that forgets everyone on every redeploy is not a leaderboard."""
    try:
        store.save(BOARD_BACKUP_KEY, board.export_state())
    except Exception as exc:                                  # noqa: BLE001
        print(f"[board] backup failed: {exc}", flush=True)


def restore_board() -> None:
    try:
        if not board.is_empty():
            return
        data = store.load(BOARD_BACKUP_KEY)
        if data:
            board.import_state(data)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[board] restore failed: {exc}", flush=True)


def snapshot_feedback() -> None:
    """Feedback is the system of record for what people told you; losing it to
    a disk reset would be worse than losing a leaderboard row."""
    try:
        store.save(FEEDBACK_BACKUP_KEY, feedback.export_state())
    except Exception as exc:                                  # noqa: BLE001
        print(f"[feedback] backup failed: {exc}", flush=True)


def restore_feedback() -> None:
    try:
        if not feedback.is_empty():
            return
        data = store.load(FEEDBACK_BACKUP_KEY)
        if data:
            feedback.import_state(data)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[feedback] restore failed: {exc}", flush=True)


@app.on_event("startup")
def _restore_on_boot() -> None:
    restore_billing()
    restore_board()
    restore_feedback()


def _secret() -> str:
    if not settings.secret_key:
        # Without this, visitor tokens can't be signed and anyone could read
        # anyone's data by guessing an id. Refuse rather than run insecurely.
        raise HTTPException(500, "Server misconfigured: SECRET_KEY is not set.")
    return settings.secret_key


def resolve_visitor(token: str | None) -> tuple[str, str]:
    """Return (visitor_id, token_to_return). Issues a new identity if needed."""
    existing = verify(token or "", _secret())
    if existing:
        return existing, sign(existing, _secret())
    fresh = new_visitor_id()
    return fresh, sign(fresh, _secret())


def check_access_code(provided: str | None) -> None:
    if settings.access_code and (provided or "") != settings.access_code:
        raise HTTPException(401, "This app needs an access code.")


def require_admin(token: str | None) -> None:
    """Owner-only endpoints. Without a configured token they stay shut.

    Defaulting to closed matters: an admin API that is open when unconfigured
    would let anyone approve their own payment the moment the server deploys.
    """
    if not settings.admin_token:
        raise HTTPException(503, "Admin API is disabled — set ADMIN_TOKEN to enable it.")
    if not hmac.compare_digest(token or "", settings.admin_token):
        raise HTTPException(401, "Not authorised.")


def subscription_gate(visitor: str) -> bool:
    """Refuse chat unless this visitor has paid, redeemed a code, or has trial
    messages left. This is the paywall — the client-side one is only cosmetic.

    Returns True when the request is being served from the free trial, so the
    caller knows to burn one.
    """
    # Open to everyone. Checked first so no trial is consumed and no
    # entitlement lookup is needed — the spend caps still apply, so the
    # owner's account stays protected.
    if settings.free_for_all:
        return False
    if billing.entitlement(visitor).active:
        return False
    if settings.free_trial_messages and billing.trial_used(visitor) < settings.free_trial_messages:
        return True
    raise HTTPException(
        402,
        "The Mindora assistant is part of a subscription. Subscribe, or enter your free-access code.",
    )


class ChatRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    system: str | list | None = None
    model: str | None = None
    effort: str | None = None
    tools: bool = True


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "chat": settings.chat_enabled,
        "storage": store.backend,
        "quota": quota.stats(),
        # Whether the server can find videos itself. The client uses this to
        # decide it needs nothing from the visitor — it is a capability flag,
        # never the key.
        "youtube": youtube.enabled,
        # Whether this server will accept feedback at all. The client hides
        # the widget when it won't, rather than dropping messages silently.
        "feedback": True,
        "accessCode": bool(settings.access_code),
        "billing": {
            # freeForAll means nothing is *required*. The rails stay enabled so
            # the client can still offer a voluntary way to pay; what it must
            # not do is gate anything or ask.
            "freeForAll": settings.free_for_all,
            "enabled": bool(settings.pay_accounts),
            "currency": settings.currency,
            "trialMessages": settings.free_trial_messages,
        },
    }


@app.post("/api/chat")
def chat(
    body: ChatRequest,
    request: Request,
    x_atlas_visitor: str | None = Header(default=None),
    x_atlas_code: str | None = Header(default=None),
):
    if not settings.chat_enabled:
        raise HTTPException(503, "Chat is not configured on this server.")
    check_access_code(x_atlas_code)
    visitor, token = resolve_visitor(x_atlas_visitor)
    on_trial = subscription_gate(visitor)

    verdict = quota.check(visitor)
    if not verdict.allowed:
        return JSONResponse(
            {"error": {"message": verdict.reason}},
            status_code=429,
            headers={"Retry-After": str(verdict.retry_after), VISITOR_HEADER: token},
        )
    if not body.messages:
        raise HTTPException(400, "messages is required.")

    # The client never chooses the model or token budget directly — it asks,
    # and anything outside the allowlist falls back to the cheap default.
    model = body.model if body.model in settings.allowed_models else settings.default_model
    payload: dict = {
        "model": model,
        "max_tokens": settings.max_tokens,
        "stream": True,
        "messages": body.messages,
    }
    if body.system:
        payload["system"] = body.system
    if body.effort in ("low", "medium", "high", "xhigh", "max"):
        payload["output_config"] = {"effort": body.effort}
    if body.tools:
        payload["tools"] = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 3},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 2},
        ]

    quota.consume(visitor)
    if on_trial:
        billing.consume_trial(visitor)

    def stream():
        try:
            with requests.post(
                ANTHROPIC_URL, timeout=300, stream=True, json=payload,
                headers={
                    "content-type": "application/json",
                    "x-api-key": settings.anthropic_key,
                    "anthropic-version": "2023-06-01",
                },
            ) as upstream:
                if upstream.status_code != 200:
                    detail = upstream.text[:500]
                    yield f"event: error\ndata: {json.dumps({'error': {'message': f'Upstream error {upstream.status_code}: {detail}'}})}\n\n"
                    return
                for chunk in upstream.iter_content(chunk_size=None):
                    if chunk:
                        yield chunk
        except requests.RequestException as exc:
            yield f"event: error\ndata: {json.dumps({'error': {'message': f'Upstream connection failed: {exc}'}})}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={VISITOR_HEADER: token, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- billing -------------------------------------------------------------


@app.get("/api/billing/config")
def billing_config():
    """Everything the checkout screen needs. Deliberately contains no secrets:
    the receiving account numbers are meant to be shown to buyers."""
    accounts = settings.pay_accounts
    return {
        "currency": settings.currency,
        "plans": list(settings.plans.values()),
        "methods": [
            {"id": key, **METHODS[key], "account": accounts[key],
             "link": settings.pay_links.get(key, "")}
            for key in METHODS if key in accounts
        ],
        "trialMessages": settings.free_trial_messages,
        "codeEnabled": bool(settings.free_access_code),
        "freeForAll": settings.free_for_all,
    }


@app.get("/api/billing/entitlement")
def get_entitlement(response: Response, x_atlas_visitor: str | None = Header(default=None)):
    visitor, token = resolve_visitor(x_atlas_visitor)
    response.headers[VISITOR_HEADER] = token
    data = billing.entitlement(visitor).as_dict()
    data["trialRemaining"] = max(0, settings.free_trial_messages - billing.trial_used(visitor))
    data["freeForAll"] = settings.free_for_all
    return data


class RedeemBody(BaseModel):
    code: str = ""


@app.post("/api/billing/redeem")
def redeem(body: RedeemBody, response: Response, x_atlas_visitor: str | None = Header(default=None)):
    visitor, token = resolve_visitor(x_atlas_visitor)
    response.headers[VISITOR_HEADER] = token
    ok, message = billing.redeem(visitor, body.code)
    if ok:
        snapshot_billing()
    if not ok:
        return JSONResponse(
            {"ok": False, "message": message}, status_code=400, headers={VISITOR_HEADER: token}
        )
    return {"ok": True, "message": message, "entitlement": billing.entitlement(visitor).as_dict()}


class ClaimBody(BaseModel):
    plan: str
    method: str
    reference: str
    sender: str = ""


@app.post("/api/billing/claim")
def claim(body: ClaimBody, response: Response, x_atlas_visitor: str | None = Header(default=None)):
    visitor, token = resolve_visitor(x_atlas_visitor)
    response.headers[VISITOR_HEADER] = token

    plan = settings.plans.get(body.plan)
    if not plan:
        raise HTTPException(400, "Unknown plan.")
    if body.method not in METHODS or body.method not in settings.pay_accounts:
        raise HTTPException(400, "That payment method isn't available.")

    # The amount comes from the server's price list, never from the client —
    # otherwise a buyer could claim to have paid one taka for a year.
    ok, payment_id, message = billing.claim(
        visitor,
        plan=body.plan,
        method=body.method,
        amount_minor=plan["price"] * 100,
        currency=plan["currency"],
        reference=body.reference,
        sender=body.sender,
    )
    if not ok:
        return JSONResponse(
            {"ok": False, "message": message}, status_code=400, headers={VISITOR_HEADER: token}
        )
    snapshot_billing()
    return {"ok": True, "id": payment_id, "message": message,
            "entitlement": billing.entitlement(visitor).as_dict()}


# --- owner-only ----------------------------------------------------------


@app.get("/api/admin/summary")
def admin_summary(x_atlas_admin: str | None = Header(default=None)):
    require_admin(x_atlas_admin)
    return {"billing": billing.summary(), "quota": quota.stats()}


@app.get("/api/admin/payments")
def admin_payments(status: str = "", x_atlas_admin: str | None = Header(default=None)):
    require_admin(x_atlas_admin)
    return {"payments": billing.payments(status=status)}


class ReviewBody(BaseModel):
    action: str          # "approve" | "reject"
    note: str = ""


@app.post("/api/admin/payments/{payment_id}")
def admin_review(payment_id: str, body: ReviewBody, x_atlas_admin: str | None = Header(default=None)):
    require_admin(x_atlas_admin)
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject.")

    records = [p for p in billing.payments(limit=1000) if p["id"] == payment_id]
    if not records:
        raise HTTPException(404, "No such payment.")
    plan = settings.plans.get(records[0]["plan"])
    days = plan["days"] if plan else 30

    ok, message = billing.review(
        payment_id, approve=body.action == "approve", days=days, note=body.note
    )
    if not ok:
        raise HTTPException(409, message)
    snapshot_billing()
    return {"ok": True, "message": message}


class BoardBody(BaseModel):
    name: str = ""
    xp: int = 0
    level: int = 1
    lessons: int = 0
    streak: int = 0


class FeedbackBody(BaseModel):
    message: str = ""
    kind: str = "other"
    contact: str = ""
    meta: dict = Field(default_factory=dict)
    # Honeypot. A real person never sees this field, so anything in it came
    # from something filling every input on the page.
    website: str = ""


@app.post("/api/feedback")
def submit_feedback(
    body: FeedbackBody,
    background: BackgroundTasks,
    response: Response,
    x_atlas_visitor: str | None = Header(default=None),
    x_atlas_code: str | None = Header(default=None),
):
    """Take a message, store it, then try to email it.

    The store happens first and the send is queued behind the response, so a
    slow or broken mail transport never makes the person wait and never makes
    them think their message was lost.
    """
    check_access_code(x_atlas_code)
    visitor, token = resolve_visitor(x_atlas_visitor)
    response.headers[VISITOR_HEADER] = token
    if body.website.strip():
        # Silently accept and discard: telling a bot it was caught only helps
        # it try again differently.
        return {"ok": True}
    meta = {k: str(v)[:120] for k, v in list((body.meta or {}).items())[:8]}
    try:
        entry = feedback.record(visitor, kind=body.kind, message=body.message,
                                contact=body.contact, meta=meta)
    except FeedbackError as exc:
        raise HTTPException(400, str(exc))

    def deliver() -> None:
        # Belt and braces around a send that already catches its own transport
        # errors: this runs after the response, so anything escaping here is an
        # unhandled exception in a background task, and the person has already
        # been told their message arrived — which it did. It is in the store.
        try:
            if send_email(settings, entry):
                feedback.mark_mailed(entry["id"])
        except Exception as exc:                              # noqa: BLE001
            print(f"[feedback] delivery failed for {entry['id']}: {exc}", flush=True)
        try:
            snapshot_feedback()
        except Exception:                                     # noqa: BLE001
            pass

    background.add_task(deliver)
    return {"ok": True, "id": entry["id"]}


@app.get("/api/admin/feedback")
def admin_feedback(limit: int = 50, x_atlas_admin: str | None = Header(default=None)):
    """Everything people have sent, whether or not the mail got through."""
    require_admin(x_atlas_admin)
    return {
        "items": feedback.recent(limit),
        "total": feedback.count(),
        "mailTo": settings.feedback_to or "",
        "mailConfigured": bool(settings.feedback_to
                               and (settings.resend_api_key or settings.smtp_host)),
    }


@app.get("/api/leaderboard")
def leaderboard(
    limit: int = 25,
    response: Response = None,
    x_atlas_visitor: str | None = Header(default=None),
    x_atlas_code: str | None = Header(default=None),
):
    """The top of the board, plus this visitor's own row if they're on it."""
    check_access_code(x_atlas_code)
    visitor, token = resolve_visitor(x_atlas_visitor)
    if response is not None:
        response.headers[VISITOR_HEADER] = token
    return {"top": board.top(limit), "me": board.me(visitor), "size": board.size()}


@app.post("/api/leaderboard")
def leaderboard_join(
    body: BoardBody,
    response: Response,
    x_atlas_visitor: str | None = Header(default=None),
    x_atlas_code: str | None = Header(default=None),
):
    """Join the board, or update an entry already on it.

    The score arrives from the browser and cannot be verified here — see the
    module docstring. It is bounded, not trusted.
    """
    check_access_code(x_atlas_code)
    visitor, token = resolve_visitor(x_atlas_visitor)
    response.headers[VISITOR_HEADER] = token
    try:
        me = board.submit(visitor, name=body.name, xp=body.xp, level=body.level,
                          lessons=body.lessons, streak=body.streak)
    except BoardError as exc:
        raise HTTPException(400, str(exc))
    snapshot_board()
    return {"me": me, "top": board.top(25), "size": board.size()}


@app.delete("/api/leaderboard")
def leaderboard_leave(
    response: Response,
    x_atlas_visitor: str | None = Header(default=None),
    x_atlas_code: str | None = Header(default=None),
):
    """Leave the board. Opting in has to be reversible."""
    check_access_code(x_atlas_code)
    visitor, token = resolve_visitor(x_atlas_visitor)
    response.headers[VISITOR_HEADER] = token
    board.leave(visitor)
    snapshot_board()
    return {"ok": True}


@app.get("/api/yt/search")
def yt_search(
    q: str = "",
    response: Response = None,
    x_atlas_visitor: str | None = Header(default=None),
    x_atlas_code: str | None = Header(default=None),
):
    """Find a video for a lesson title, using the owner's YouTube key.

    Free — this is not the paid feature and costs nothing on the Anthropic
    account. It has its own caps because YouTube's quota is far tighter than
    it looks: a search costs 100 of 10,000 daily units.
    """
    check_access_code(x_atlas_code)
    visitor, token = resolve_visitor(x_atlas_visitor)
    if response is not None:
        response.headers[VISITOR_HEADER] = token
    if not youtube.enabled:
        raise HTTPException(503, "This server has no YouTube key configured.")
    try:
        return youtube.search(q, visitor)
    except YouTubeError as exc:
        # 429 rather than 500: the caller did nothing wrong and retrying
        # tomorrow is the actual remedy.
        raise HTTPException(429, str(exc))


@app.get("/api/yt/stats")
def yt_stats(x_atlas_admin: str | None = Header(default=None)):
    """Owner-only: what the cache has saved and what today has cost."""
    require_admin(x_atlas_admin)
    return youtube.stats()


@app.get("/api/state")
def get_state(response: Response, x_atlas_visitor: str | None = Header(default=None)):
    visitor, token = resolve_visitor(x_atlas_visitor)
    response.headers[VISITOR_HEADER] = token
    try:
        data = store.load(storage_key(visitor))
    except StorageError as exc:
        raise HTTPException(502, str(exc))
    return {"state": data or {}, "storage": store.backend}


class StateBody(BaseModel):
    state: dict = Field(default_factory=dict)


@app.put("/api/state")
def put_state(body: StateBody, response: Response, x_atlas_visitor: str | None = Header(default=None)):
    visitor, token = resolve_visitor(x_atlas_visitor)
    response.headers[VISITOR_HEADER] = token
    # A runaway client shouldn't be able to fill the owner's Drive.
    encoded = json.dumps(body.state)
    if len(encoded) > 2_000_000:
        raise HTTPException(413, "That's too much data to store (2 MB limit).")
    try:
        store.save(storage_key(visitor), body.state)
    except StorageError as exc:
        raise HTTPException(502, str(exc))
    return {"ok": True, "storage": store.backend}
