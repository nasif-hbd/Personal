"use strict";
/* The client must prefer the server's key, never ask for one when the server
 * has it, and never call Google from the browser when it does.
 *
 * Both the health check and the search are stubbed at the network layer, so
 * this suite needs no API key and spends no quota — a real search costs 100
 * units of a 10,000/day allowance. The server side of the same feature is
 * covered by server/tests/test_youtube.py.
 */
const { API, ok, open, shot, withBrowser } = require("./harness");
const { linklessVideoPlan } = require("./fixtures");

const HEALTH = (youtube) => ({
  ok: true, chat: true, youtube, storage: "memory", quota: {},
  billing: { freeForAll: true, enabled: false },
});

async function boot(browser, settings) {
  // googleapis is deliberately left unblocked so the direct-to-Google call
  // can be counted rather than silently aborted.
  const { ctx, page } = await open(browser, {
    settings, plans: linklessVideoPlan(),
    block: /fonts\.g|youtube\.com|ytimg/,
    settle: 0, waitUntil: "domcontentloaded",
  });
  return { ctx, page };
}

/** Wire the counters before the page loads, then navigate. */
async function bootCounted(browser, settings, { serverHasKey }) {
  const hits = { google: 0, server: 0 };
  const { ctx, page } = await open(browser, {
    settings, plans: linklessVideoPlan(),
    block: /fonts\.g|youtube\.com|ytimg/,
    settle: 0, waitUntil: "commit",
  });
  await page.route("**://www.googleapis.com/youtube/v3/search*", (r) => {
    hits.google++;
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      items: [{ id: { videoId: "GOOGLEDIRECT" }, snippet: { channelTitle: "Direct" } }] }) });
  });
  await page.route("**/api/yt/search*", (r) => {
    hits.server++;
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      videoId: "VIASERVER01", channel: "3Blue1Brown", cached: false }) });
  });
  await page.route("**/api/health", (r) => r.fulfill({ status: 200,
    contentType: "application/json", body: JSON.stringify(HEALTH(serverHasKey)) }));
  await page.route("**/api/billing/**", (r) => r.fulfill({ status: 200,
    contentType: "application/json", body: JSON.stringify({
      freeForAll: true, plans: [], methods: [], codeEnabled: true, status: "none", active: false }) }));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2200);
  return { ctx, page, hits };
}

/** Open the plan, press play on the linkless lesson, return the iframe src. */
async function openLesson(page) {
  await page.evaluate(() => { location.hash = "#plans"; });
  await page.waitForTimeout(900);
  await page.locator(".plan-card").first().click();
  await page.waitForTimeout(1000);
  const play = page.locator('[data-action="play-inline"]').first();
  await play.waitFor({ timeout: 8000 });
  await play.click();
  const frame = page.locator("iframe[data-yt-class]").first();
  await frame.waitFor({ timeout: 15000 });
  return await frame.getAttribute("src");
}

withBrowser(async (browser) => {
  // 1. Server has the key: the client uses it and hides the field.
  {
    const { ctx, page, hits } = await bootCounted(browser,
      { theme: "dark", backendUrl: API, ytApiKey: "" }, { serverHasKey: true });

    await page.evaluate(() => { location.hash = "#settings"; });
    await page.waitForTimeout(1200);
    ok("settings hides the key field when the server has one",
      await page.locator("#ytApiKeyInput").count() === 0);
    const txt = await page.locator('.settings-section:has-text("In-app YouTube playback")').innerText();
    ok("and says the server is handling it", /server is doing this already/i.test(txt));

    // Drive the real UI — the app is inside an IIFE, so its internals are
    // deliberately unreachable and a test poking at them would prove nothing
    // about what a visitor can do.
    const src = await openLesson(page);
    ok("a lesson with no link resolves through the server",
      /VIASERVER01/.test(src || ""), src);
    ok("the browser never called Google directly", hits.google === 0, `google=${hits.google}`);
    ok("it went to the server instead", hits.server >= 1, `server=${hits.server}`);
    await shot(page, "yt-settings-server");
    await ctx.close();
  }

  // 2. No server: a personal key still works, and the field comes back.
  {
    const { ctx, page, hits } = await bootCounted(browser,
      { theme: "dark", backendUrl: "", ytApiKey: "AIza-personal" }, { serverHasKey: false });
    await page.evaluate(() => { location.hash = "#settings"; });
    await page.waitForTimeout(1200);
    ok("without a server the key field is offered", await page.locator("#ytApiKeyInput").count() === 1);

    const src = await openLesson(page);
    ok("the personal key still resolves", /GOOGLEDIRECT/.test(src || ""), src);
    ok("via Google directly", hits.google === 1 && hits.server === 0,
      `google=${hits.google} server=${hits.server}`);
    await ctx.close();
  }

  // 3. Neither: the app says so rather than offering a dead button.
  {
    const { ctx, page } = await boot(browser, { theme: "dark", backendUrl: "", ytApiKey: "" });
    await page.waitForTimeout(2200);
    await page.evaluate(() => { location.hash = "#plans"; });
    await page.waitForTimeout(900);
    await page.locator(".plan-card").first().click();
    await page.waitForTimeout(1000);
    ok("with no key anywhere, no play button is offered at all",
      await page.locator('[data-action="play-inline"]').count() === 0);
    await ctx.close();
  }
});
