#!/usr/bin/env node
/**
 * Generate every store screenshot at the exact pixel sizes Apple, Google,
 * Microsoft and the Mac App Store demand.
 *
 * The shots are of the *real* app, driven through its own UI — a featured path
 * is added and a few lessons ticked off, so the store listing shows a populated
 * app rather than empty states. Nothing is mocked or composited.
 *
 * Sizes come from each store's published requirements; the CSS viewport is
 * chosen so that viewport x deviceScaleFactor lands exactly on the required
 * pixel dimensions, which means the app lays itself out at a believable
 * device width instead of being scaled up from a desktop render.
 *
 *   npx playwright install chromium      # once
 *   node packaging/store/make-screenshots.mjs
 *
 * Serve the repo root first:  python3 -m http.server 8899
 */
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "screenshots");
const URL_BASE = process.env.MINDORA_URL || "http://127.0.0.1:8899";

const DEVICES = [
  // Google Play: 9:16, between 320px and 3840px on each side.
  { id: "play-phone", w: 360, h: 640, dsf: 3, mobile: true },
  { id: "play-tablet", w: 800, h: 1280, dsf: 1.5, mobile: true },
  // App Store 6.9" iPhone (iPhone 16 Pro Max) — the required modern size.
  { id: "ios-6.9", w: 430, h: 932, dsf: 3, mobile: true },
  // App Store 13" iPad.
  { id: "ios-ipad-13", w: 1032, h: 1376, dsf: 2, mobile: true },
  // Mac App Store accepts 2880x1800.
  { id: "mac", w: 1440, h: 900, dsf: 2, mobile: false },
  // Microsoft Store minimum is 1366x768.
  { id: "windows", w: 1366, h: 768, dsf: 1, mobile: false },
];

// Each screenshot is a step: seed once, then walk the app.
const SHOTS = [
  { id: "1-home", run: async (p) => scrollTop(p) },
  {
    id: "2-plan",
    run: async (p) => {
      await clickText(p, "Plans");
      const card = p.locator('[data-action="open-plan"], .plan-card a, .plan-card').first();
      if (await card.count()) await card.click({ timeout: 4000 }).catch(() => {});
      await p.waitForTimeout(900);
      await scrollTop(p);
    },
  },
  { id: "3-catalog", run: async (p) => { await clickText(p, "Catalog"); await scrollTop(p); } },
  { id: "4-today", run: async (p) => { await clickText(p, "Today"); await scrollTop(p); } },
];

const scrollTop = async (p) => {
  await p.evaluate(() => window.scrollTo(0, 0));
  await p.waitForTimeout(500);
};

async function clickText(page, label) {
  const nav = page.locator(`nav a, nav button, .nav a, .nav button, [data-view]`).filter({ hasText: new RegExp(`^\\s*${label}\\s*$`, "i") }).first();
  if (await nav.count()) {
    await nav.click({ timeout: 4000 }).catch(() => {});
  } else {
    await page.getByText(label, { exact: true }).first().click({ timeout: 4000 }).catch(() => {});
  }
  await page.waitForTimeout(900);
}

/** Add two featured paths and tick some lessons, so no shot is an empty state. */
async function seed(page) {
  await page.waitForTimeout(1200);
  const adds = page.locator('[data-action="add-featured-path"]');
  const n = Math.min(2, await adds.count());
  for (let i = 0; i < n; i++) {
    await adds.nth(i).click({ timeout: 4000 }).catch(() => {});
    await page.waitForTimeout(500);
    // Adding may pop a confirmation toast or navigate; return home either way.
    await page.evaluate(() => { location.hash = "#home"; });
    await page.waitForTimeout(400);
  }
  // Tick a handful of lessons so progress rings and streaks read as real.
  await page.evaluate(() => {
    const boxes = document.querySelectorAll('input[type="checkbox"]');
    [...boxes].slice(0, 4).forEach((b) => { if (!b.checked) b.click(); });
  });
  await page.waitForTimeout(600);
}

(async () => {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();

  for (const dev of DEVICES) {
    const ctx = await browser.newContext({
      viewport: { width: dev.w, height: dev.h },
      deviceScaleFactor: dev.dsf,
      isMobile: dev.mobile,
      hasTouch: dev.mobile,
      colorScheme: "dark",
    });
    // YouTube and Google Fonts are third-party: blocking them keeps the render
    // deterministic and stops a slow CDN from producing a half-painted shot.
    await ctx.route(/fonts\.g|youtube|ytimg/, (r) => r.abort());

    const page = await ctx.newPage();
    await page.goto(`${URL_BASE}/index.html`, { waitUntil: "domcontentloaded" });
    await seed(page);

    for (const shot of SHOTS) {
      await shot.run(page).catch(() => {});
      const file = join(OUT, `${dev.id}-${shot.id}.png`);
      await page.screenshot({ path: file });
      console.log(`${dev.id.padEnd(14)} ${shot.id.padEnd(10)} ${dev.w * dev.dsf}x${dev.h * dev.dsf}`);
      await page.evaluate(() => { location.hash = "#home"; });
      await page.waitForTimeout(500);
    }
    await ctx.close();
  }

  // Google Play feature graphic — 1024x500, required, and the one asset that
  // is a banner rather than a screenshot.
  const ctx = await browser.newContext({ viewport: { width: 1024, height: 500 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  // Inlined from disk rather than fetched: navigating to an .svg gives an SVG
  // document, and setContent only works on HTML ones.
  const mark = readFileSync(join(HERE, "..", "..", "icon.svg"), "utf8");
  await page.setContent(`<!doctype html><body style="margin:0;width:1024px;height:500px;
    background:linear-gradient(120deg,#08060f 0%,#141046 45%,#2a1160 100%);
    display:flex;align-items:center;gap:46px;padding:0 74px;box-sizing:border-box;
    font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#fff">
    <div style="width:190px;height:190px;background:#fff;border-radius:42px;padding:22px;
      box-sizing:border-box;flex:none">${mark}</div>
    <div>
      <div style="font-size:66px;font-weight:800;letter-spacing:-1.5px">Mindora</div>
      <div style="font-size:29px;font-weight:600;margin-top:6px;
        background:linear-gradient(90deg,#5b8cff,#c084fc);-webkit-background-clip:text;
        -webkit-text-fill-color:transparent">Learn with Dedication</div>
      <div style="font-size:21px;opacity:.72;margin-top:16px;line-height:1.45;max-width:520px">
        AI-built study plans. Real courses, played inside the app.</div>
    </div></body>`);
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(OUT, "play-feature-graphic-1024x500.png") });
  console.log("feature graphic  1024x500");
  await ctx.close();

  await browser.close();
  console.log(`\nAll assets written to ${OUT}`);
})();
