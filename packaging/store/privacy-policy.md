# Privacy Policy — Mindora

**Last updated: 19 August 2026**

Both app stores require a privacy policy at a public URL before they will
accept a submission. This is the source text — publish it as a web page and use
that URL in your listings. The repo already has a published copy at
`privacy.html`, linked from within the app's Settings screen; you can point
store listings at that same URL instead of republishing this file elsewhere,
as long as it stays live.

> **Before publishing:** replace the contact email below if you don't want your
> personal address in a public store listing, and delete this note. If you
> never deploy the shared server, delete the "AI planning features" and
> "YouTube lookup" paragraphs that describe it rather than describing something
> that doesn't happen. If you turn off the leaderboard, feedback, or payments
> features entirely, remove those sections too — keep this file matching what
> the shipped app actually does.

---

## The short version

Your study plans, progress and settings stay on your own device — there is no
account and no tracking. A handful of features are opt-in and send a specific,
limited piece of data somewhere else so the feature can work: joining the
leaderboard publishes a display name you choose, sending feedback sends your
message, claiming a paid plan sends the transaction reference so a human can
verify it. Each is described exactly below.

## What is stored on your device

Everything you create — your study plans, lesson progress, watch history,
notes, flashcards, completed items and settings — is stored locally in your
browser or app storage. It never leaves your device unless you turn on one of
the optional features below, or export it yourself.

Uninstalling the app, or clearing your browser data, deletes it permanently. We
cannot recover it for you, because we never had it.

## What we do not collect

We don't run analytics, advertising, or third-party trackers, and we don't
build profiles or sell data — we have none to sell. We don't ask for your real
name, phone number, location, contacts or photos, and nothing on your device is
uniquely fingerprinted for tracking.

A few features below send a specific, limited piece of information — a display
name you type in, a feedback message, a payment reference — because that's
what the feature does. Those are opt-in and covered individually; nothing is
collected passively in the background.

## Optional: AI planning features

If you use the AI to build or edit a study plan, or to generate a quiz for a
lesson, the text of your request and the relevant plan content are sent to
**Anthropic** (the Claude API) to generate a response. This happens only when
you send a message.

Where the app is configured to use a shared server, that request passes through
that server, which forwards it to Anthropic. The server records only anonymous
usage counters — how many requests came from a browser, so that rate limits can
be enforced. Those counters are not linked to any identity, and message content
is not stored on the server.

The assistant is instructed to stay focused on your study plan and
study-skills questions, and to decline requests that try to redirect it
elsewhere — that's a behavioral instruction given to the model, not a
data-handling change.

Anthropic's handling of the data it receives is governed by its own privacy
policy: <https://www.anthropic.com/legal/privacy>

If you never use the AI features, nothing is ever sent.

## Certificates

When you finish a plan, Mindora can generate a certificate. The name printed
on it is whatever you type into the name field — it is **stored only on your
device**, the same as the rest of your plan data, and is never sent to any
server.

## Optional: Leaderboard

The leaderboard is off by default. If you choose to join it, the **display
name you type in** (not your account or real name, since there is no
account), along with your XP, level, lesson count and streak, is sent to the
server and shown publicly on the board to anyone who opens it. These figures
are self-reported from your device's own progress record, and the board says
so. A random visitor token — not tied to your identity — lets the server
recognize your row when you return; it is not a login.

You can leave the leaderboard at any time, which removes your row from the
server.

## Optional: Feedback

If you open the feedback panel and send a message, the **message text**, the
**category you picked**, and — only if you fill it in — an **optional contact
address** are sent to the server, along with a little automatic context about
what screen you were on. The message is stored and the server attempts to
email it to the app's developer; if that fails, the message still waits
safely in storage. Feedback is visible only to the developer, never to other
visitors, and is capped per visitor per day.

## Optional: Payments & subscriptions

Mindora does not process card payments and never sees your card number, bank
login, or mobile-wallet PIN. Subscribing works by sending money through an
external payment method you already use (for example bKash, Nagad, or a bank
transfer), entirely outside the app, and then telling Mindora you did. What
you tell Mindora — the plan, the payment method, the amount, and the
**transaction reference number** from your payment confirmation, plus an
optional sender name or number — is sent to the server and stored so a human
can manually verify it against the real transaction. Payment records are
visible only to the developer.

## Optional: Google Drive sync

If you connect Google Drive, the app saves a single backup file containing your
plans to **your own Google Drive**. The app requests the narrowest permission
Google offers (`drive.file`), which grants access **only to the one file the
app itself creates**. It cannot see, read, or modify anything else in your
Drive.

That file is in your Drive, under your control. Delete it at any time, or
disconnect from the app's Settings screen to revoke access.

This feature is unavailable in the Android and iOS apps, because Google does
not permit its sign-in flow inside an app's embedded browser.

## Video content

Lessons play through embedded **YouTube** players. When a video loads, YouTube
receives the request directly and applies its own privacy practices, including
any cookies it sets. Mindora does not send YouTube any information about you.

When a lesson has no specific video attached, the app can ask its server to
look one up by searching YouTube on your behalf. That search query (the
lesson title and subject) is relayed using the app's own API key — never
yours — and the result is cached so the same lookup isn't repeated. No
personal information is included.

YouTube's privacy policy: <https://policies.google.com/privacy>

## Children & teens

Mindora is built to be usable by learners of many ages, including high-school
students, and is not directed specifically at children under 13. The app
doesn't ask anyone's age. Everything above applies the same way regardless of
age: a leaderboard display name, a feedback message, or a payment reference is
only ever sent if that specific feature is used. We'd encourage a parent or
guardian to be aware of a younger student using the optional leaderboard,
since a chosen display name is shown publicly.

## Data deletion

- **On-device data** (plans, progress, certificates, settings): delete the app
  or clear its site storage — permanent and immediate.
- **Leaderboard entry**: use "Leave the leaderboard" in the app at any time.
- **Google Drive backup**: delete the file from your Drive, or disconnect in
  Settings.
- **A feedback message or payment record already sent**: not self-service,
  since there's no account — contact us below and we'll remove it.

## Changes

If this policy changes, the date at the top changes with it, and the updated
version is published at the same URL.

## Contact

Questions about this policy, or a deletion request: **mdmukul666343@gmail.com**
