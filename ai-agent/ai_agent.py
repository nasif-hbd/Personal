#!/usr/bin/env python3
"""
AI Agent — single-file bundle
==============================

Everything merged into one organized file:

    1. Storage        — SQLite layer (agents, commands, chats, schedules,
                         events, files)
    2. Cloud server    — FastAPI orchestrator: command queue + broadcast,
                         chat via Claude, email, SMS/calls via Twilio,
                         Slack/Discord bridge, scheduler, webhooks, file
                         store, audit log, rate limiting
    3. Agent base      — shared poll/dispatch loop with a pluggable command
                         registry, used by every local agent
    4. Windows agent    — app launch, notifications, clipboard, TTS,
                         screenshot, file write, gated shell exec
    5. macOS agent      — same command set as Windows
    6. Android agent    — same command set (via Termux); screenshot needs root

iOS has no code section on purpose — Apple's sandbox rules out a background
daemon. See the "iOS" note below.

Usage
-----
    # Cloud orchestrator (needs: pip install fastapi "uvicorn[standard]" anthropic)
    export AGENT_API_KEY=change-me
    export ANTHROPIC_API_KEY=sk-ant-...
    python ai_agent.py server --port 8000

    # Local agent on the machine you want to control (needs: pip install requests)
    python ai_agent.py windows --server http://your-cloud-host:8000 --key change-me --tags home,laptop
    python ai_agent.py macos   --server http://your-cloud-host:8000 --key change-me
    python ai_agent.py android --server http://your-cloud-host:8000 --key change-me   # inside Termux

    # Opt in to remote shell execution (dangerous — only if you trust every
    # holder of the API key):
    python ai_agent.py windows --server ... --key ... --allow-shell

iOS
---
No background daemon is possible under Apple's sandbox. The closest working
equivalent is a Shortcuts automation that polls GET /agents/<id>/poll and
maps `launch_app` -> "Open App", `notify` -> "Show Notification", triggered
by a Personal Automation or a tapped `shortcuts://run-shortcut?name=...`
link. That's a Shortcut you build in the Shortcuts app, not a Python file.

What's new in this version (features added on top of the original core)
-------------------------------------------------------------------------
 1. Clipboard get/set                — new command types, all three OS agents
 2. Text-to-speech ("speak")         — new command type, all three OS agents
 3. Screenshot capture               — new command type, uploads to the cloud file store
 4. Remote file write                — write_file command, base64 payload
 5. Gated remote shell execution     — run_command, opt-in via --allow-shell
 6. Cloud file store                 — /files/upload, /files, /files/{id}
 7. Broadcast commands               — POST /commands/broadcast, target "all" / "os:x" / "tag:x"
 8. Agent tags/grouping              — --tags on agents, used as broadcast targets
 9. Command TTL / auto-expiry        — ttl_seconds on a command; stale ones expire instead of firing late
10. Command retry                    — POST /commands/{id}/retry
11. Per-API-key rate limiting        — sliding window, 120 req/min
12. Consolidated audit log           — GET /audit merges commands + chats + webhook events
13. Slack/Discord notify bridge      — POST /notify/external, no agent required
14. Pluggable command registry       — AgentBase.register_command(), no more fixed if/elif chain
"""
import argparse
import base64
import json
import os
import platform
import smtplib
import sqlite3
import subprocess
import threading
import time
import urllib.request
import uuid
from collections import defaultdict, deque
from email.mime.text import MIMEText
from pathlib import Path
from typing import Callable, Literal


# ============================================================================
# 1. STORAGE — SQLite layer, no ORM
# ============================================================================

DB_PATH = Path(__file__).parent / "agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    os TEXT NOT NULL,
    name TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    last_seen REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    result TEXT,
    retries INTEGER NOT NULL DEFAULT 0,
    expires_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    next_run_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    content BLOB NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at REAL NOT NULL
);
"""

_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row


def db_init() -> None:
    with _lock:
        _conn.executescript(SCHEMA)
        _conn.commit()


def db_execute(query: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        cur = _conn.execute(query, params)
        _conn.commit()
        return cur


def db_query(query_str: str, params: tuple = ()) -> list[dict]:
    with _lock:
        cur = _conn.execute(query_str, params)
        return [dict(row) for row in cur.fetchall()]


def db_query_one(query_str: str, params: tuple = ()) -> dict | None:
    rows = db_query(query_str, params)
    return rows[0] if rows else None


# ============================================================================
# 2. CLOUD SERVER — FastAPI orchestrator
#    (imports are local to build_server_app() so `ai_agent.py windows` etc.
#    don't require fastapi/anthropic to be installed)
# ============================================================================

AGENT_ONLINE_WINDOW_SECONDS = 30
CLAUDE_MODEL = "claude-opus-4-8"

CommandType = Literal[
    "launch_app", "notify", "get_clipboard", "set_clipboard",
    "speak", "screenshot", "write_file", "run_command", "custom",
]

RATE_LIMIT_REQUESTS = 120
RATE_LIMIT_WINDOW_SECONDS = 60


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
    agents = db_query("SELECT * FROM agents")
    if selector == "all":
        return [a["id"] for a in agents]
    if selector.startswith("os:"):
        osname = selector.split(":", 1)[1]
        return [a["id"] for a in agents if a["os"] == osname]
    if selector.startswith("tag:"):
        tag = selector.split(":", 1)[1]
        return [a["id"] for a in agents if tag in (a["tags"] or "").split(",")]
    return [selector]


def build_server_app():
    import asyncio
    import logging
    from contextlib import asynccontextmanager

    import anthropic
    from fastapi import Depends, FastAPI, Header, HTTPException, Response
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel

    log = logging.getLogger("agent.server")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    API_KEY = os.environ.get("AGENT_API_KEY", "")
    ALLOWED_ORIGIN = os.environ.get("DASHBOARD_ORIGIN", "*")
    _rate_buckets: dict[str, deque] = defaultdict(deque)

    def require_api_key(x_api_key: str = Header(default="")) -> None:
        if not API_KEY:
            raise HTTPException(500, "Server misconfigured: AGENT_API_KEY is not set")
        if x_api_key != API_KEY:
            raise HTTPException(401, "Invalid or missing X-API-Key header")
        # feature 11: per-API-key sliding-window rate limit
        now = time.time()
        bucket = _rate_buckets[x_api_key]
        bucket.append(now)
        while bucket and bucket[0] < now - RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) > RATE_LIMIT_REQUESTS:
            raise HTTPException(429, f"Rate limit exceeded — max {RATE_LIMIT_REQUESTS} requests/minute per API key")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_init()
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
        allow_origins=[ALLOWED_ORIGIN],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Agents --------------------------------------------------------

    class RegisterAgent(BaseModel):
        agent_id: str
        os: str
        name: str
        tags: str = ""  # feature 8: comma-separated tags for grouping/targeting

    @app.post("/agents/register", dependencies=[Depends(require_api_key)])
    def register_agent(body: RegisterAgent):
        now = time.time()
        existing = db_query_one("SELECT id FROM agents WHERE id = ?", (body.agent_id,))
        if existing:
            db_execute(
                "UPDATE agents SET os = ?, name = ?, tags = ?, last_seen = ? WHERE id = ?",
                (body.os, body.name, body.tags, now, body.agent_id),
            )
        else:
            db_execute(
                "INSERT INTO agents (id, os, name, tags, last_seen, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (body.agent_id, body.os, body.name, body.tags, now, now),
            )
        return {"status": "registered", "agent_id": body.agent_id}

    @app.get("/agents", dependencies=[Depends(require_api_key)])
    def list_agents():
        now = time.time()
        agents = db_query("SELECT * FROM agents ORDER BY name")
        for a in agents:
            a["online"] = bool(a["last_seen"]) and (now - a["last_seen"]) < AGENT_ONLINE_WINDOW_SECONDS
        return agents

    @app.get("/agents/{agent_id}/poll", dependencies=[Depends(require_api_key)])
    def poll_commands(agent_id: str):
        db_execute("UPDATE agents SET last_seen = ? WHERE id = ?", (time.time(), agent_id))
        now = time.time()
        while True:
            row = db_query_one(
                "SELECT * FROM commands WHERE agent_id = ? AND status = 'queued' ORDER BY created_at LIMIT 1",
                (agent_id,),
            )
            if not row:
                return {"command": None}
            # feature 9: commands with an expired TTL never fire — they're marked
            # expired and the loop moves to the next queued command instead.
            if row["expires_at"] and row["expires_at"] < now:
                db_execute("UPDATE commands SET status = 'expired', updated_at = ? WHERE id = ?", (now, row["id"]))
                continue
            db_execute("UPDATE commands SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, row["id"]))
            return {"command": {"id": row["id"], "type": row["type"], "payload": json.loads(row["payload"])}}

    class AckCommand(BaseModel):
        command_id: int
        status: Literal["done", "failed"]
        result: str = ""

    @app.post("/agents/{agent_id}/ack", dependencies=[Depends(require_api_key)])
    def ack_command(agent_id: str, body: AckCommand):
        db_execute(
            "UPDATE commands SET status = ?, result = ?, updated_at = ? WHERE id = ? AND agent_id = ?",
            (body.status, body.result, time.time(), body.command_id, agent_id),
        )
        return {"status": "ok"}

    # -- Commands --------------------------------------------------------

    class CreateCommand(BaseModel):
        agent_id: str
        type: CommandType
        payload: dict
        ttl_seconds: int | None = None  # feature 9

    @app.post("/commands", dependencies=[Depends(require_api_key)])
    def create_command(body: CreateCommand):
        now = time.time()
        expires_at = now + body.ttl_seconds if body.ttl_seconds else None
        cur = db_execute(
            "INSERT INTO commands (agent_id, type, payload, expires_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (body.agent_id, body.type, json.dumps(body.payload), expires_at, now, now),
        )
        return {"command_id": cur.lastrowid}

    @app.get("/commands", dependencies=[Depends(require_api_key)])
    def list_commands(agent_id: str | None = None, limit: int = 50):
        if agent_id:
            rows = db_query(
                "SELECT * FROM commands WHERE agent_id = ? ORDER BY id DESC LIMIT ?", (agent_id, limit)
            )
        else:
            rows = db_query("SELECT * FROM commands ORDER BY id DESC LIMIT ?", (limit,))
        return rows

    # feature 10: retry a failed command instead of re-submitting it by hand
    @app.post("/commands/{command_id}/retry", dependencies=[Depends(require_api_key)])
    def retry_command(command_id: int):
        row = db_query_one("SELECT * FROM commands WHERE id = ?", (command_id,))
        if not row:
            raise HTTPException(404, "command not found")
        if row["status"] != "failed":
            raise HTTPException(400, f"can only retry failed commands (current status: {row['status']})")
        db_execute(
            "UPDATE commands SET status = 'queued', result = NULL, retries = retries + 1, updated_at = ? WHERE id = ?",
            (time.time(), command_id),
        )
        return {"status": "requeued", "retries": row["retries"] + 1}

    # feature 7: fan a command out to every agent matching a selector
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
            cur = db_execute(
                "INSERT INTO commands (agent_id, type, payload, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, body.type, json.dumps(body.payload), expires_at, now, now),
            )
            command_ids.append(cur.lastrowid)
        return {"command_ids": command_ids, "targeted_agents": targets}

    # -- Chat (Claude API) ------------------------------------------------

    class ChatRequest(BaseModel):
        session_id: str = "default"
        message: str

    @app.post("/chat", dependencies=[Depends(require_api_key)])
    def chat(body: ChatRequest):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise HTTPException(500, "ANTHROPIC_API_KEY is not configured on the server")

        now = time.time()
        db_execute(
            "INSERT INTO chats (session_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
            (body.session_id, body.message, now),
        )

        history = db_query(
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

        db_execute(
            "INSERT INTO chats (session_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
            (body.session_id, reply, time.time()),
        )
        return {"reply": reply}

    @app.get("/chat/{session_id}", dependencies=[Depends(require_api_key)])
    def chat_history(session_id: str):
        return db_query(
            "SELECT role, content, created_at FROM chats WHERE session_id = ? ORDER BY id", (session_id,)
        )

    # -- Email -------------------------------------------------------------

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

    # -- SMS / calls (Twilio — optional) ------------------------------------

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

    # feature 13: push straight to Slack/Discord without any device agent involved
    class ExternalNotify(BaseModel):
        message: str
        title: str | None = None

    @app.post("/notify/external", dependencies=[Depends(require_api_key)])
    def notify_external(body: ExternalNotify):
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

    # -- Schedules -----------------------------------------------------------

    class CreateSchedule(BaseModel):
        agent_id: str
        type: CommandType
        payload: dict
        interval_seconds: int

    @app.post("/schedules", dependencies=[Depends(require_api_key)])
    def create_schedule(body: CreateSchedule):
        now = time.time()
        cur = db_execute(
            "INSERT INTO schedules (agent_id, type, payload, interval_seconds, next_run_at) VALUES (?, ?, ?, ?, ?)",
            (body.agent_id, body.type, json.dumps(body.payload), body.interval_seconds, now + body.interval_seconds),
        )
        return {"schedule_id": cur.lastrowid}

    @app.get("/schedules", dependencies=[Depends(require_api_key)])
    def list_schedules():
        return db_query("SELECT * FROM schedules ORDER BY id DESC")

    @app.delete("/schedules/{schedule_id}", dependencies=[Depends(require_api_key)])
    def delete_schedule(schedule_id: int):
        db_execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        return {"status": "deleted"}

    async def scheduler_loop() -> None:
        while True:
            now = time.time()
            due = db_query("SELECT * FROM schedules WHERE enabled = 1 AND next_run_at <= ?", (now,))
            for s in due:
                db_execute(
                    "INSERT INTO commands (agent_id, type, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (s["agent_id"], s["type"], s["payload"], now, now),
                )
                db_execute(
                    "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                    (now + s["interval_seconds"], s["id"]),
                )
            await asyncio.sleep(15)

    # -- Inbound webhooks ------------------------------------------------------

    @app.post("/webhooks/{name}", dependencies=[Depends(require_api_key)])
    async def inbound_webhook(name: str, body: dict, notify_agent_id: str | None = None):
        db_execute(
            "INSERT INTO events (source, payload, created_at) VALUES (?, ?, ?)",
            (name, json.dumps(body), time.time()),
        )
        if notify_agent_id:
            summary = json.dumps(body)[:500]
            now = time.time()
            db_execute(
                "INSERT INTO commands (agent_id, type, payload, created_at, updated_at) VALUES (?, 'notify', ?, ?, ?)",
                (notify_agent_id, json.dumps({"title": f"Webhook: {name}", "message": summary}), now, now),
            )
        return {"status": "received"}

    # -- Files (feature 6: screenshots / remote-file-write / arbitrary uploads) --

    class FileUpload(BaseModel):
        agent_id: str
        filename: str
        content_type: str = "application/octet-stream"
        content_base64: str

    @app.post("/files/upload", dependencies=[Depends(require_api_key)])
    def upload_file(body: FileUpload):
        data = base64.b64decode(body.content_base64)
        now = time.time()
        cur = db_execute(
            "INSERT INTO files (agent_id, filename, content, content_type, size_bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (body.agent_id, body.filename, data, body.content_type, len(data), now),
        )
        return {"file_id": cur.lastrowid, "size_bytes": len(data)}

    @app.get("/files", dependencies=[Depends(require_api_key)])
    def list_files(agent_id: str | None = None):
        cols = "id, agent_id, filename, content_type, size_bytes, created_at"
        if agent_id:
            return db_query(f"SELECT {cols} FROM files WHERE agent_id = ? ORDER BY id DESC", (agent_id,))
        return db_query(f"SELECT {cols} FROM files ORDER BY id DESC")

    @app.get("/files/{file_id}", dependencies=[Depends(require_api_key)])
    def download_file(file_id: int):
        row = db_query_one("SELECT * FROM files WHERE id = ?", (file_id,))
        if not row:
            raise HTTPException(404, "file not found")
        return Response(
            content=row["content"],
            media_type=row["content_type"],
            headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
        )

    # -- Audit log (feature 12) --------------------------------------------

    @app.get("/audit", dependencies=[Depends(require_api_key)])
    def audit_log(limit: int = 100):
        commands = db_query(
            "SELECT id, agent_id, type, status, created_at FROM commands ORDER BY id DESC LIMIT ?", (limit,)
        )
        chats = db_query(
            "SELECT id, session_id, role, created_at FROM chats ORDER BY id DESC LIMIT ?", (limit,)
        )
        events = db_query(
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

    return app


def run_server(args: argparse.Namespace) -> None:
    import uvicorn

    app = build_server_app()
    uvicorn.run(app, host=args.host, port=args.port)


# ============================================================================
# 3. AGENT BASE — shared poll/dispatch loop, pluggable command registry
# ============================================================================

class AgentBase:
    os_name = "generic"

    def __init__(
        self,
        server_url: str,
        api_key: str,
        agent_name: str | None = None,
        poll_interval: int = 5,
        tags: str = "",
        allow_shell: bool = False,
        allow_write: bool = False,
    ):
        if not api_key:
            raise SystemExit("Missing API key — pass --key or set AGENT_API_KEY")
        import requests  # local import: only agent modes need this dependency

        self.server_url = server_url.rstrip("/")
        self.agent_id = f"{self.os_name}-{uuid.getnode():x}"
        self.agent_name = agent_name or platform.node()
        self.poll_interval = poll_interval
        self.tags = tags
        self.allow_shell = allow_shell
        self.allow_write = allow_write
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})
        self._requests = requests

        # feature 14: pluggable command registry — new command types are one
        # register_command() call, not a new branch in a growing if/elif chain.
        # write_file and run_command are opt-in (--allow-write / --allow-shell):
        # they're the two commands that can do real damage on the host machine.
        self.commands: dict[str, Callable[[dict], str]] = {}
        self.register_command("launch_app", lambda p: self.launch_app(p["app_name"], p.get("args")))
        self.register_command("notify", lambda p: self.notify(p.get("title", "Notice"), p.get("message", "")))
        self.register_command("get_clipboard", lambda p: self.get_clipboard())
        self.register_command("set_clipboard", lambda p: self.set_clipboard(p["text"]))
        self.register_command("speak", lambda p: self.speak(p["text"]))
        self.register_command("screenshot", lambda p: self.screenshot())
        if allow_write:
            self.register_command("write_file", lambda p: self.write_file(p["path"], p["content_base64"]))
        if allow_shell:
            self.register_command("run_command", lambda p: self.run_command(p["command"]))

    def register_command(self, name: str, handler: Callable[[dict], str]) -> None:
        self.commands[name] = handler

    def register(self) -> None:
        resp = self.session.post(
            f"{self.server_url}/agents/register",
            json={"agent_id": self.agent_id, "os": self.os_name, "name": self.agent_name, "tags": self.tags},
            timeout=10,
        )
        resp.raise_for_status()

    # ---- OS-specific primitives — subclasses implement these -------------

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        raise NotImplementedError

    def notify(self, title: str, message: str) -> str:
        raise NotImplementedError

    def get_clipboard(self) -> str:
        raise NotImplementedError

    def set_clipboard(self, text: str) -> str:
        raise NotImplementedError

    def speak(self, text: str) -> str:
        raise NotImplementedError

    def screenshot(self) -> str:
        raise NotImplementedError

    # ---- OS-agnostic commands — implemented once, here --------------------

    def write_file(self, path: str, content_base64: str) -> str:
        data = base64.b64decode(content_base64)
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"wrote {len(data)} bytes to {target}"

    def run_command(self, command: str) -> str:
        # Gated behind --allow-shell at startup. The command comes from
        # whoever holds the API key — same trust model as launch_app/write_file,
        # just with a much bigger blast radius, hence the explicit opt-in.
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        output = (result.stdout or "") + (result.stderr or "")
        return f"exit={result.returncode}: {output[:2000]}"

    def _upload_file(self, path: str, content_type: str) -> int:
        with open(path, "rb") as f:
            data = f.read()
        resp = self.session.post(
            f"{self.server_url}/files/upload",
            json={
                "agent_id": self.agent_id,
                "filename": os.path.basename(path),
                "content_type": content_type,
                "content_base64": base64.b64encode(data).decode(),
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["file_id"]

    # ---- Poll loop ----------------------------------------------------------

    def dispatch(self, command: dict) -> dict:
        ctype = command["type"]
        handler = self.commands.get(ctype)
        if not handler:
            return {"status": "failed", "result": f"unsupported command type: {ctype}"}
        try:
            return {"status": "done", "result": handler(command["payload"])}
        except Exception as exc:  # tool execution should never crash the poll loop
            return {"status": "failed", "result": str(exc)}

    def run(self) -> None:
        self.register()
        print(f"[{self.agent_id}] registered as '{self.agent_name}' (tags: {self.tags or 'none'}), polling {self.server_url}")
        while True:
            try:
                resp = self.session.get(f"{self.server_url}/agents/{self.agent_id}/poll", timeout=10)
                resp.raise_for_status()
                command = resp.json().get("command")
                if command:
                    print(f"[{self.agent_id}] running {command['type']} #{command['id']}")
                    outcome = self.dispatch(command)
                    self.session.post(
                        f"{self.server_url}/agents/{self.agent_id}/ack",
                        json={"command_id": command["id"], **outcome},
                        timeout=10,
                    )
                else:
                    time.sleep(self.poll_interval)
            except self._requests.RequestException as exc:
                print(f"[{self.agent_id}] poll error: {exc}")
                time.sleep(self.poll_interval)


# ============================================================================
# 4. WINDOWS AGENT
# ============================================================================

class WindowsAgent(AgentBase):
    os_name = "windows"

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        # app_name can be an .exe path, a Start-Menu-resolvable name (e.g. "notepad"),
        # or a URI (e.g. "ms-settings:", "spotify:", any registered app protocol).
        if args:
            subprocess.Popen([app_name, *args])
        else:
            os.startfile(app_name)  # type: ignore[attr-defined]  (Windows-only builtin)
        return f"launched {app_name}"

    def notify(self, title: str, message: str) -> str:
        script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType=WindowsRuntime] > $null;"
            "$template = [Windows.UI.Notifications.ToastNotificationManager]::"
            "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
            "$texts = $template.GetElementsByTagName('text');"
            f"$texts.Item(0).AppendChild($template.CreateTextNode('{title}')) > $null;"
            f"$texts.Item(1).AppendChild($template.CreateTextNode('{message}')) > $null;"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AI Agent').Show($toast)"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        return "notification shown"

    def get_clipboard(self) -> str:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    def set_clipboard(self, text: str) -> str:
        subprocess.run(["clip"], input=text, text=True, check=True)
        return "clipboard set"

    def speak(self, text: str) -> str:
        escaped = text.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "(New-Object System.Speech.Synthesis.SpeechSynthesizer)."
            f"Speak('{escaped}')"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        return "spoke text"

    def screenshot(self) -> str:
        path = os.path.join(os.environ.get("TEMP", "."), f"shot_{uuid.uuid4().hex}.png")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height;"
            "$g = [System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size);"
            f"$bmp.Save('{path}', [System.Drawing.Imaging.ImageFormat]::Png)"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
        file_id = self._upload_file(path, "image/png")
        os.remove(path)
        return f"uploaded screenshot as file {file_id}"


# ============================================================================
# 5. macOS AGENT
# ============================================================================

class MacAgent(AgentBase):
    os_name = "macos"

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        # app_name is the .app's display name, e.g. "Safari", "Notes", "Spotify".
        cmd = ["open", "-a", app_name]
        if args:
            cmd += ["--args", *args]
        subprocess.run(cmd, check=True)
        return f"launched {app_name}"

    def notify(self, title: str, message: str) -> str:
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        script = f'display notification "{esc(message)}" with title "{esc(title)}"'
        subprocess.run(["osascript", "-e", script], check=True)
        return "notification shown"

    def get_clipboard(self) -> str:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, check=True)
        return result.stdout

    def set_clipboard(self, text: str) -> str:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        return "clipboard set"

    def speak(self, text: str) -> str:
        subprocess.run(["say", text], check=True)
        return "spoke text"

    def screenshot(self) -> str:
        path = f"/tmp/shot_{uuid.uuid4().hex}.png"
        subprocess.run(["screencapture", "-x", path], check=True)
        file_id = self._upload_file(path, "image/png")
        os.remove(path)
        return f"uploaded screenshot as file {file_id}"


# ============================================================================
# 6. ANDROID AGENT (run inside Termux; needs the Termux:API companion app)
# ============================================================================

class AndroidAgent(AgentBase):
    os_name = "android"

    def launch_app(self, app_name: str, args: list[str] | None = None) -> str:
        # app_name is the Android package name, e.g. "com.whatsapp", "com.spotify.music".
        subprocess.run(
            ["monkey", "-p", app_name, "-c", "android.intent.category.LAUNCHER", "1"],
            check=True,
        )
        return f"launched package {app_name}"

    def notify(self, title: str, message: str) -> str:
        subprocess.run(["termux-notification", "--title", title, "--content", message], check=True)
        return "notification shown"

    def get_clipboard(self) -> str:
        result = subprocess.run(["termux-clipboard-get"], capture_output=True, text=True, check=True)
        return result.stdout

    def set_clipboard(self, text: str) -> str:
        subprocess.run(["termux-clipboard-set"], input=text, text=True, check=True)
        return "clipboard set"

    def speak(self, text: str) -> str:
        subprocess.run(["termux-tts-speak", text], check=True)
        return "spoke text"

    def screenshot(self) -> str:
        # Plain, non-root Termux cannot capture the screen — `screencap` needs
        # root. Documented here rather than silently failing or faking success.
        path = f"/data/data/com.termux/files/usr/tmp/shot_{uuid.uuid4().hex}.png"
        try:
            subprocess.run(["screencap", "-p", path], check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                "screenshot requires a rooted device with 'screencap' on PATH — "
                "not available on a standard, non-root Termux install"
            ) from exc
        file_id = self._upload_file(path, "image/png")
        os.remove(path)
        return f"uploaded screenshot as file {file_id}"


# ============================================================================
# CLI entry point
# ============================================================================

AGENT_CLASSES = {"windows": WindowsAgent, "macos": MacAgent, "android": AndroidAgent}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    p_server = sub.add_parser("server", help="Run the cloud orchestrator (FastAPI)")
    p_server.add_argument("--host", default="0.0.0.0")
    p_server.add_argument("--port", type=int, default=8000)

    for mode in AGENT_CLASSES:
        p = sub.add_parser(mode, help=f"Run the {mode} local agent")
        p.add_argument("--server", default=os.environ.get("AGENT_SERVER_URL", "http://localhost:8000"))
        p.add_argument("--key", default=os.environ.get("AGENT_API_KEY", ""))
        p.add_argument("--name", default=None)
        p.add_argument("--tags", default="", help="comma-separated tags, e.g. home,laptop")
        p.add_argument(
            "--allow-write", action="store_true",
            help="enable the write_file command type (can write to any path this process can reach — off by default)",
        )
        p.add_argument(
            "--allow-shell", action="store_true",
            help="enable the run_command command type (dangerous — only if you trust every API-key holder)",
        )

    args = parser.parse_args()

    if args.mode == "server":
        run_server(args)
    else:
        AGENT_CLASSES[args.mode](
            args.server, args.key, args.name,
            tags=args.tags, allow_shell=args.allow_shell, allow_write=args.allow_write,
        ).run()


if __name__ == "__main__":
    main()
