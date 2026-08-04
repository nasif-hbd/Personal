# Atlas tooling

Python that keeps the app's course data honest and up to date. It runs in CI
and on your machine — **not** in the browser. GitHub Pages serves static files
only, so nothing here executes at page load; it produces `courses.json`, which
the app then fetches.

```
data/Global_Learning_Resource_Directory.xlsx ─┐
                                              ├─ build_catalog.py ─→ courses.json ─→ app
data/paths.json ──────────────────────────────┘                          │
                                                     check_links.py ←────┘
```

## Setup

```bash
pip install -r tools/requirements.txt
```

## build_catalog.py — course data as data

Turns the spreadsheet and the curated-path definitions into the single
`courses.json` the app reads at runtime.

```bash
python tools/build_catalog.py           # regenerate courses.json
python tools/build_catalog.py --check   # CI: fail if it's stale
```

* **To add or change a course in the browsable catalog**, edit the spreadsheet
  in `data/` and re-run. No application code changes.
* **To change a 14-day path**, edit `data/paths.json` and re-run. Each path
  needs exactly 14 days; the script refuses to build otherwise.
* A day is either `{"rest": true}` or `{"classes": [...], "todos": [...]}`.
  A class with an `altVideoUrl` gets an in-app YouTube player even when its
  main `resourceUrl` can't be embedded.

`courses.json` is generated — don't hand-edit it. The paths compiled into
`learning-atlas.html` remain as an offline fallback for when the fetch can't
run (opening the file directly from disk), so the app never hard-depends on it.

## check_links.py — catch rotting links

Free resources disappear: videos get deleted, made private, or have embedding
switched off. This finds those before a learner does.

```bash
python tools/check_links.py               # everything
python tools/check_links.py --paths-only  # just the 14-day paths (fast)
```

YouTube links are checked through the **oEmbed endpoint**, which is
authoritative — a 404 there means the video is genuinely gone, not merely slow.

Exit status is non-zero **only for definitive failures**:

| Status | Meaning | Fails the build |
|---|---|---|
| `ok` | resolves fine | no |
| `dead` | 404/410 — gone or private | **yes** |
| `embedding-disabled` | 401/403 from oEmbed — can't be iframed | **yes** |
| `unreachable` | network error, timeout, bot-blocking | no |

That last row matters: a flaky runner or a firewalled network reports
`unreachable`, never a false alarm. Full results land in `link-report.json`.

## Automation

| Workflow | Trigger | Does |
|---|---|---|
| `.github/workflows/catalog.yml` | push touching `data/` or the builder | rebuilds `courses.json` and commits it |
| `.github/workflows/link-check.yml` | Mondays 05:17 UTC, or manually | verifies every link; opens/updates a `broken-links` issue listing exactly what died |

The link-check issue is reused rather than re-filed each week, so a persistent
problem doesn't bury you in duplicates.

## Installing Atlas as an app

Atlas is a PWA — the same build installs on phone, tablet and desktop:

* **Android / Chrome / Edge** — "Install app" from the address bar or ⋮ menu
* **iOS Safari** — Share → Add to Home Screen
* **Desktop Chrome/Edge** — install icon in the address bar

Once installed it launches without browser chrome, respects notches and the
home indicator, and **works offline**: the app shell and the full course
catalog are cached, so saved plans and browsing stay available with no
network. Chat and video need a connection, and the app says so with an
"Offline" pill rather than failing silently.

Long-press the installed icon for shortcuts straight to Today, Plans or the
Catalog. When a new version ships, the app offers a Reload rather than
stranding you on a cached build.

Deliberately never cached: the backend API, Anthropic, and YouTube — those are
per-visitor or streaming, and a cached copy would be wrong.
