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

import json

import requests
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings
from .identity import new_visitor_id, sign, storage_key, verify
from .limits import Quota
from .storage import StorageError, build_storage

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
VISITOR_HEADER = "x-atlas-visitor"

app = FastAPI(title="Mindora backend", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "PUT", "POST", "OPTIONS"],
    allow_headers=["content-type", VISITOR_HEADER, "x-atlas-code"],
    expose_headers=[VISITOR_HEADER],
)

quota = Quota(
    settings.db_path,
    daily_cap=settings.daily_request_cap,
    visitor_daily_cap=settings.per_visitor_daily_cap,
    per_minute_cap=settings.per_minute_cap,
)
store = build_storage(settings)


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
        "accessCode": bool(settings.access_code),
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
