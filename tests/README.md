# Tests

Two suites, covering two different things.

| | What it covers | Run it with |
|---|---|---|
| `server/tests/` | The backend: billing, quota, YouTube proxy, leaderboard clamps, feedback storage and the mailer | `cd server && python3 -m pytest` |
| `tests/browser/` | The app a visitor actually uses: layout, navigation, quizzes, certificates, XP, the feedback dock, the service worker | `node tests/browser/run.mjs` |

The split matters. `index.html` is one file with ~6,000 lines of JavaScript wrapped in an
IIFE — nothing inside it is importable, and pytest cannot see any of it. Every bug the
browser suite has caught so far was invisible to both the backend tests and to reading
the diff:

- The certificate printed into a 236px strip, because `#app` is a grid and hiding the
  nav left its column behind.
- `--panel` was referenced in eight places and defined in none, so every
  `background: var(--panel)` was invalid and dropped — silently, in both themes.
- The certificate overflowed its own sheet, because the type was sized in `vw` while the
  sheet sizes to its container.
- The feedback dock shipped, worked, and was unreachable on phones.

## Running the browser suite

```bash
cd tests/browser
npm install                      # once
npx playwright install chromium  # once
node run.mjs                     # everything
```

`run.mjs` starts a static server for the app and a throwaway backend with fixture
credentials in a temporary database, runs each suite, tears both down, and **exits
non-zero if any assertion failed**. Nothing touches your real data, and no run leaves
rows behind for the next one to trip over.

```bash
node run.mjs --list          # what would run
node run.mjs --no-api        # only the suites that need no backend
node run.mjs awards quiz     # only suites whose filename matches
node run.mjs                 # 240 assertions across 15 suites
```

Screenshots land in `tests/browser/screenshots/` (git-ignored). They are not compared
against baselines — they are there so you can look at what the suite saw when something
fails, and so the certificate can be reviewed by eye.

Individual suites are ordinary Node scripts and can be run alone, against servers you
started yourself:

```bash
MINDORA_WEB_URL=http://127.0.0.1:8899 \
MINDORA_API_URL=http://127.0.0.1:8904 \
node tests/browser/awards.js
```

## Writing a new one

`harness.js` supplies `ok()`, `open()`, `shot()` and `withBrowser()`; `fixtures.js`
supplies plan and lesson shapes whose numbers the assertions depend on. Add the file to
the `SUITES` list in `run.mjs`, with `api: true` if it needs the backend.

Two rules, both learned the hard way:

**Drive the UI, never the internals.** The app is inside an IIFE precisely so nothing can
reach in. A test that pokes at internals proves nothing about what a visitor can do —
click what they click, read what they see. Where storage is the only honest window into
what the app saved, read `localStorage` rather than reaching for a function.

**A failed assertion must fail the run.** `ok()` sets the exit code. A suite that prints
FAIL and exits 0 is a suite nobody notices breaking.

## Fixture credentials

The backend the runner starts is configured entirely from `run.mjs`. None of it is real:
the Anthropic key is `sk-test-not-real`, the payment rails are placeholder numbers, and
`YOUTUBE_API_KEY` is deliberately empty — the browser suite stubs YouTube at the network
layer so a run never spends quota. A real search costs 100 units of a 10,000/day
allowance; the server side of that feature is covered by `server/tests/test_youtube.py`
instead.

Two settings there are not decorative:

- `ALLOWED_ORIGINS` must name the test origin. It defaults to the production origin only,
  so without this every fetch from the page is blocked by CORS and a working server looks
  like a dead one.
- `FREE_FOR_ALL=false`, because the checkout suite needs plans to pick from. With it on,
  the upgrade screen is a thank-you page and there is nothing to buy.
