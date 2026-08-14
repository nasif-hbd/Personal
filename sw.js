/* Mindora service worker — installable, offline-capable app shell.
 *
 * Bump CACHE whenever the shipped files change; old caches are deleted on
 * activate so a stale shell can never linger.
 *
 * What is deliberately NEVER cached:
 *   - the backend API (/api/…): responses are per-visitor and change constantly,
 *     so a cached copy could show someone stale — or another visitor's — state
 *   - Anthropic and YouTube: streaming and media, pointless and harmful to store
 * Everything else is cached so the app opens instantly and works offline.
 */
// Bumped whenever the shipped app changes. It had sat at v1 through the
// subscription, quiz, sidebar and layout work, so an installed copy could
// keep serving a shell from before any of it.
const CACHE = "mindora-v3";

// Precached so a cold, offline start still renders a usable app.
const SHELL = [
  "./",
  "./index.html",
  "./privacy.html",
  "./manifest.json",
  "./courses.json",
  "./icon.svg",
  "./icon-192.png",
  "./icon-512.png",
  "./icon-512-maskable.png",
];

const NEVER_CACHE = [
  "/api/",
  "api.anthropic.com",
  "youtube.com",
  "youtube-nocookie.com",
  "ytimg.com",
  "googleapis.com",
  "googlevideo.com",
  "accounts.google.com",
];

function bypass(url) {
  return NEVER_CACHE.some((fragment) => url.includes(fragment));
}

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      // addAll fails the whole install if any single file 404s; add
      // individually so one missing asset can't break installation.
      Promise.all(SHELL.map((path) => cache.add(path).catch(() => {})))
    )
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Lets the page tell a waiting worker to take over immediately.
self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = request.url;
  if (!url.startsWith("http") || bypass(url)) return; // straight to network

  // Navigations: prefer fresh, fall back to the cached shell when offline.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
          return response;
        })
        .catch(() =>
          caches.match(request).then((hit) => hit || caches.match("./index.html"))
        )
    );
    return;
  }

  // Everything else: serve from cache immediately, refresh in the background.
  event.respondWith(
    caches.match(request).then((hit) => {
      const network = fetch(request)
        .then((response) => {
          // Opaque cross-origin responses (fonts) are still worth caching;
          // genuine errors are not.
          if (response && (response.ok || response.type === "opaque")) {
            const copy = response.clone();
            caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
          }
          return response;
        })
        .catch(() => hit);
      return hit || network;
    })
  );
});
