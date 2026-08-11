# Mindora — complete build prompt

Paste this whole file into any AI tool to have it rebuild the interface. It
describes what the app **is**, what it **must keep doing**, and what it is free
to change. Nothing here is abbreviated.

---

## 1. What Mindora is

**Mindora — Learn with Dedication.** A learning app that turns "I want to learn
this" into a plan someone actually finishes.

You tell it a subject and how much time you have. It builds a day-by-day study
plan up to six months long, fills it with real free YouTube courses, and plays
every lesson inside the app. It watches how much of each video you actually
watch and ticks the lesson off at 100%.

**The one-line positioning:** most learning apps hand you a library and wish you
luck. Mindora hands you today's lesson.

**Who it's for:** self-taught learners, exam candidates (IELTS, SAT), and
students in places where paid courses are out of reach. Primary market is
Bangladesh — hence bKash/Nagad/Rocket payments and prices in taka.

**Business model:** everything is free except the AI assistant. Plans, the
348-course catalog, video playback, progress tracking, notes, flashcards and
offline access cost nothing. Only the chat that *builds* plans is paid, because
each message bills the owner at Anthropic.

---

## 2. Brand

The logo is a gradient **M** with a sparkle above a small figure standing at an
open book — learning, personified.

| Token | Dark | Light |
|---|---|---|
| accent | `#5b8cff` | `#2b4acb` |
| accent-2 | `#a855f7` | `#7c3aed` |
| accent-3 | `#c084fc` | `#9333ea` |
| background | `#08060f` | `#f7f7fb` |
| success | `#34d399` | `#059669` |
| danger | `#fb7185` | `#dc2626` |
| gold | `#fbbf24` | `#b45309` |

Type: system sans for UI, a monospace face for labels, dates, counts and
anything numeric. Uppercase mono micro-labels with wide letter-spacing are a
signature of the current design.

**Voice:** plain, direct, quietly confident. Never salesy, never cute. Errors
say what happened and what to do. Empty states explain the point of the screen
rather than apologising for being empty.

---

## 3. Every screen

### Home
Dashboard. A hero with the date and a headline, two buttons (New learning plan,
Browse catalog). A progress ring showing percent done across all plans, and stat
tiles: active plans, day streak, done this week. Then **Today's lessons**, then
**Your plans**, then the ten ready-made 14-day paths as cards.

### Chat
Where the AI builds and rewrites plans. Streams responses token by token.
Suggestion chips above the composer. **Gated:** without a subscription this
screen shows the upgrade pitch instead of the composer.

### Plans
List of plan cards with progress bars, then a detail view: title, subject,
language badge, progress, start date picker, and buttons for Chat about this
plan / Calendar / Print / Delete. Below that, week accordions containing day
rows, each with lesson cards and to-dos.

A **catch-up banner** appears when days are overdue: "N days behind — shift the
remaining schedule so the next unfinished day is today."

### Lesson card (the most important component)
Thumbnail with a play button → clicking swaps it for a real embedded YouTube
iframe that plays inline. Title, channel, duration. A watch-progress bar. Then
controls: Open (external), Notes, Make card, Edit, a reminder time input, and a
done checkbox.

Under it, an expandable **notes panel**. While a video plays, a note records the
exact second it was written; clicking that timestamp seeks the player back
there.

### Catalog
Search and subject filters over 348 courses. Chips for subjects.

### Today
Just what is scheduled today, plus today's to-dos. The screen you open every
morning.

### Review
Spaced repetition. One card at a time: question, then Show answer, then four
grades — Again / Hard / Good / Easy — each labelled with the interval it would
schedule. Below, a library of all cards.

### Insights
A six-month activity heatmap, a twelve-week bar chart, per-subject progress
bars, and totals: lessons done, hours studied, video actually watched, notes
written, cards, active plans.

### Upgrade
Pricing cards (monthly ৳499, yearly ৳4499), a feature list, payment method
picker (bKash, Nagad, Rocket, Bank transfer, Google Pay) with tap-to-copy
account numbers, a transaction-ID form, and a free-access code box.

### Settings
Server address, model and effort pickers, YouTube API key, Google Drive
connection, notifications, export/import, clear all data.

### Command palette
Ctrl/Cmd-K. Fuzzy search across every plan, lesson, course and command.

---

## 4. Data model

```js
plan = {
  id, title, subject, level, language,        // language: "en" | "bn" | "hi" | …
  durationWeeks,                              // 1–26
  dailyMinutes, daysOff: [], startDate,       // startDate "YYYY-MM-DD"
  weeks: [{
    weekNumber, theme,
    days: [{
      dayNumber,                              // 1–7
      classes: [ class ],
      todos: [{ id, text, done }],
    }],
  }],
  chat: [{ role, content }],
  cards: [ card ],
  createdAt, updatedAt,
}

class = {
  id, title,
  type,                    // "video" | "practice" | "reading" | "quiz" | "rest"
  resourceUrl, channel,
  altVideoUrl, altChannel, // fallback video when resourceUrl can't be embedded
  resolvedYtId,            // cached YouTube id
  durationMinutes,
  notes,                   // the AI's description of the lesson
  userNotes: [{ id, text, at, createdAt }],   // at = seconds into video, or null
  done, doneAt,            // doneAt = ms timestamp when ticked
  completionTime,          // "HH:MM" reminder time — NOT a completion date
  watchPct,                // 0..1
}

card = {                   // SM-2 spaced repetition
  id, classId, front, back,
  due, interval, ease, reps, lapses,
  createdAt, lastReviewed,
}
```

**Every date on screen is derived from `plan.startDate` plus the week and day
number.** Nothing stores absolute dates per lesson. Moving `startDate` moves the
whole remaining schedule — that is how catch-up works.

Storage: `localStorage`, keys prefixed `atlas_v1_` (`settings`, `plans`,
`activePlanId`). **Do not rename that prefix** — it would erase every existing
user's plans.

---

## 5. Backend API

Base URL is user-configurable in Settings. All optional: without it the app runs
fully offline except chat.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | config + remaining quota |
| `POST /api/chat` | streams Server-Sent Events from Claude |
| `GET/PUT /api/state` | per-visitor plan storage in the owner's Google Drive |
| `GET /api/billing/config` | plans, prices, payment rails |
| `GET /api/billing/entitlement` | am I subscribed? |
| `POST /api/billing/redeem` | free-access code |
| `POST /api/billing/claim` | submit a transaction ID |
| `GET /api/admin/payments` | owner: pending payments |
| `POST /api/admin/payments/{id}` | owner: approve or reject |

Headers: `x-atlas-visitor` (signed anonymous id, echoed back on every response
and re-sent), `x-atlas-code`, `x-atlas-admin`.

`POST /api/chat` returns **402** when unsubscribed. The client must catch that,
re-read entitlement, and show the paywall.

---

## 6. Hard constraints

These are not stylistic preferences. Breaking any of them breaks the app.

1. **One file.** The entire app is a single `index.html` — inline `<style>` and
   `<script>`, no build step, no framework, no bundler, no npm. It is served
   from GitHub Pages and must run by opening the file.

2. **No external JS/CSS dependencies.** The only third-party runtime code is the
   YouTube IFrame API and Google Identity Services, both loaded lazily. Fonts
   load non-blocking with a system fallback.

3. **YouTube needs a real origin.** Videos are embedded with `enablejsapi=1` and
   an `origin` parameter. On `file://` the origin is `null` and the player API
   refuses its handshake — video still plays but watch tracking dies silently.

4. **Re-rendering must never kill a playing video.** Marking a lesson done,
   saving a note, or expanding a panel patches the DOM in place. A full
   `renderView()` rebuilds iframes and restarts playback.

5. **Open state lives in `state`, not the DOM.** Expanded weeks, open players and
   open note panels are Sets in the state object, because any re-render would
   otherwise collapse them.

6. **The paywall is server-side.** The client hides the composer; the server
   returns 402. Client-side gating is decoration.

7. **`[hidden]` must mean hidden.** Any rule setting `display` outranks the
   attribute — a "hidden" fixed overlay then swallows every click on the page.
   Keep `[hidden]{display:none!important}`.

8. **Mobile is a bottom tab bar below 720px.** Eight tabs never fit a 390px
   header. Five destinations in a fixed bar (Home, Today, Plans, Review, More),
   the rest in a More sheet. Tap targets ≥44px. Respect safe-area insets.

9. **Both themes, three states.** Light, dark, and unstamped system default.
   Define the full palette on `:root`, redefine tokens under
   `@media (prefers-color-scheme: dark)` guarded by `:root:not([data-theme="light"])`,
   and again under `:root[data-theme="dark"]`.

10. **Never invent YouTube IDs.** Every video ID must come from the spreadsheet,
    a real search result, or the user. A fabricated ID is a dead lesson.

11. **Accessibility.** Pinch-zoom stays enabled, focus is visible and trapped in
    modals, `prefers-reduced-motion` is respected, everything works by keyboard.

12. **Offline.** A service worker precaches the shell and the full catalog. The
    backend API, Anthropic and YouTube are never cached.

---

## 7. What exists today

- `index.html` — 5,963 lines: 1,293 CSS, 4,537 JS, 181 functions
- `courses.json` — 348 courses, 10 ready-made 14-day paths
- Subjects: Math 55, Coding 40, Advanced Math 39, AI 36, Physics 34, English 31,
  Chemistry 24, Python 23, IELTS 22, Biology 16, Research 15, SAT 13
- `server/` — FastAPI: Claude proxy, spend caps, subscriptions, Drive storage
- `packaging/` — Capacitor (Android/iOS) and Electron (Windows/macOS/Linux)
- `admin.html` — owner console for approving payments

---

## 8. Your brief

Redesign the interface. Keep every constraint in section 6 and every behaviour
in section 3 — a learner must be able to do everything they can do now.

Beyond that, the visual direction is yours. The current design is dark
glassmorphism with gradient accents and mono micro-labels; you are not obliged
to keep it. What matters is that it looks like a product someone would pay for,
that today's lesson is never more than one glance away, and that it works as
well on a 390px phone as on a desktop.

Ship it as a single self-contained `index.html`.
