# AI Agent — cloud brain + local launchers

A personal automation system: one cloud service (the "brain" — chat, email,
storage, scheduling, files, audit log) plus small local agents for Windows,
macOS, and Android that actually launch apps, show notifications, and more
on that device. iOS is documented separately because it genuinely can't work
the same way — see [`agents/ios_agent.md`](agents/ios_agent.md).

Two ways to run it — pick one:

- **Modular** (`cloud/` + `agents/`) — the cloud service and each OS agent
  are separate files. Better if you're going to deploy the server somewhere
  and only ship the relevant agent script per machine.
- **Single file** ([`ai_agent.py`](ai_agent.py)) — the same code, merged
  into one file with a CLI dispatcher (`python ai_agent.py server`,
  `python ai_agent.py windows`, ...). Handy if you just want to grab one
  file. The two are kept in sync; `tests/` runs against the modular version.

## Why it's split this way

No cloud service can reach into your laptop or phone and open an app on its
own — every OS blocks that by design. The only honest architecture is:
a lightweight process runs **on** each device, polls the cloud for work, and
acts locally. That process is the "agent"; the cloud service is where
chat, email, storage and scheduling live, since those don't need to run on
your device at all.

```
                    ┌──────────────────────────────────┐
                    │   Cloud orchestrator (FastAPI)     │
   Dashboard  ────▶ │  - command queue + broadcast        │
   (ui/)            │  - chat  → Claude API                │
                    │  - email → SMTP                       │
                    │  - SMS/calls → Twilio (opt.)          │
                    │  - Slack/Discord bridge (opt.)         │
                    │  - scheduler (recurring cmds)           │
                    │  - inbound webhooks                      │
                    │  - file store + audit log                 │
                    │  - rate limiting                           │
                    └────────────┬────────────────────────────────┘
                                 │ poll / ack (HTTPS + API key)
        ┌───────────────┬────────┴────────┬───────────────┐
        ▼               ▼                 ▼               ▼
  windows_agent.py  macos_agent.py   android_agent.py   iOS: Shortcuts
  (launch, notify,  (launch, notify,  (Termux; launch,   (see ios_agent.md
   clipboard, TTS,   clipboard, TTS,   notify, clipboard,  — user-in-the-loop)
   screenshot)       screenshot)       TTS; screenshot
                                        needs root)
```

## Quick start

```bash
cd cloud
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in AGENT_API_KEY, ANTHROPIC_API_KEY, SMTP_*
export $(grep -v '^#' ../.env | xargs)
uvicorn server:app --reload --port 8000
```

Or the single-file version: `python ai_agent.py server --port 8000` (same
env vars).

Open `ui/dashboard.html` in a browser, point it at `http://localhost:8000`
with your `AGENT_API_KEY`, and hit Connect.

On each machine you want to control:

```bash
cd agents
pip install requests
python windows_agent.py --server http://your-cloud-host:8000 --key change-me --tags home,laptop
# or macos_agent.py / android_agent.py (inside Termux)

# opt in to the two commands that can do real damage on the host machine:
python windows_agent.py --server ... --key ... --allow-write --allow-shell
```

The agent registers itself, shows up in the dashboard's Agents panel, and
starts polling for commands.

## Tests

```bash
pip install -r cloud/requirements.txt pytest httpx
cd tests
AGENT_API_KEY=testkey pytest -v
```

14 tests exercise the queue round-trip, retry, TTL expiry, broadcast
targeting (`all` / `os:x` / `tag:x`), the file store, the audit log, rate
limiting, and the "not configured" error paths for Twilio/Slack/Discord.
They run in CI on every push that touches `ai-agent/` (see
[`.github/workflows/ai-agent-tests.yml`](../.github/workflows/ai-agent-tests.yml)).

## What's implemented right now

**Core**
- **Command queue + broadcast** — `POST /commands` targets one agent;
  `POST /commands/broadcast` targets `all`, `os:<name>`, or `tag:<name>` at
  once. Commands support an optional `ttl_seconds` — one that goes stale
  before an offline agent picks it up is marked `expired` instead of firing
  hours late. Failed commands can be requeued with `POST /commands/{id}/retry`.
- **Pluggable command dispatch** — `AgentBase.register_command(name, fn)`
  replaced a fixed if/elif chain, so a new command type is one call, not a
  new branch in a growing function.
- **App launching** — Windows (`os.startfile`/subprocess), macOS
  (`open -a`), Android (`monkey` via Termux).
- **Native notifications** — Windows toast (PowerShell), macOS
  (`osascript`), Android (`termux-notification`).
- **Clipboard get/set, text-to-speech, screenshot capture** — all three OS
  agents. Screenshots upload to the cloud file store. Android's screenshot
  is honest about needing root — plain Termux can't capture the screen, and
  it raises a clear error instead of pretending to succeed.
- **Remote file write / shell execution** — `write_file` and `run_command`
  are the two commands that can do real damage on the host, so they're
  **off by default** and only registered with `--allow-write` /
  `--allow-shell` on the agent.

**Cloud service**
- **Chat** — `/chat` calls the Claude API (`claude-opus-4-8`) with
  per-session history stored in SQLite.
- **Email** — `/email/send` over SMTP (Gmail app passwords, SendGrid SMTP
  relay, etc.).
- **SMS & voice calls** — `/sms/send` and `/calls/make` via Twilio. 501 (not
  a silent no-op) until you set `TWILIO_*`.
- **Slack/Discord bridge** — `POST /notify/external` pushes straight to a
  webhook URL, no device agent involved. 501 until `SLACK_WEBHOOK_URL` /
  `DISCORD_WEBHOOK_URL` is set.
- **File store** — `/files/upload`, `/files`, `/files/{id}` — small blob
  store backing screenshots and any other agent → cloud upload.
- **Scheduling** — `/schedules` creates recurring commands; a background
  asyncio loop enqueues them when due.
- **Inbound webhooks** — `POST /webhooks/{name}` lets external services
  push events in, optionally turning them into a notification.
- **Audit log** — `GET /audit` merges commands + chats + webhook events into
  one time-ordered feed.
- **Rate limiting** — sliding window, 120 requests/minute per API key.
- **Agent tags** — `--tags home,laptop` on an agent; used by broadcast
  targeting (`tag:home`).

**Dashboard** (`ui/dashboard.html`, zero build step) — agent roster with
tags, launch/notify/email panels, broadcast panel, external-notify panel,
chat, command history with a retry button on failed commands, a file
browser with upload, and an audit log view. Everything above has a UI
surface — nothing new here is curl-only.

## What's still a real gap, not a promise

- **Single shared API key.** Every agent and the dashboard use the same
  secret. The `agents` table already has an unused-by-auth per-agent shape
  (`id`); wiring per-agent tokens through registration and `require_api_key`
  is the next security step if you go beyond personal use.
- **SQLite, single process.** Fine at personal scale. A Redis/RQ queue and
  WebSocket push (instead of polling) are the natural next step if you
  outgrow one process.
- **No multi-user accounts.** One operator, one API key, all agents visible
  to anyone who holds it.

These aren't hidden — they're the honest edge of what a from-scratch build
covers before it needs a second pass.

## Security notes

- `AGENT_API_KEY` is a single shared secret for this example (see the gap
  above). For anything beyond personal use, add per-agent tokens.
- CORS defaults to `allow_origins=["*"]` for local dev — set
  `DASHBOARD_ORIGIN` in `.env` before exposing this server publicly; the
  server logs a warning at startup if it's still wide open.
- `write_file` and `run_command` are off by default; only enable them
  (`--allow-write` / `--allow-shell`) on agents where you trust every holder
  of the API key with that level of access.
- Command payloads are trusted input from whoever holds the API key. If you
  expose `/commands` to anything other than yourself, validate `app_name`
  against an allowlist before it reaches `launch_app`.
- Never commit `.env` — it holds your Anthropic, SMTP, Twilio, and webhook
  credentials. `.env.example` is the template.

## iOS

Read [`agents/ios_agent.md`](agents/ios_agent.md) — Apple's sandboxing means
there's no background daemon equivalent to the other three agents. The
realistic version uses a Shortcuts automation that polls the same `/poll`
endpoint and uses "Open App" / "Show Notification" actions, triggered by a
Personal Automation or a tapped `shortcuts://` link. It's the honest
ceiling on iOS without shipping a full App Store app.
