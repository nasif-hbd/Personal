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

## Subscriptions

Chat is the paid feature. Plans, the catalog, progress tracking and offline
access stay free — the paywall exists because every chat message bills to your
Anthropic account.

**The paywall is enforced here, not in the browser.** The client hides the chat
box when someone isn't subscribed, but that is only cosmetic — anyone can edit
the page they are holding. `/api/chat` returns `402` unless the server itself
sees an entitlement, which is the check that actually protects your credit.

| Env var | Default | What it does |
|---|---|---|
| `FREE_ACCESS_CODE` | `Nafia is my Sister` | Unlocks Pro permanently, free. Matching ignores case and extra spaces |
| `FREE_TRIAL_MESSAGES` | `0` | Messages allowed before paying. `0` is a strict paywall |
| `PRICE_MONTHLY` | `499` | Price in whole currency units |
| `PRICE_YEARLY` | `4499` | Price in whole currency units |
| `CURRENCY` | `BDT` | Shown at checkout |
| `ADMIN_TOKEN` | *(empty)* | **Required** to approve payments. Empty = admin API returns 503 |

The access code is a **shared secret with no per-person revocation**: once you
give it to someone, they can give it to anyone. Treat it as "free for people I
tell", not as a licence key. Rotate it by changing `FREE_ACCESS_CODE` — that
instantly stops new redemptions, though it does not revoke access already
granted (use the admin API for that). Guessing is capped at 8 attempts per
visitor per hour.

### Payment rails

Set only the ones you actually accept; the rest are hidden at checkout.

| Env var | Example |
|---|---|
| `PAY_BKASH` | `01712345678` |
| `PAY_NAGAD` | `01812345678` |
| `PAY_ROCKET` | `01912345678` |
| `PAY_BANK` | `City Bank · 1234567890` |
| `PAY_GPAY` | `you@okaxis` |

**These are manual, not automated.** bKash, Nagad and Rocket only issue
merchant API credentials to registered businesses (trade licence, TIN, company
bank account), which is a slow process and out of reach on day one. So:

1. The buyer sends you money and gets a TrxID from their confirmation SMS.
2. They submit that TrxID in the app. Their status becomes **pending** — this
   grants nothing, because a claim is not a payment.
3. You check it against your own bKash/bank statement.
4. You approve it in the admin console, which activates their subscription.

A TrxID can only be submitted once, so one real payment can't be recycled into
several subscriptions by people sharing a reference. Rejected references are
freed for resubmission, so an honest typo isn't fatal.

When you do get merchant API access, the automated version replaces step 3 —
call `billing.review(payment_id, approve=True, ...)` from a gateway webhook
instead of from the console. Nothing else changes.

### Approving payments

Open `admin.html` from the repo, enter your server URL and `ADMIN_TOKEN`. It
shows pending payments, revenue and active subscriber counts, and approves or
rejects with one click. The token is kept in that browser's local storage and
sent only to your own server — use a private profile on a shared machine.

> **Verify before approving.** The server cannot tell whether money actually
> arrived; it only records what the buyer typed. Approving without checking
> your statement is how you give away subscriptions.

### Selling inside the mobile apps

You cannot, not this way. Apple's Guideline 3.1.1 and Google Play's Billing
policy both require digital subscriptions consumed in the app to go through
**their** in-app purchase systems, which take 15–30%. A bKash flow inside the
iOS app is a guaranteed rejection.

The app already handles this: the upgrade screen is hidden in the packaged
mobile shell, and the web build sells normally. Subscriptions bought on the web
still work everywhere, because entitlement follows the visitor token. To sell
on mobile you would need to add StoreKit and Play Billing as separate rails.

### Running on a free plan

Free hosting gives you no persistent disk: the filesystem is wiped whenever the
instance sleeps or redeploys. Left alone that would delete every subscription
people paid for, which is why the blueprint ships with the disk commented out
and a snapshot mechanism instead.

After every payment, approval and code redemption, the billing tables are
mirrored to durable storage under the key `billing-backup`. On startup, if the
database is empty, that snapshot is loaded back. A wiped disk then costs
nothing — verified end to end: a paid customer keeps chatting after the
database file is deleted underneath a running server.

> **This only works if Google Drive is configured.** Without
> `GOOGLE_REFRESH_TOKEN`, storage falls back to local files, which the free
> plan wipes along with everything else — so the backup dies with the thing it
> was backing up. Do the Drive setup below *before* taking real money on a free
> plan, or pay for a disk instead.

Restore never overwrites newer data: existing rows win, so a stale snapshot
cannot roll a live database backwards. A failed backup is logged and ignored
rather than failing the purchase that triggered it — the customer has already
sent the money and the local database is still correct.

Two other things about free plans, neither of which the snapshot fixes: the
instance sleeps after inactivity, so the first visit of the day waits up to a
minute; and there is no uptime guarantee. To move to a paid disk later,
uncomment the `disk:` block in `render.yaml` and set `DB_PATH` back to
`/data/mindora.db`. The snapshot keeps working; it just stops being the only
thing standing between you and losing your subscribers.

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

`render.yaml` sits at the repo root (Render only looks there); on Render, "New → Blueprint" and point it at the repo.
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

Mindora → Settings → **Server address** → paste the URL → **Save &amp; test**. It
accepts a trailing slash or a missing `https://`. On success the header shows
**AI ready** and, once a `PAY_*` rail is set, an **Upgrade** button appears.

To make it the default for everyone instead of per-browser, set
`DEFAULT_BACKEND_URL` in `index.html` and run `node packaging/sync-web.mjs`.

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
