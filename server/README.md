# Mindora backend — shared key, shared storage

Lets anyone use Mindora without their own Claude key or Google Drive. The owner's
credentials live on this server and are never sent to a browser.

```
browser ──POST /api/chat───→ server ──(owner's key)──→ Anthropic
        ──PUT  /api/state──→ server ──(owner's token)→ owner's Google Drive
                                          one file per visitor
```

Without this server the app still works exactly as before — each visitor uses
their own key and their own Drive. This is purely additive.

> **This bills to you.** Every message a visitor sends is charged to your
> Anthropic account. The caps below exist because a public URL will eventually
> be found and scripted against. Read them before deploying.

## Spend controls

| Env var | Default | What it does |
|---|---|---|
| `DAILY_REQUEST_CAP` | `300` | Hard ceiling for *everyone combined*, per UTC day |
| `PER_VISITOR_DAILY_CAP` | `25` | One browser can't eat the whole budget |
| `PER_MINUTE_CAP` | `4` | Blocks tight scripted loops |
| `ALLOWED_MODELS` | `claude-sonnet-5,claude-haiku-4-5` | Callers can't upgrade themselves to a pricier model |
| `MAX_TOKENS` | `12000` | Per-response ceiling; the client cannot raise it |
| `ACCESS_CODE` | *(empty)* | Set it and visitors must enter a passcode. Empty = open to anyone with the URL |

Counters live in SQLite, so a restart doesn't hand out a fresh budget. When a
cap is hit the server returns `429` and the app shows a plain message.

**Set `ACCESS_CODE` if you don't want the whole internet spending your credit.**
Caps bound the damage; a passcode prevents it.

## Setup

### 1. Google Drive (storage)

Create a **Desktop app** OAuth client (Google Cloud Console → Credentials →
Create credentials → OAuth client ID → Desktop app). This is separate from the
Web client the browser app uses. Then, on your own machine:

```bash
pip install -r server/requirements.txt
python server/setup_drive.py --client-id XXX --client-secret YYY
```

Approve once; it prints a `GOOGLE_REFRESH_TOKEN`. The scope is `drive.file`, so
this server can only touch files it creates — it cannot read anything else in
your Drive.

### 2. Deploy

`render.yaml` is included; on Render, "New → Blueprint" and point it at the repo.
Anything that runs a Docker image works equally well.

Set these as **secrets** in the host's dashboard — never in the repo:

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
SECRET_KEY=<long random string>
ALLOWED_ORIGINS=https://nasif-hbd.github.io
```

`SECRET_KEY` signs the anonymous visitor ids. Without it the server refuses to
serve state, because unsigned ids could be edited to read someone else's plans.

### 3. Point the app at it

Mindora → Settings → **Shared Mindora server** → paste the URL → **Test connection**.
The nav pill switches to **Shared** and the API-key field becomes unnecessary.

## How storage works

Each browser gets a random, signed, anonymous id on first contact — no login, no
email. Its plans are stored as `atlas-<hash>.json` inside one folder in your
Drive.

**One file per visitor is deliberate.** A single shared file written by many
browsers at once loses data: Drive has no merge, so the last write silently
wins. Separate files remove the conflict entirely.

The signature matters too: a forged id fails verification and is handed a fresh
empty identity rather than someone else's data. That's covered by tests.

## Endpoints

| | |
|---|---|
| `GET /api/health` | config + remaining daily quota. Safe to call publicly; exposes no secrets |
| `POST /api/chat` | streams from Anthropic using the owner's key |
| `GET /api/state` | this visitor's saved plans |
| `PUT /api/state` | save this visitor's plans (2 MB limit) |

## Local development

```bash
cd server
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-... SECRET_KEY=dev uvicorn app.main:app --reload
```

With no Google credentials set it falls back to writing files under
`data_store/`, which is fine locally — but on an ephemeral host that disk is
wiped on every deploy, which is exactly why Drive is the real target.

## Tests

```bash
cd server && python -m pytest tests -q
```

Covers the parts that protect you: caps (including surviving restart), the
model allowlist, that the owner's key never appears in a response, and that a
forged visitor token can't read another visitor's data.
