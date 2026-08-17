"use strict";
/* Shared plumbing for the browser suites.
 *
 * The app is wrapped in an IIFE, so nothing inside it is reachable from
 * page.evaluate. Every assertion in these suites therefore drives the real
 * UI — clicking what a visitor clicks, reading what a visitor sees. That is
 * slower than poking at internals, and it is the only thing that proves
 * anything about what a visitor can actually do.
 *
 * The rule that matters most here: a failed assertion sets the exit code.
 * A suite that prints FAIL and exits 0 is a suite nobody notices breaking.
 */
const fs = require("fs");
const path = require("path");

/** Playwright may be a devDependency here or installed globally. Accept both,
 *  and say what to do when it is neither. */
function loadPlaywright() {
  try {
    return require("playwright");
  } catch (_) { /* fall through */ }
  try {
    const root = require("child_process")
      .execSync("npm root -g", { encoding: "utf8" })
      .trim();
    return require(path.join(root, "playwright"));
  } catch (_) { /* fall through */ }
  console.error(
    "Playwright is not installed.\n" +
    "  cd tests/browser && npm install && npx playwright install chromium"
  );
  process.exit(2);
}

const { chromium } = loadPlaywright();

/** The static server and the API. The runner starts both; override to point
 *  a suite at something already running. */
const WEB = process.env.MINDORA_WEB_URL || "http://127.0.0.1:8899";
const API = process.env.MINDORA_API_URL || "http://127.0.0.1:8904";
const SHOTS = process.env.MINDORA_SHOTS || path.join(__dirname, "screenshots");
fs.mkdirSync(SHOTS, { recursive: true });

let passed = 0;
let failed = 0;
let skipped = 0;

/** One assertion. `extra` is printed only on failure, and should carry the
 *  value that made it fail — a bare FAIL costs another run to diagnose. */
function ok(label, cond, extra = "") {
  if (cond) {
    passed++;
    console.log(`PASS  ${label}`);
  } else {
    failed++;
    process.exitCode = 1;
    console.log(`FAIL  ${label}${extra ? `  (${extra})` : ""}`);
  }
  return !!cond;
}

/** For a check that genuinely cannot run here — a missing API key, not a
 *  missing server. Skips are reported, never counted as passes. */
function skip(label, reason) {
  skipped++;
  console.log(`SKIP  ${label}  (${reason})`);
}

function note(text) {
  console.log(`      ${text}`);
}

/** The page must not be throwing while it is being tested. Every suite
 *  watched for this by eye; this is that eye, written down. */
function noPageErrors(errors, label = "no uncaught page errors") {
  return ok(label, errors.length === 0, errors[0]);
}

/** Standard context + page: fonts and media blocked, storage seeded, errors
 *  collected. Returns the page plus the things a suite needs to clean up. */
async function open(browser, opts = {}) {
  const {
    viewport = { width: 1440, height: 1000 },
    phone = false,
    theme,                      // colorScheme for the OS-level preference
    reducedMotion,
    settings,                   // written to atlas_v1_settings
    plans,                      // written to atlas_v1_plans
    seedOnce = true,            // never overwrite what the app itself saved
    hash = "",
    serviceWorkers = "block",
    block = /fonts\.g|youtube|ytimg|googleapis/,
    waitUntil = "domcontentloaded",
    settle = 1800,
  } = opts;

  const ctx = await browser.newContext({
    viewport,
    isMobile: phone,
    hasTouch: phone,
    ...(theme ? { colorScheme: theme } : {}),
    ...(reducedMotion ? { reducedMotion } : {}),
    serviceWorkers,
  });
  if (block) await ctx.route(block, (r) => r.abort());

  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));

  if (settings || plans) {
    await page.addInitScript(
      ([s, pl, once]) => {
        if (once && localStorage.getItem("atlas_v1_settings")) return;
        if (s) localStorage.setItem("atlas_v1_settings", JSON.stringify(s));
        if (pl) localStorage.setItem("atlas_v1_plans", JSON.stringify(pl));
      },
      [settings || null, plans || null, seedOnce]
    );
  }

  await page.goto(`${WEB}/index.html${hash}`, { waitUntil });
  if (settle) await page.waitForTimeout(settle);
  return { ctx, page, errors };
}

async function shot(page, name, opts = {}) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), ...opts });
}

/** Launch, run, always close — so one thrown assertion cannot leave a
 *  browser behind and wedge the runner. */
async function withBrowser(fn) {
  const browser = await chromium.launch();
  try {
    await fn(browser);
  } finally {
    await browser.close();
  }
}

/** Suites that need the backend call this. A missing API is a failed run,
 *  not a skip: silently passing when the server is down is how a broken
 *  endpoint ships. */
async function requireApi() {
  const health = await apiHealth();
  if (!health) {
    console.log(`FAIL  the API is reachable at ${API}`);
    failed++;
    process.exitCode = 1;
    process.exit(1);
  }
  return health;
}

async function apiHealth() {
  try {
    const res = await fetch(`${API}/api/health`);
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

process.on("unhandledRejection", (err) => {
  console.log(`FAIL  the suite threw before it finished`);
  console.error(err);
  process.exit(1);
});

process.on("exit", () => {
  const bits = [`${passed} passed`, `${failed} failed`];
  if (skipped) bits.push(`${skipped} skipped`);
  console.log(`\n${bits.join(", ")}`);
});

module.exports = {
  WEB, API, SHOTS,
  ok, skip, note, noPageErrors,
  open, shot, withBrowser,
  requireApi, apiHealth,
  chromium,
};
