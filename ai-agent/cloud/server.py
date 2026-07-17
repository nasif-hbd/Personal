"""
Cloud orchestrator ("the brain") for the AI agent system.

Local per-OS agents (see ../agents/) poll this server for commands (launch an
app, show a notification, ...) and report results back. The server also
exposes chat (via the Claude API), email, and generic webhook endpoints, and
runs a tiny in-process scheduler for recurring commands.

Run:
    pip install -r requirements.txt
    export AGENT_API_KEY=change-me
    export ANTHROPIC_API_KEY=sk-ant-...
    uvicorn server:app --reload --port 8000
"""
import asyncio
import json
import os
import smtplib
import time
from email.mime.text import MIMEText
from typing import Literal

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db

API_KEY = os.environ.get("AGENT_API_KEY", "")
AGENT_ONLINE_WINDOW_SECONDS = 30
CLAUDE_MODEL = "claude-opus-4-8"

app = FastAPI(title="AI Agent Cloud Orchestrator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your dashboard's origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not API_KEY:
        raise HTTPException(500, "Server misconfigured: AGENT_API_KEY is not set")
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key header")


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()
    asyncio.create_task(scheduler_loop())


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

class RegisterAgent(BaseModel):
    agent_id: str
    os: str
    name: str


@app.post("/agents/register", dependencies=[Depends(require_api_key)])
def register_agent(body: RegisterAgent):
    now = time.time()
    existing = db.query_one("SELECT id FROM agents WHERE id = ?", (body.agent_id,))
    if existing:
        db.execute(
            "UPDATE agents SET os = ?, name = ?, last_seen = ? WHERE id = ?",
            (body.os, body.name, now, body.agent_id),
        )
    else:
        db.execute(
            "INSERT INTO agents (id, os, name, last_seen, created_at) VALUES (?, ?, ?, ?, ?)",
            (body.agent_id, body.os, body.name, now, now),
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
    row = db.query_one(
        "SELECT * FROM commands WHERE agent_id = ? AND status = 'queued' ORDER BY created_at LIMIT 1",
        (agent_id,),
    )
    if not row:
        return {"command": None}
    db.execute("UPDATE commands SET status = 'in_progress', updated_at = ? WHERE id = ?", (time.time(), row["id"]))
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
# Commands (launch_app, notify, custom — the agent-side vocabulary)
# --------------------------------------------------------------------------

class CreateCommand(BaseModel):
    agent_id: str
    type: Literal["launch_app", "notify", "custom"]
    payload: dict


@app.post("/commands", dependencies=[Depends(require_api_key)])
def create_command(body: CreateCommand):
    now = time.time()
    cur = db.execute(
        "INSERT INTO commands (agent_id, type, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (body.agent_id, body.type, json.dumps(body.payload), now, now),
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


# --------------------------------------------------------------------------
# Schedules — simple recurring commands, checked by the background loop
# --------------------------------------------------------------------------

class CreateSchedule(BaseModel):
    agent_id: str
    type: Literal["launch_app", "notify", "custom"]
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
# Generic inbound webhooks — external services (GitHub, Slack, cron pingers,
# ...) can POST here. The payload is stored and, optionally, summarized by
# Claude and turned into a notification for a chosen agent.
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


@app.get("/health")
def health():
    return {"status": "ok", "time": time.time()}
