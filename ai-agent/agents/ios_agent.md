# iOS — why there's no `ios_agent.py`

Every other agent in this folder is a background process that polls the
cloud server and acts without the user touching the device. **That's not
possible on iOS**, and it's worth being upfront about why rather than
shipping something that quietly doesn't work:

- Apple does not allow third-party apps to run arbitrary background
  processes, and there is no public API for "launch app X" from outside
  that app. Everything below works *with* the sandbox, not around it.
- A plain script can't be "installed" the way `windows_agent.py` can — it
  has to be a Shortcut (or a full App Store app with entitlements this
  project doesn't have).

## The closest realistic equivalent: Shortcuts

1. **Build a Shortcut** in the Shortcuts app that does the useful part on
   the phone — e.g. "Get Contents of URL" against
   `GET {AGENT_SERVER_URL}/agents/ios-<id>/poll` with the `X-API-Key`
   header, then branches on the returned command:
   - `launch_app` → the shortcut's **Open App** action
   - `notify` → **Show Notification**
2. **Trigger it** one of two ways:
   - *Personal Automation* — "When I open Shortcuts" / "At a time of day" /
     "When I arrive at a location" can run the shortcut with **Ask Before
     Running** turned off. This is the only way to get something close to
     "runs on its own" on iOS, and it's still gated by a system automation
     trigger, not a cloud push.
   - *Tap a link* — the cloud side (e.g. a push notification, an email, an
     iMessage) can include a `shortcuts://run-shortcut?name=YourShortcut`
     URL. Tapping it runs the shortcut immediately. This is reliable but
     requires the user to tap — there is no silent equivalent.
3. **Report back**, if you want it: end the Shortcut with "Get Contents of
   URL" → `POST {AGENT_SERVER_URL}/agents/ios-<id>/ack`.

## What this gets you

- Reasonably close to `notify` (a Shortcuts notification instead of a native
  banner from this app — visually similar, one extra tap to set up).
- `launch_app` works, but only for apps with a Shortcuts "Open App" target
  or a URL scheme (most mainstream apps have one).
- Nothing silent, nothing that survives a device restart without the user
  re-enabling the automation, and nothing that bypasses Screen Time /
  Focus / notification permissions — as it should be.

If a fully autonomous iOS agent is a hard requirement, the honest options
are: (a) accept the Shortcuts-based, user-in-the-loop flow above, or
(b) build a real App Store app with a Notification Service Extension and
Background App Refresh — which is a separate, much larger project with its
own Apple Developer account, review process, and entitlements, not a script
that drops into this folder.
