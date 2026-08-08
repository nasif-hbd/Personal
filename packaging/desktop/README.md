# Mindora for Windows, macOS and Linux

Electron shell around the same web app. Everything is bundled — the desktop app
never fetches the website.

```bash
cd packaging/desktop
npm install
npm start          # syncs the web app and runs it locally
```

---

## How it works

`main.js` starts a tiny HTTP server on `127.0.0.1` on a random free port and
loads the app from there, rather than from `file://`.

That is deliberate. A `file://` page has the origin `null`, and the YouTube
IFrame Player API refuses its postMessage handshake from a null origin — videos
would still play, but watch-progress tracking and auto-complete-at-100% would
silently stop working. A loopback origin also counts as a secure context, so
`localStorage` and the service worker behave exactly as on the website.

Security posture: `nodeIntegration` off, `contextIsolation` on, `sandbox` on,
top-level navigation pinned to the local origin, and every external link handed
to the user's real browser. The static server refuses any path that resolves
outside the bundled `app/` directory.

---

## Building installers

```bash
npm run dist:win      # NSIS installer + portable .exe, x64 and arm64
npm run dist:mac      # .dmg and .zip, Intel and Apple Silicon
npm run dist:mas      # Mac App Store .pkg
npm run dist:linux    # AppImage and .deb
npm run pack          # unpacked directory, for a quick sanity check
```

Output lands in `dist/`. You can only build macOS targets on a Mac; Windows and
Linux targets build anywhere.

`build/icon.png` (1024×1024, opaque) is the only icon you maintain —
electron-builder derives the Windows `.ico` and macOS `.icns` from it.

---

## Windows

`npm run dist:win` produces a working installer with no account and no
certificate. The catch: **SmartScreen shows "Windows protected your PC"** on
first run of an unsigned app, and most people click away rather than through.

Options, cheapest first:

* **Ship unsigned.** Fine for personal use. Users click *More info → Run anyway*.
* **Code signing certificate**, ~$200/year (Sectigo, DigiCert). Since June 2023
  these require hardware or cloud HSM storage, which complicates CI. An OV
  certificate still accumulates SmartScreen reputation slowly; an EV
  certificate gets instant trust and costs more.
* **Microsoft Store** — $19 once, and the Store signs for you, which sidesteps
  SmartScreen entirely. Package with:

  ```bash
  npx electron-builder --win appx
  ```

  Then submit through **partner.microsoft.com**. Store screenshots at 1366×768
  are already generated in `../store/screenshots/windows-*.png`.

---

## macOS

### Direct distribution (.dmg)

Unsigned, Gatekeeper refuses to open it at all on recent macOS — users must
right-click → Open, or clear the quarantine flag. To distribute properly you
need an Apple Developer account ($99/year) and notarization:

```bash
export APPLE_ID="you@example.com"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"   # appleid.apple.com
export APPLE_TEAM_ID="XXXXXXXXXX"
npm run dist:mac
```

electron-builder notarizes automatically when those three variables are
present. Generate the app-specific password at appleid.apple.com — your real
Apple password will not work.

`build/entitlements.mac.plist` carries the hardened-runtime entitlements
Electron needs (JIT and unsigned executable memory) plus network client and
server access for the loopback server.

### Mac App Store (.pkg)

```bash
npm run dist:mas
```

The MAS build is sandboxed and stricter — no JIT entitlements are permitted,
which is why `entitlements.mas.plist` is a separate, narrower file. The one
entitlement people miss is `com.apple.security.network.server`: without it the
sandbox blocks the loopback server from binding and the window opens blank.

You need a **Mac App Distribution** certificate and a **Mac Installer
Distribution** certificate, both from developer.apple.com. Upload with
Transporter or `xcrun altool`. Screenshots at 2880×1800 are already generated
in `../store/screenshots/mac-*.png`.

Be aware the Mac App Store review applies the same "is this just a website"
scrutiny as iOS — see the 4.2 notes in `../mobile/README.md`.

---

## Linux

```bash
npm run dist:linux    # AppImage (runs anywhere) and .deb
```

No signing, no store, no account. If you want it listed, Flathub is the usual
destination and takes a manifest rather than these artifacts.

---

## Before every release

1. `node ../sync-web.mjs` — or use the npm scripts, which do it for you.
2. Bump `version` in `package.json`.
3. Rebuild. Stale `app/` contents are the most common release bug.
