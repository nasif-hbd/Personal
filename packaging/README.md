# Shipping Mindora to the stores

Everything needed to publish Mindora on **Google Play**, the **App Store**,
**Windows**, and **macOS** — from one codebase. The web app in the repo root
stays the single source of truth; each platform wraps it.

```
repo root (index.html, courses.json, sw.js, icons)
        │
        └── packaging/sync-web.mjs
                 ├── mobile/www   → Capacitor → Android (.aab) + iOS (.ipa)
                 └── desktop/app  → Electron  → Windows (.exe) + macOS (.dmg)
```

Run the sync before **every** build, or you will ship a stale app:

```bash
node packaging/sync-web.mjs           # copy web app into both targets
node packaging/sync-web.mjs --check   # CI: fail if a target is stale
```

---

## Read this before you spend anything

Five things decide whether this is a weekend or a month. None of them are code.

**1. You cannot build the iOS or Mac apps without a Mac.** Xcode is macOS-only
and there is no way around it. Android, Windows and Linux builds work from any
machine. If you don't have a Mac, a rented cloud Mac (MacStadium, Scaleway) or
a CI runner with a macOS image is the usual route.

**2. Apple charges $99/year, Google charges $25 once.** The Google fee is a
one-time registration; Apple's is a subscription, and when it lapses your apps
are pulled from sale.

**3. Apple rejects thin web wrappers — App Store Review Guideline 4.2.** This
is the single biggest risk in this whole plan, and it is a real one. An app
that is "just a website in a shell" gets rejected. What is already done here to
survive that review: the entire app is bundled into the binary and runs with no
network, the catalog works offline, there is no visible browser chrome, and
native plugins are wired up. What you should add before submitting: something
genuinely native. Local notifications reminding a learner about today's lesson
is the cheapest convincing option. Expect at least one rejection round and
budget for it.

**4. Google now requires most new personal developer accounts to test with 12
people for 14 days before production access.** If your Play account was
registered as an individual after Nov 2023, you must recruit 12 testers who opt
into a closed test and keep it running two weeks. Start this early — it is
usually the longest pole in an Android launch.

**5. Deploy the backend first.** `DEFAULT_BACKEND_URL` in `index.html` is still
empty, so every build right now ships with AI switched off — the header shows
an "AI OFF" pill, and that will appear in your store screenshots. Deploy
`server/`, set the URL, then re-sync and regenerate screenshots.

---

## Cost and requirement summary

| Target | Account cost | Build machine | Signing |
|---|---|---|---|
| Google Play | $25 once | any | upload keystore you generate |
| App Store (iOS) | $99/year | **Mac + Xcode** | Apple certs, automatic in Xcode |
| Mac App Store | $99/year | **Mac + Xcode** | Apple certs + sandbox |
| macOS direct (.dmg) | $99/year for notarizing | **Mac** | Developer ID, or ship unsigned |
| Windows (.exe) | free | any | optional cert (~$200/yr) |
| Microsoft Store | $19 once | Windows | store-managed |

Windows and macOS builds work **unsigned**, but the OS shows a scary warning
before the first launch. That is tolerable for a personal project and not for a
public one.

---

## The four builds

Each has its own guide:

* **[mobile/README.md](mobile/README.md)** — Android and iOS via Capacitor
* **[desktop/README.md](desktop/README.md)** — Windows, macOS and Linux via Electron
* **[store/listing.md](store/listing.md)** — titles, descriptions, keywords, category answers
* **[store/privacy-policy.md](store/privacy-policy.md)** — required by both stores; must be at a public URL

### Quick start

```bash
# Desktop, running locally in 30 seconds
cd packaging/desktop && npm install && npm start

# Android project
cd packaging/mobile && npm install && npm run add:android && npm run open:android

# iOS project (Mac only)
cd packaging/mobile && npm install && npm run add:ios && npm run open:ios

# Store screenshots + Play feature graphic
python3 -m http.server 8899 &          # serve the repo root
cd packaging/store && npm install && npm run shots
```

---

## What is already generated

| File | Used by |
|---|---|
| `mobile/resources/icon.png` (1024, opaque) | iOS marketing icon, Play listing |
| `mobile/resources/icon-foreground.png` | Android adaptive icon, mark inside the 66% safe zone |
| `mobile/resources/icon-background.png` | Android adaptive icon backdrop |
| `mobile/resources/splash.png` (2732²) | launch screen, both platforms |
| `desktop/build/icon.png` (1024, opaque) | electron-builder derives `.ico` and `.icns` |
| `store/screenshots/*.png` | 24 screenshots at every required store size |
| `store/screenshots/play-feature-graphic-1024x500.png` | Google Play, mandatory |

The marketing icon is deliberately **opaque** — Apple rejects an icon with an
alpha channel — while the Android adaptive foreground deliberately **keeps**
its alpha, because the launcher composites it over the background layer. Both
are rendered from `icon.svg`, so the mark can never drift between platforms.
Re-render them any time with:

```bash
node packaging/store/make-screenshots.mjs
```

---

## Things that behave differently once packaged

**Google Drive sign-in is hidden in the mobile app.** Google refuses OAuth
initiated from an embedded WebView (`disallowed_useragent`), so the button
cannot work there. Rather than ship a button guaranteed to fail, the Settings
screen explains this and points at the shared server, which syncs native plans
anyway. Drive still works normally on the web and on desktop.

**Videos need a real origin.** Both shells serve the app over `http(s)://
localhost` rather than `file://`. This is not cosmetic: on `file://` the page
origin is `null`, and the YouTube IFrame Player API refuses its postMessage
handshake — videos would still play, but watch-progress tracking and
auto-complete would quietly stop working. The desktop shell runs a tiny
loopback HTTP server on a random free port for exactly this reason.

**Store review needs your backend live.** Reviewers will open the app and try
the AI. If the backend is down or its daily cap is exhausted during review, you
get rejected for "broken functionality". Raise the caps for review week.

---

## Version bumps

Three files carry the version, and the stores reject uploads that reuse a
number:

| File | Field |
|---|---|
| `desktop/package.json` | `version` |
| `mobile/android/app/build.gradle` | `versionCode` (integer, must increase) and `versionName` |
| `mobile/ios/App/App.xcodeproj` | `MARKETING_VERSION` and `CURRENT_PROJECT_VERSION` |

`versionCode` is the one people forget. Play rejects any upload whose
`versionCode` is not strictly greater than the last — bump it every single time.
