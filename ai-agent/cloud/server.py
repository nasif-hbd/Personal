"""
Cloud orchestrator ("the brain") for the AI agent system.

Local per-OS agents (see ../agents/) poll this server for commands (launch an
app, show a notification, capture a screenshot, ...) and report results back.
The server also exposes chat (via the Claude API), email, SMS/calls, a
Slack/Discord bridge, a small file store, scheduling, broadcast targeting,
and an audit log.

Run:
    pip install -r requirements.txt
    export AGENT_API_KEY=change-me
    export ANTHROPIC_API_KEY=sk-ant-...
    uvicorn server:app --reload --port 8000
"""
import asyncio
import base64
import json
import logging
import os
import smtplib
import time
import urllib.request
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from email.mime.text import MIMEText
from typing import Literal

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db

log = logging.getLogger("agent.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

API_KEY = os.environ.get("AGENT_API_KEY", "")
AGENT_ONLINE_WINDOW_SECONDS = 30
CLAUDE_MODEL = "claude-opus-4-8"
RATE_LIMIT_REQUESTS = 120
RATE_LIMIT_WINDOW_SECONDS = 60
ALLOWED_ORIGIN = os.environ.get("DASHBOARD_ORIGIN", "*")

CommandType = Literal[
    "launch_app", "notify", "get_clipboard", "set_clipboard",
    "speak", "screenshot", "write_file", "run_command", "custom",
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if not API_KEY:
        log.warning("AGENT_API_KEY is not set — every authenticated endpoint will 500 until you set it.")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.warning("ANTHROPIC_API_KEY is not set — /chat will 500 until you set it.")
    if ALLOWED_ORIGIN == "*":
        log.warning("CORS allow_origins is '*' (dev default) — set DASHBOARD_ORIGIN before exposing this publicly.")
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title="AI Agent Cloud Orchestrator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # Defaults to "*" for local dev. Set DASHBOARD_ORIGIN before exposing this
    # server publicly — see README.md → Security notes.
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

_rate_buckets: dict[str, deque] = defaultdict(deque)


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not API_KEY:
        raise HTTPException(500, "Server misconfigured: AGENT_API_KEY is not set")
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key header")
    # Sliding-window rate limit, per API key.
    now = time.time()
    bucket = _rate_buckets[x_api_key]
    bucket.append(now)
    while bucket and bucket[0] < now - RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) > RATE_LIMIT_REQUESTS:
        raise HTTPException(429, f"Rate limit exceeded — max {RATE_LIMIT_REQUESTS} requests/minute per API key")


def _post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10).read()


def resolve_targets(selector: str) -> list[str]:
    """agent_id selector for broadcast: 'all', 'os:<name>', 'tag:<name>', or a literal agent id."""
    agents = db.query("SELECT * FROM agents")
    if selector == "all":
        return [a["id"] for a in agents]
    if selector.startswith("os:"):
        osname = selector.split(":", 1)[1]
        return [a["id"] for a in agents if a["os"] == osname]
    if selector.startswith("tag:"):
        tag = selector.split(":", 1)[1]
        return [a["id"] for a in agents if tag in (a["tags"] or "").split(",")]
    return [selector]


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

class RegisterAgent(BaseModel):
    agent_id: str
    os: str
    name: str
    tags: str = ""  # comma-separated, e.g. "home,laptop" — used by broadcast targeting


@app.post("/agents/register", dependencies=[Depends(require_api_key)])
def register_agent(body: RegisterAgent):
    now = time.time()
    existing = db.query_one("SELECT id FROM agents WHERE id = ?", (body.agent_id,))
    if existing:
        db.execute(
            "UPDATE agents SET os = ?, name = ?, tags = ?, last_seen = ? WHERE id = ?",
            (body.os, body.name, body.tags, now, body.agent_id),
        )
    else:
        db.execute(
            "INSERT INTO agents (id, os, name, tags, last_seen, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (body.agent_id, body.os, body.name, body.tags, now, now),
        )
    return {"status": "registered", "agent_id": body.agent_id}


@app.get("/agents", dependencies=[Depends(require_api_key)])
def list_agents():
    now = time.time()
    agents = db.query("SELECT * FROM agents ORDER BY name")
    for a in agents:
        a["online"] = bool(a["last_seen"]) and (now - a["last_seen"]) < AGENT_ONLINE_WINDOW_SECONDS
    return agents


@app.get("/agents/{agent_id}/poll", dependencies=[Depends(require_api_key)])
def poll_commands(agent_id: str):
    db.execute("UPDATE agents SET last_seen = ? WHERE id = ?", (time.time(), agent_id))
    now = time.time()
    while True:
        row = db.query_one(
            "SELECT * FROM commands WHERE agent_id = ? AND status = 'queued' ORDER BY created_at LIMIT 1",
            (agent_id,),
        )
        if not row:
            return {"command": None}
        # A command with an expired TTL never fires late — mark it and move on.
        if row["expires_at"] and row["expires_at"] < now:
            db.execute("UPDATE commands SET status = 'expired', updated_at = ? WHERE id = ?", (now, row["id"]))
            continue
        db.execute("UPDATE commands SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, row["id"]))
        return {"command": {"id": row["id"], "type": row["type"], "payload": json.loads(row["payload"])}}


class AckCommand(BaseModel):
    command_id: int
    status: Literal["done", "failed"]
    result: str = ""


@app.post("/agents/{agent_id}/ack", dependencies=[Depends(require_api_key)])
def ack_command(agent_id: str, body: AckCommand):
    db.execute(
        "UPDATE commands SET status = ?, result = ?, updated_at = ? WHERE id = ? AND agent_id = ?",
        (body.status, body.result, time.time(), body.command_id, agent_id),
    )
    return {"status": "ok"}


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

class CreateCommand(BaseModel):
    agent_id: str
    type: CommandType
    payload: dict
    ttl_seconds: int | None = None


@app.post("/commands", dependencies=[Depends(require_api_key)])
def create_command(body: CreateCommand):
    now = time.time()
    expires_at = now + body.ttl_seconds if body.ttl_seconds else None
    cur = db.execute(
        "INSERT INTO commands (agent_id, type, payload, expires_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (body.agent_id, body.type, json.dumps(body.payload), expires_at, now, now),
    )
    return {"command_id": cur.lastrowid}


@app.get("/commands", dependencies=[Depends(require_api_key)])
def list_commands(agent_id: str | None = None, limit: int = 50):
    if agent_id:
        rows = db.query(
            "SELECT * FROM commands WHERE agent_id = ? ORDER BY id DESC LIMIT ?", (agent_id, limit)
        )
    else:
        rows = db.query("SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,))
    return rows


@app.post("/commands/{command_id}/retry", dependencies=[Depends(require_api_key)])
def retry_command(command_id: int):
    row = db.query_one("SELECT * FROM commands WHERE id = ?", (command_id,))
    if not row:
        raise HTTPException(404, "command not found")
    if row["status"] != "failed":
        raise HTTPException(400, f"can only retry failed commands (current status: {row['status']})")
    db.execute(
        "UPDATE commands SET status = 'queued', result = NULL, retries = retries + 1, updated_at = ? WHERE id = ?",
        (time.time(), command_id),
    )
    return {"status": "requeued", "retries": row["retries"] + 1}


class BroadcastCommand(BaseModel):
    target: str  # "all" | "os:<name>" | "tag:<name>" | a literal agent id
    type: CommandType
    payload: dict
    ttl_seconds: int | None = None


@app.post("/commands/broadcast", dependencies=[Depends(require_api_key)])
def broadcast_command(body: BroadcastCommand):
    targets = resolve_targets(body.target)
    if not targets:
        raise HTTPException(404, f"no agents matched target '{body.target}'")
    now = time.time()
    expires_at = now + body.ttl_seconds if body.ttl_seconds else None
    command_ids = []
    for agent_id in targets:
        cur = db.execute(
            "INSERT INTO commands (agent_id, type, payload, expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (agent_id, body.type, json.dumps(body.payload), expires_at, now, now),
        )
        command_ids.append(cur.lastrowid)
    return {"command_ids": command_ids, "targeted_agents": targets}


# --------------------------------------------------------------------------
# Chat (Claude API)
# --------------------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str = "default"
    message: str


@app.post("/chat", dependencies=[Depends(require_api_key)])
def chat(body: ChatRequest):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY is not configured on the server")

    now = time.time()
    db.execute(
        "INSERT INTO chats (session_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
        (body.session_id, body.message, now),
    )

    history = db.query(
        "SELECT role, content FROM chats WHERE session_id = ? ORDER BY id DESC LIMIT 20",
        (body.session_id,),
    )
    history.reverse()
    messages = [{"role": h["role"], "content": h["content"]} for h in history]

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system="You are the assistant embedded in a personal automation dashboard. Be concise.",
        messages=messages,
    )
    reply = "".join(block.text for block in response.content if block.type == "text")

    db.execute(
        "INSERT INTO chats (session_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
        (body.session_id, reply, time.time()),
    )
    return {"reply": reply}


@app.get("/chat/{session_id}", dependencies=[Depends(require_api_key)])
def chat_history(session_id: str):
    return db.query(
        "SELECT role, content, created_at FROM chats WHERE session_id = ? ORDER BY id", (session_id,)
    )


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

class SendEmail(BaseModel):
    to: str
    subject: str
    body: str


@app.post("/email/send", dependencies=[Depends(require_api_key)])
def send_email(body: SendEmail):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    sender = os.environ.get("SMTP_FROM", user)
    if not all([host, user, password, sender]):
        raise HTTPException(500, "SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_FROM are not configured")

    msg = MIMEText(body.body)
    msg["Subject"] = body.subject
    msg["From"] = sender
    msg["To"] = body.to

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(sender, [body.to], msg.as_string())
    return {"status": "sent"}


# --------------------------------------------------------------------------
# Calls / SMS (Twilio — optional, only active if configured and installed)
# --------------------------------------------------------------------------

class SendSMS(BaseModel):
    to: str
    body: str


@app.post("/sms/send", dependencies=[Depends(require_api_key)])
def send_sms(body: SendSMS):
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not all([sid, token, from_number]):
        raise HTTPException(501, "Twilio is not configured (set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER)")
    try:
        from twilio.rest import Client  # lazy import — optional dependency
    except ImportError:
        raise HTTPException(501, "Twilio SDK not installed — run: pip install twilio")

    client = Client(sid, token)
    message = client.messages.create(to=body.to, from_=from_number, body=body.body)
    return {"status": "sent", "sid": message.sid}


class MakeCall(BaseModel):
    to: str
    say: str


@app.post("/calls/make", dependencies=[Depends(require_api_key)])
def make_call(body: MakeCall):
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM_NUMBER")
    if not all([sid, token, from_number]):
        raise HTTPException(501, "Twilio is not configured (set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER)")
    try:
        from twilio.rest import Client  # lazy import — optional dependency
    except ImportError:
        raise HTTPException(501, "Twilio SDK not installed — run: pip install twilio")

    client = Client(sid, token)
    twiml = f"<Response><Say>{body.say}</Say></Response>"
    call = client.calls.create(to=body.to, from_=from_number, twiml=twiml)
    return {"status": "calling", "sid": call.sid}


class ExternalNotify(BaseModel):
    message: str
    title: str | None = None


@app.post("/notify/external", dependencies=[Depends(require_api_key)])
def notify_external(body: ExternalNotify):
    """Push straight to Slack/Discord — no device agent involved."""
    slack_url = os.environ.get("SLACK_WEBHOOK_URL")
    discord_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not slack_url and not discord_url:
        raise HTTPException(501, "Set SLACK_WEBHOOK_URL and/or DISCORD_WEBHOOK_URL to use this endpoint")
    text = f"*{body.title}*\n{body.message}" if body.title else body.message
    sent = []
    if slack_url:
        _post_json(slack_url, {"text": text})
        sent.append("slack")
    if discord_url:
        _post_json(discord_url, {"content": text})
        sent.append("discord")
    return {"status": "sent", "channels": sent}


# --------------------------------------------------------------------------
# Schedules — simple recurring commands, checked by the background loop
# --------------------------------------------------------------------------

class CreateSchedule(BaseModel):
    agent_id: str
    type: CommandType
    payload: dict
    interval_seconds: int


@app.post("/schedules", dependencies=[Depends(require_api_key)])
def create_schedule(body: CreateSchedule):
    now = time.time()
    cur = db.execute(
        "INSERT INTO schedules (agent_id, type, payload, interval_seconds, next_run_at) VALUES (?, ?, ?, ?, ?)",
        (body.agent_id, body.type, json.dumps(body.payload), body.interval_seconds, now + body.interval_seconds),
    )
    return {"schedule_id": cur.lastrowid}


@app.get("/schedules", dependencies=[Depends(require_api_key)])
def list_schedules():
    return db.query("SELECT * FROM schedules ORDER BY id DESC")


@app.delete("/schedules/{schedule_id}", dependencies=[Depends(require_api_key)])
def delete_schedule(schedule_id: int):
    db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
    return {"status": "deleted"}


async def scheduler_loop() -> None:
    while True:
        now = time.time()
        due = db.query("SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ?", (now,))
        for s in due:
            db.execute(
                "INSERT INTO commands (agent_id, type, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (s["agent_id"], s["type"], s["payload"], now, now),
            )
            db.execute(
                "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                (now + s["interval_seconds"], s["id"]),
            )
        await asyncio.sleep(15)


# --------------------------------------------------------------------------
# Generic inbound webhooks
# --------------------------------------------------------------------------

@app.post("/webhooks/{name}", dependencies=[Depends(require_api_key)])
async def inbound_webhook(name: str, body: dict, notify_agent_id: str | None = None):
    db.execute(
        "INSERT INTO events (source, payload, created_at) VALUES (?, ?, ?)",
        (name, json.dumps(body), time.time()),
    )
    if notify_agent_id:
        summary = json.dumps(body)[:500]
        now = time.time()
        db.execute(
            "INSERT INTO commands (agent_id, type, payload, created_at, updated_at) VALUES (?, 'notify', ?, ?, ?)",
            (notify_agent_id, json.dumps({"title": f"Webhook: {name}", "message": summary}), now, now),
        )
    return {"status": "received"}


# --------------------------------------------------------------------------
# Files — screenshots, remote-file-write, or arbitrary uploads land here
# --------------------------------------------------------------------------

class FileUpload(BaseModel):
    agent_id: str
    filename: str
    content_type: str = "application/octet-stream"
    content_base64: str


@app.post("/files/upload", dependencies=[Depends(require_api_key)])
def upload_file(body: FileUpload):
    data = base64.b64decode(body.content_base64)
    now = time.time()
    cur = db.execute(
        "INSERT INTO files (agent_id, filename, content, content_type, size_bytes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (body.agent_id, body.filename, data, body.content_type, len(data), now),
    )
    return {"file_id": cur.lastrowid, "size_bytes": len(data)}


@app.get("/files", dependencies=[Depends(require_api_key)])
def list_files(agent_id: str | None = None):
    cols = "id, agent_id, filename, content_type, size_bytes, created_at"
    if agent_id:
        return db.query(f"SELECT {cols} FROM files WHERE agent_id = ? ORDER BY id DESC", (agent_id,))
    return db.query(f"SELECT {cols} FROM files ORDER BY id DESC")


@app.get("/files/{file_id}", dependencies=[Depends(require_api_key)])
def download_file(file_id: int):
    row = db.query_one("SELECT * FROM files WHERE id = ?", (file_id,))
    if not row:
        raise HTTPException(404, "file not found")
    return Response(
        content=row["content"],
        media_type=row["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
    )


# --------------------------------------------------------------------------
# Audit log — merges commands + chats + inbound webhook events by time
# --------------------------------------------------------------------------

@app.get("/audit", dependencies=[Depends(require_api_key)])
def audit_log(limit: int = 100):
    commands = db.query(
        "SELECT id, agent_id, type, status, created_at FROM commands ORDER BY id DESC LIMIT ?", (limit,)
    )
    chats = db.query(
        "SELECT id, session_id, role, created_at FROM chats ORDER BY id DESC LIMIT ?", (limit,)
    )
    events = db.query(
        "SELECT id, source, created_at FROM events ORDER BY id DESC LIMIT ?", (limit,)
    )
    combined = (
        [{"kind": "command", **c} for c in commands]
        + [{"kind": "chat", **c} for c in chats]
        + [{"kind": "webhook", **e} for e in events]
    )
    combined.sort(key=lambda r: r["created_at"], reverse=True)
    return combined[:limit]


@app.get("/health")
def health():
    return {"status": "ok", "time": time.time()}
