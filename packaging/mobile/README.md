# Mindora for Android and iOS

Capacitor wraps the web app in a native shell. The entire app is bundled into
the binary — it launches and works with no network, which is both a real
feature and the strongest defence against an App Store 4.2 rejection.

```bash
cd packaging/mobile
npm install
```

---

## Android → Google Play

### 1. Create the project

```bash
npm run add:android      # syncs the web app, adds Android, generates icons
npm run open:android     # opens Android Studio
```

`npm run add:android` runs `@capacitor/assets`, which expands
`resources/icon*.png` and `resources/splash*.png` into every density Android
wants. You never hand-place a mipmap.

### 2. Generate an upload key

**Do this once and never lose the file.** Google signs your app with a key it
holds, but your uploads must be signed with *this* key forever. Lose it and you
cannot update your own app — you have to publish a new listing and abandon your
install base.

```bash
keytool -genkey -v -keystore mindora-upload.keystore \
  -alias mindora -keyalg RSA -keysize 2048 -validity 10000
```

Back the `.keystore` up somewhere permanent and private. It is gitignored here
on purpose. Then create `android/keystore.properties`:

```properties
storeFile=../../mindora-upload.keystore
storePassword=YOUR_PASSWORD
keyAlias=mindora
keyPassword=YOUR_PASSWORD
```

And wire it into `android/app/build.gradle`, above `android {`:

```gradle
def keystoreProperties = new Properties()
def keystorePropertiesFile = rootProject.file("keystore.properties")
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(new FileInputStream(keystorePropertiesFile))
}
```

then inside `android { }`:

```gradle
signingConfigs {
    release {
        storeFile file(keystoreProperties['storeFile'])
        storePassword keystoreProperties['storePassword']
        keyAlias keystoreProperties['keyAlias']
        keyPassword keystoreProperties['keyPassword']
    }
}
buildTypes {
    release {
        signingConfig signingConfigs.release
        minifyEnabled true
        shrinkResources true
    }
}
```

### 3. Build the bundle

```bash
npm run build:android    # → android/app/build/outputs/bundle/release/app-release.aab
```

Play requires an **`.aab`**, not an `.apk`. Use `npm run build:android:apk` only
for sideloading to a test device.

### 4. Publish

1. Register at **play.google.com/console** — $25, once.
2. Create the app, then work through **Dashboard → Set up your app**: privacy
   policy URL, ads declaration, content rating questionnaire, target audience,
   data safety form.
3. **Data safety** is the fiddly one. Declare honestly: the app collects
   nothing itself; if you deploy the shared server, chat messages travel to
   Anthropic, and Drive sync stores a file in the user's own Drive. Both are
   described in `../store/privacy-policy.md`.
4. Upload the `.aab` to a **closed test** track first.
5. If your developer account is a personal one registered after Nov 2023, Play
   requires **12 testers opted in for 14 continuous days** before it will
   unlock production. Start this the day you have a working build.
6. Promote to production and submit. First review is typically a few days.

---

## iOS → App Store

**Requires a Mac with Xcode.** There is no supported way around this.

```bash
npm run add:ios
npm run open:ios          # opens Xcode
```

### In Xcode

1. Select the **App** target → **Signing & Capabilities**.
2. Set **Team** to your Apple Developer account and confirm the bundle
   identifier matches `appId` in `capacitor.config.ts` (`com.mindora.learn`).
   Register that identifier at **developer.apple.com → Identifiers** first.
3. Leave **Automatically manage signing** ticked unless you know why not.
4. Set **Deployment Target** to iOS 14 or later.
5. **Product → Archive**, then **Distribute App → App Store Connect**.

### In App Store Connect

1. Create the app record; bundle ID must match exactly.
2. Upload screenshots from `../store/screenshots/`:
   * **6.9" iPhone** — `ios-6.9-*.png` (1290×2796), required
   * **13" iPad** — `ios-ipad-13-*.png` (2064×2752), required if you claim iPad support
3. Fill in **App Privacy** ("nutrition labels") — same disclosures as Play.
4. Copy the listing text from `../store/listing.md`.
5. Submit. Review is usually 24–48 hours.

### Surviving Guideline 4.2

Apple rejects apps that are a website in a wrapper. Already in your favour: the
app is fully bundled and works offline, the catalog and all saved plans are
local, and there is no browser chrome anywhere.

**Add at least one genuinely native capability before you submit.** Local
notifications are the cheapest convincing one — a daily reminder about today's
lesson is a real phone feature a website cannot provide:

```bash
npm install @capacitor/local-notifications
npx cap sync
```

In your review notes, say plainly what works offline and name the native
features. Reviewers read that field. If you are rejected anyway, the reply is
usually a conversation rather than a dead end — respond in Resolution Center
describing the offline and native functionality.

---

## Testing on a real device

```bash
# Android, phone connected with USB debugging on
npm run sync && npx cap run android

# iOS, device connected and trusted
npm run sync && npx cap run ios
```

To debug the web layer: Android → `chrome://inspect`; iOS → Safari → Develop →
your device. Note that `webContentsDebuggingEnabled` is `false` in
`capacitor.config.ts` for release builds — flip it temporarily if you need to
inspect a release build.

---

## Known behaviour in the native shell

**Google Drive sign-in is hidden.** Google blocks OAuth started from an
embedded WebView, so the button cannot work. The Settings screen explains this
instead of offering it. Plans still sync through the shared server, and Drive
works normally on web and desktop.

**Videos need the localhost origin.** `capacitor.config.ts` sets
`androidScheme: "http"` / `iosScheme: "https"` with hostname `localhost`. Do not
change these to `file`. The YouTube IFrame Player API rejects a `null` origin,
and watch-progress tracking would break silently while videos kept playing.

**A stale `www/` is the most common bug.** If a change isn't showing up, you
forgot `npm run sync`.
