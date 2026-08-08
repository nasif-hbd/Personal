# Store listing copy

Ready to paste. Character limits are the current store maximums — the text
below is already inside them.

---

## App name

**Mindora** (30 chars max on both stores — 7 used)

If `Mindora` is taken, Play allows a subtitle in the name field:
`Mindora: Learn with Dedication` (30 chars exactly).

## Subtitle / short description

**Apple subtitle** (30 max) and **Play short description** (80 max):

```
Learn with Dedication
```

Longer variant for Play's 80 characters:

```
AI study plans and real video courses, tracked lesson by lesson.
```

---

## Full description

Play allows 4000 characters; Apple allows 4000. This fits both.

```
Mindora turns "I want to learn this" into a plan you actually finish.

Tell Mindora what you want to learn and how much time you have. It builds a
day-by-day study plan — up to six months long — and fills it with real video
courses you can watch without ever leaving the app.

BUILT AROUND FINISHING, NOT STARTING

Most learning apps hand you a library and wish you luck. Mindora hands you
today's lesson. Open the app and you see exactly one thing: what to study now.

• Day-by-day plans from a week to six months
• Every lesson plays inside the app — no bouncing to a browser
• Progress tracks itself as you watch; a lesson ticks off at 100%
• Rest days built in, because plans without them get abandoned
• Streaks and completion tracking that reflect real watched minutes

A CATALOG THAT IS ACTUALLY CURATED

Hundreds of free courses across programming, mathematics, languages, exam
prep, science and design — each one checked automatically every week so dead
links and disabled videos get caught before you hit them.

Ten ready-made 14-day paths get you started in one tap, covering everything
from IELTS preparation to calculus to web development.

EDIT ANYTHING

Swap a video you don't like. Rewrite a lesson title. Add your own to-dos.
Change the schedule. Every single item in a plan can be edited by hand, or you
can ask the built-in AI to rework it for you.

WORKS OFFLINE

Your plans, your progress and the whole course catalog live on your device.
Open Mindora on a plane and everything is still there. Only video playback and
the AI need a connection, and the app tells you plainly when you're offline
rather than just failing.

YOUR DATA IS YOURS

Plans are stored on your device. Optional Google Drive sync keeps a copy in
your own Drive — in a single file the app creates, and nothing else in your
Drive is ever touched. Export everything to a file whenever you want.

Free. No account required to start. No ads.
```

---

## Keywords (Apple, 100 characters, comma-separated, no spaces)

```
study,learn,course,video,plan,tracker,education,schedule,skill,productivity,habit,IELTS,coding,math
```

98 characters. Do not repeat the app name — Apple indexes it already, and
repeating it wastes the budget.

---

## Categories

| Store | Primary | Secondary |
|---|---|---|
| Google Play | Education | — |
| App Store | Education | Productivity |
| Mac App Store | Education | Productivity |
| Microsoft Store | Education | — |

---

## Content rating answers

Both stores ask a questionnaire. For Mindora, honestly:

* No violence, sexuality, profanity, gambling, or drug references
* **No user-generated content shared between users** — plans are private
* **Yes, it links to third-party content** (YouTube videos)
* No in-app purchases, no ads
* Expected rating: **Everyone / 4+**

The YouTube question matters. Videos are embedded from YouTube, which means
content Mindora does not itself moderate. Disclose it; the rating stays
Everyone because the catalog is curated educational material.

---

## Age rating and target audience

Do **not** declare the app as targeting children under 13. It embeds YouTube
and would then fall under Play's Families policy and COPPA, which brings
requirements this app does not meet. Target audience: 13+.

---

## Support and marketing URLs

Both stores require a support URL that resolves.

| Field | Value |
|---|---|
| Support URL | `https://nasif-hbd.github.io/Personal/` |
| Marketing URL | `https://nasif-hbd.github.io/Personal/` |
| Privacy policy | must be its own public URL — see below |

**The privacy policy needs a real, separate, permanently reachable URL.**
Publish `privacy-policy.md` as a page — the simplest route is a
`privacy.html` in this repo, served at
`https://nasif-hbd.github.io/Personal/privacy.html`.

---

## Review notes

Paste into "Notes for Review" (Apple) / "App access" (Play). This field is
read, and it is your best defence against a 4.2 rejection:

```
Mindora is a fully offline-capable study planner. The entire application,
including the course catalog, is bundled in the binary — no login is required
and no server is needed to browse plans, edit lessons, or track progress.
Please try it in airplane mode to confirm.

Native functionality: local storage of all user plans, offline catalog search,
launch-screen and status-bar integration, and system share.

The AI planning feature requires a network connection and is optional; the app
is fully usable without it. Video lessons are embedded from YouTube.

No account is required. There is no paid content and no advertising.
```

If you add local notifications before submitting — recommended — name them
here too.

---

## Screenshots

Generated in `screenshots/`. Upload:

| Store | Files | Size |
|---|---|---|
| Play phone | `play-phone-*.png` | 1080×1920 |
| Play tablet | `play-tablet-*.png` | 1200×1920 |
| Play feature graphic | `play-feature-graphic-1024x500.png` | 1024×500, **required** |
| App Store 6.9" | `ios-6.9-*.png` | 1290×2796 |
| App Store 13" iPad | `ios-ipad-13-*.png` | 2064×2752 |
| Mac App Store | `mac-*.png` | 2880×1800 |
| Microsoft Store | `windows-*.png` | 1366×768 |

Regenerate after any UI change:

```bash
python3 -m http.server 8899 &
cd packaging/store && npm run shots
```

**Regenerate them after you deploy the backend.** Until `DEFAULT_BACKEND_URL`
is set, the header shows an "AI OFF" pill, and it is visible in every current
screenshot.
