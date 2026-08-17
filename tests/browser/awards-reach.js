"use strict";
/* The other paths to Awards: a light theme, an empty state that invents
 * nothing, and the three ways to get there without typing a hash.
 */
const { ok, open, shot, withBrowser, noPageErrors } = require("./harness");

withBrowser(async (browser) => {
  // 1. Light theme, a real certificate, every plan finished.
  {
    const { ctx, page, errors } = await open(browser, {
      viewport: { width: 1280, height: 960 },
      hash: "#awards",
      settle: 1600,
      settings: { theme: "light", learnerName: "Nasif Hossain" },
      plans: [{
        id: "p1", title: "Calculus, start to finish", subject: "Mathematics",
        durationWeeks: 1, startDate: "2026-07-01", cards: [{ id: "k" }],
        weeks: [{ weekNumber: 1, days: [{ dayNumber: 1, classes: [
          { id: "a", title: "L1", type: "reading", durationMinutes: 70, done: true,
            doneAt: Date.UTC(2026, 6, 12), userNotes: [{ id: "n", text: "x", at: 1 }],
            quiz: [{ q: "?" }], quizAttempts: [{ score: 0.9 }] },
          { id: "b", title: "L2", type: "reading", durationMinutes: 50, done: true,
            doneAt: Date.UTC(2026, 6, 13), userNotes: [],
            quiz: [{ q: "?" }], quizAttempts: [{ score: 0.8 }] },
        ] }] }],
      }],
    });
    ok("light: certificate renders", await page.locator(".cert-sheet").count() === 1);
    ok("light: no name prompt once named", await page.locator(".name-prompt").count() === 0);
    ok("light: nothing on the way when every plan is done", await page.locator(".pending-row").count() === 0);
    await shot(page, "awards-light", { fullPage: true });
    noPageErrors(errors, "light: no uncaught page errors");
    await ctx.close();
  }

  // 2. Empty: no plans at all. Nothing may be invented.
  {
    const { ctx, page, errors } = await open(browser, {
      viewport: { width: 1280, height: 900 },
      hash: "#awards",
      settle: 1500,
      settings: { theme: "dark" },
    });
    ok("empty: explains how to earn one",
      (await page.locator(".chat-empty h2").innerText()).includes("certificate"));
    ok("empty: no certificate is invented", await page.locator(".cert-sheet").count() === 0);
    ok("empty: nav badge stays hidden", !await page.locator("#awardDot").isVisible());
    await shot(page, "awards-empty");

    // 3. Reachable without touching the hash: keyboard chord, then palette.
    await page.evaluate(() => { location.hash = "#home"; });
    await page.waitForTimeout(600);
    await page.keyboard.press("g");
    await page.keyboard.press("a");
    await page.waitForTimeout(600);
    ok("chord: g then a opens Awards",
      await page.evaluate(() => location.hash) === "#awards",
      await page.evaluate(() => location.hash));

    await page.evaluate(() => { location.hash = "#home"; });
    await page.waitForTimeout(500);
    await page.keyboard.press("Control+k");
    await page.waitForTimeout(400);
    await page.keyboard.type("award");
    await page.waitForTimeout(400);
    ok("palette: Awards is findable by name", await page.locator(".palette-item").count() >= 1);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(700);
    ok("palette: and it navigates there",
      await page.evaluate(() => location.hash) === "#awards",
      await page.evaluate(() => location.hash));
    noPageErrors(errors, "empty: no uncaught page errors");
    await ctx.close();
  }

  // 4. Phone: the More sheet route.
  {
    const { ctx, page, errors } = await open(browser, {
      viewport: { width: 390, height: 844 }, phone: true, settle: 1500,
      settings: { theme: "dark" },
    });
    await page.locator("#btnMore").tap();
    await page.waitForTimeout(500);
    await page.locator('.sheet-item[data-nav="awards"]').tap();
    await page.waitForTimeout(700);
    ok("phone: More then Awards works",
      await page.evaluate(() => location.hash) === "#awards",
      await page.evaluate(() => location.hash));
    await shot(page, "awards-empty-phone");
    noPageErrors(errors, "phone: no uncaught page errors");
    await ctx.close();
  }
});
