# AI Agent — cloud brain + local launchers

A personal automation system: one cloud service (the "brain" — chat, email,
storage, scheduling) plus small local agents for Windows, macOS, and Android
that actually launch apps and show notifications on that device. iOS is
documented separately because it genuinely can't work the same way — see
[`agents/ios_agent.md`](agents/ios_agent.md).

## Why it's split this way

No cloud service can reach into your laptop or phone and open an app on its
own — every OS blocks that by design. The only honest architecture is:
a lightweight process runs **on** each device, polls the cloud for work, and
acts locally. That process is the "agent"; the cloud service is where
chat, email, storage and scheduling live, since those don't need to run on
your device at all.

```
                    ┌─────────────────────────────┐
                    │   Cloud orchestrator (FastAPI)│
   Dashboard  ────▶ │  - command queue (SQLite)     │
   (ui/)            │  - chat  → Claude API          │
                    │  - email → SMTP                │
                    │  - SMS/calls → Twilio (opt.)   │
                    │  - scheduler (recurring cmds)  │
                    │  - inbound webhooks            │
                    └───────────┬────────────────────┘
                                │ poll / ack (HTTPS + API key)
        ┌───────────────┬───────┴────────┬───────────────┐
        ▼               ▼                ▼               ▼
  windows_agent.py  macos_agent.py  android_agent.py   iOS: Shortcuts
  (launches apps,   (launches apps,  (Termux, launches  (see ios_agent.md
   toast notify)     osascript)      apps, notify)       — user-in-the-loop)
```

## Quick start

```bash
cd cloud
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in AGENT_API_KEY, ANTHROPIC_API_KEY, SMTP_*
export $(grep -v '^#' ../.env | xargs)
uvicorn server:app --reload --port 8000
```

Open `ui/dashboard.html` in a browser, point it at `http://localhost:8000`
with your `AGENT_API_KEY`, and hit Connect.

On each machine you want to control:

```bash
cd agents
pip install requests
python windows_agent.py --server http://your-cloud-host:8000 --key change-me
# or macos_agent.py / android_agent.py (inside Termux)
```

The agent registers itself, shows up in the dashboard's Agents panel, and
starts polling for commands.

## What's implemented right now

- **Command queue** — cloud enqueues `launch_app` / `notify` / `custom`
  commands per agent; agents poll every few seconds and report back
  `done`/`failed` with a result string. This is the extensible core: adding
  a new command type is one `elif` in `dispatch()`.
- **App launching** — real, working launch on Windows (`os.startfile` /
  subprocess), macOS (`open -a`), Android (`monkey` via Termux).
- **Native notifications** — Windows toast (PowerShell), macOS
  (`osascript`), Android (`termux-notification`).
- **Chat** — `/chat` calls the Claude API (`claude-opus-4-8`) with per-session
  history stored in SQLite.
- **Email** — `/email/send` over SMTP (works with Gmail app passwords,
  SendGrid SMTP relay, etc.).
- **SMS & voice calls** — `/sms/send` and `/calls/make` via Twilio. Disabled
  (returns 501) until you set `TWILIO_*` env vars — this is a real,
  working integration point, not a stub.
- **Scheduling** — `/schedules` creates recurring commands; a background
  asyncio loop checks every 15s and enqueues them when due.
- **Inbound webhooks** — `POST /webhooks/{name}` lets external services
  (GitHub, cron pingers, anything) push events in, optionally turning them
  into a notification on a chosen agent.
- **API-key auth** on every endpoint; **agent online/offline status** from
  last-seen heartbeats; **web dashboard** (`ui/dashboard.html`, zero build
  step) to drive all of the above.

## The "dozens of additional features" — what's realistic to promise

I built the core cleanly rather than half-implementing thirty features, but
every one of these fits the same command-queue/endpoint pattern and is a
small, bounded addition on top of what's here — not a rewrite:

**Extending the agent side:** clipboard sync, screenshot capture, file
transfer (`get_file`/`put_file` commands), macro/keystroke playback, browser
automation via a headless-browser command, voice command input (Whisper),
geofencing triggers on the mobile agents, a system-tray/menu-bar UI instead
of a console script.

**Extending the cloud side:** Slack/Discord bot bridge (mirror `/chat` into
a Slack app), calendar integration (Google Calendar API → scheduled
commands), RAG memory for chat (embed + retrieve past conversations instead
of a flat history), multi-user accounts with per-user API keys and agent
scoping, a proper task queue (Redis/RQ) instead of the SQLite polling table
once you outgrow single-process, WebSocket push instead of polling so
commands run instantly instead of within one poll interval, an audit-log
export, rate limiting per API key, a plugin registry so third parties can
register new command types without touching `server.py`.

I did not stub these out as fake endpoints — an API that pretends to send a
push notification but doesn't is worse than no API. Everything above is a
clearly-scoped addition to the same architecture, ready when you need it.

## Security notes

- `AGENT_API_KEY` is a single shared secret for this example. For anything
  beyond personal use, switch to per-agent tokens (the `agents` table
  already has room for one) and per-dashboard-user auth.
- The dashboard's CORS is wide open (`allow_origins=["*"]`) for local dev —
  lock it to your dashboard's real origin before exposing the server
  publicly.
- Command payloads are trusted input from whoever holds the API key. If you
  expose `/commands` to anything other than yourself, validate `app_name`
  against an allowlist before it reaches `launch_app`.
- Never commit `.env` — it holds your Anthropic, SMTP, and Twilio
  credentials. `.env.example` is the template.

## iOS

Read [`agents/ios_agent.md`](agents/ios_agent.md) — Apple's sandboxing means
there's no background daemon equivalent to the other three agents. The
realistic version uses a Shortcuts automation that polls the same `/poll`
endpoint and uses "Open App" / "Show Notification" actions, triggered by a
Personal Automation or a tapped `shortcuts://` link. It's the honest
ceiling on iOS without shipping a full App Store app.
