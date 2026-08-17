#!/usr/bin/env node
/* Runs the browser suites against a throwaway copy of the whole stack.
 *
 *   node tests/browser/run.mjs              every suite
 *   node tests/browser/run.mjs awards quiz  only suites whose name matches
 *   node tests/browser/run.mjs --no-api     skip the ones needing a backend
 *   node tests/browser/run.mjs --list       show what would run
 *
 * The API is started with fixture credentials in a temporary database, so a
 * run never touches your real data and never leaves rows behind — a
 * leaderboard that persisted between runs was a real source of confusing
 * failures before this existed.
 */
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { createReadStream, existsSync, statSync, mkdtempSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, extname, normalize } from "node:path";
import { tmpdir } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..");
const WEB_PORT = Number(process.env.MINDORA_WEB_PORT || 8899);
const API_PORT = Number(process.env.MINDORA_API_PORT || 8904);

/** `api: true` means the suite talks to the real backend and must not be
 *  quietly skipped when it is missing. */
const SUITES = [
  { file: "layout.js", api: false },
  { file: "theme-tokens.js", api: false },
  { file: "stillness.js", api: false },
  { file: "depth.js", api: false },
  { file: "features.js", api: false },
  { file: "quiz.js", api: false },
  { file: "quiz-auto.js", api: false },
  { file: "youtube.js", api: false },
  { file: "service-worker.js", api: false },
  { file: "awards.js", api: false },
  { file: "awards-reach.js", api: false },
  { file: "feedback-nav.js", api: false },
  { file: "xp-leaderboard.js", api: true },
  { file: "feedback.js", api: true },
  { file: "payments.js", api: true },
];

const args = process.argv.slice(2);
const noApi = args.includes("--no-api");
const listOnly = args.includes("--list");
const filters = args.filter((a) => !a.startsWith("--"));

const selected = SUITES.filter((s) =>
  (!filters.length || filters.some((f) => s.file.includes(f))) &&
  (!noApi || !s.api));

if (listOnly) {
  for (const s of selected) console.log(`${s.file}${s.api ? "  (needs api)" : ""}`);
  process.exit(0);
}
if (!selected.length) {
  console.error(`No suite matches ${filters.join(", ")}. Try --list.`);
  process.exit(2);
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".webmanifest": "application/manifest+json",
};

/** Static server for the app itself. Service workers need a real origin, so
 *  file:// is not an option. */
function serveStatic(port) {
  const server = createServer((req, res) => {
    const url = decodeURIComponent(req.url.split("?")[0]);
    const rel = normalize(url).replace(/^(\.\.[/\\])+/, "");
    let file = join(ROOT, rel === "/" ? "index.html" : rel);
    if (existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
    if (!file.startsWith(ROOT) || !existsSync(file)) {
      res.writeHead(404).end("not found");
      return;
    }
    res.writeHead(200, {
      "content-type": MIME[extname(file)] || "application/octet-stream",
      // The worker caches aggressively; the test server must not add a
      // second layer of staleness on top of it.
      "cache-control": "no-store",
      "service-worker-allowed": "/",
    });
    createReadStream(file).pipe(res);
  });
  return new Promise((resolve, reject) => {
    server.on("error", reject);
    server.listen(port, "127.0.0.1", () => resolve(server));
  });
}

/** The backend, with fixture settings the suites assert against. Nothing
 *  here is a real credential. */
function startApi(port, dbDir) {
  const env = {
    ...process.env,
    ANTHROPIC_API_KEY: "sk-test-not-real",
    SECRET_KEY: "browser-suite-secret",
    DB_PATH: join(dbDir, "test.db"),
    // The CORS allow-list defaults to the production origin only, so the
    // test origin has to be named or every fetch from the page is blocked
    // and the suites see a working server as a dead one.
    ALLOWED_ORIGINS: `http://127.0.0.1:${WEB_PORT},http://localhost:${WEB_PORT}`,
    // Subscriptions on, because the checkout suite needs plans to pick
    // from — with FREE_FOR_ALL the upgrade screen is a thank-you page
    // instead, and there is nothing to buy.
    FREE_FOR_ALL: "false",
    ADMIN_TOKEN: "browser-suite-admin",
    // Feedback: stored, capped at five a day, never actually emailed.
    FEEDBACK_TO: "owner@example.com",
    FEEDBACK_DAILY_CAP: "5",
    RESEND_API_KEY: "",
    SMTP_HOST: "",
    // Payment rails the checkout suite expects to find configured.
    PRICE_MONTHLY: "499",
    PAY_BKASH: "01700000000",
    PAY_BKASH_LINK: "https://shop.bkash.com/mindora/pay",
    PAY_NAGAD: "01700000000",
    PAY_GPAY: "mindora@upi",
    // No YouTube key: the browser suite stubs that entirely and must never
    // spend real quota.
    YOUTUBE_API_KEY: "",
    PYTHONUNBUFFERED: "1",
  };
  const proc = spawn("python3",
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--log-level", "warning"],
    { cwd: join(ROOT, "server"), env, stdio: ["ignore", "pipe", "pipe"] });
  const log = [];
  proc.stdout.on("data", (d) => log.push(String(d)));
  proc.stderr.on("data", (d) => log.push(String(d)));
  proc.on("exit", (code) => {
    if (code !== 0 && !proc.killed) {
      console.error(`The API exited with ${code}:\n${log.join("").slice(-1500)}`);
    }
  });
  return proc;
}

async function waitFor(url, label, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch (_) { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  console.error(`${label} never came up at ${url}`);
  return false;
}

function runSuite(file) {
  return new Promise((resolve) => {
    const proc = spawn(process.execPath, [join(HERE, file)], {
      stdio: "inherit",
      env: {
        ...process.env,
        MINDORA_WEB_URL: `http://127.0.0.1:${WEB_PORT}`,
        MINDORA_API_URL: `http://127.0.0.1:${API_PORT}`,
      },
    });
    proc.on("exit", (code) => resolve(code === 0));
  });
}

const dbDir = mkdtempSync(join(tmpdir(), "mindora-browser-"));
let web = null;
let api = null;

async function shutdown() {
  if (api) { api.kill("SIGTERM"); }
  if (web) { web.close(); }
  try { rmSync(dbDir, { recursive: true, force: true }); } catch (_) { /* best effort */ }
}
process.on("SIGINT", async () => { await shutdown(); process.exit(130); });

const results = [];
try {
  web = await serveStatic(WEB_PORT);
  console.log(`app      http://127.0.0.1:${WEB_PORT}`);

  const needsApi = selected.some((s) => s.api);
  if (needsApi) {
    api = startApi(API_PORT, dbDir);
    const up = await waitFor(`http://127.0.0.1:${API_PORT}/api/health`, "The API");
    if (!up) {
      await shutdown();
      process.exit(1);
    }
    console.log(`api      http://127.0.0.1:${API_PORT}  (fixture data in ${dbDir})`);
  }
  console.log("");

  for (const suite of selected) {
    console.log(`\n── ${suite.file} ${"─".repeat(Math.max(0, 56 - suite.file.length))}`);
    results.push([suite.file, await runSuite(suite.file)]);
  }
} finally {
  await shutdown();
}

const failedSuites = results.filter(([, passing]) => !passing);
console.log(`\n${"=".repeat(60)}`);
for (const [file, passing] of results) console.log(`${passing ? "ok  " : "FAIL"}  ${file}`);
console.log(`\n${results.length - failedSuites.length}/${results.length} suites passed`);
process.exit(failedSuites.length ? 1 : 0);
