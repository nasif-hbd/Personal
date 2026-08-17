"use strict";
/* The everyday machinery: command palette, keyboard chords, notes that
 * survive a re-render, spaced repetition that schedules forward, and a
 * calendar export that a real calendar will accept.
 */
const { ok, open, shot, withBrowser, SHOTS, note } = require("./harness");
const fs = require("fs");
const path = require("path");

withBrowser(async (browser) => {
  const { ctx, page, errors } = await open(browser, {
    viewport: { width: 1340, height: 1000 }, theme: "dark", settle: 1600,
  });

  // Seed two plans.
  const adds = page.locator('[data-action="add-featured-path"]');
  for (let i = 0; i < 2; i++) {
    await adds.nth(i).click();
    await page.waitForTimeout(400);
    await page.evaluate(() => { location.hash = "#home"; });
    await page.waitForTimeout(300);
  }

  // --- command palette ---------------------------------------------------
  await page.keyboard.press("Control+k");
  await page.waitForTimeout(500);
  ok("palette opens on Ctrl+K", await page.locator("#palette.open").count() > 0);
  await page.locator("#paletteInput").fill("insi");
  await page.waitForTimeout(400);
  const first = await page.locator(".palette-item").first().innerText();
  ok("fuzzy search finds Insights", /Insights/i.test(first), first.replace(/\n/g, " "));
  await shot(page, "f-palette");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(900);
  ok("palette Enter navigates", /insights/i.test(await page.locator("#topTitle").innerText()));

  // --- insights ----------------------------------------------------------
  ok("heatmap rendered", await page.locator(".heat-day").count() > 150,
    (await page.locator(".heat-day").count()) + " cells");
  ok("weekly bars rendered", await page.locator(".bar-col").count() === 12);
  await shot(page, "f-insights", { fullPage: true });

  // --- keyboard chord ----------------------------------------------------
  await page.keyboard.press("g");
  await page.keyboard.press("p");
  await page.waitForTimeout(700);
  ok("chord g+p goes to Plans", /plans/i.test(await page.locator("#topTitle").innerText()));

  await page.locator('[data-action="open-plan"], .plan-card').first().click();
  await page.waitForTimeout(800);

  // --- notes -------------------------------------------------------------
  await page.locator('[data-action="toggle-notes"]').first().click();
  await page.waitForTimeout(400);
  await page.locator(".note-input").first().fill("Limits are about approach, not arrival.");
  await page.locator('[data-action="save-note"]').first().click();
  await page.waitForTimeout(600);
  ok("note saved and shown", await page.locator(".note-text").count() >= 1);
  ok("note badge appears", await page.locator(".note-count").count() >= 1);
  await shot(page, "f-notes");

  // Notes must survive the view being torn down and rebuilt.
  await page.evaluate(() => { location.hash = "#home"; });
  await page.waitForTimeout(400);
  await page.evaluate(() => { location.hash = "#plans"; });
  await page.waitForTimeout(600);
  const persisted = await page.evaluate(() => {
    const plans = JSON.parse(localStorage.getItem("atlas_v1_plans") || "[]");
    return plans.some((p) => (p.weeks || []).some((w) => (w.days || []).some((d) =>
      (d.classes || []).some((c) => Array.isArray(c.userNotes) && c.userNotes.length))));
  });
  ok("note persisted to storage", persisted);

  // --- recall cards ------------------------------------------------------
  await page.locator('[data-action="open-plan"], .plan-card').first().click();
  await page.waitForTimeout(700);
  await page.locator('[data-action="add-card"]').first().click();
  await page.waitForTimeout(500);
  await page.locator("#cardFront").fill("What is a limit?");
  await page.locator("#cardBack").fill("The value a function approaches as input nears a point.");
  await page.locator("#btnSaveCard").click();
  await page.waitForTimeout(700);
  ok("due badge shows after adding card", await page.locator("#dueDot:not([hidden])").count() > 0);

  await page.evaluate(() => { location.hash = "#review"; });
  await page.waitForTimeout(700);
  await page.locator("#btnStartReview").click();
  await page.waitForTimeout(600);
  ok("review shows the question",
    (await page.locator(".review-face.front p").innerText()).includes("limit"));
  await page.keyboard.press(" ");
  await page.waitForTimeout(500);
  ok("space reveals answer", await page.locator(".review-face.back").count() > 0);
  const gradeLabels = await page.locator(".grade span").allInnerTexts();
  ok("grade buttons preview intervals", gradeLabels.length === 4, gradeLabels.join(" | "));
  await shot(page, "f-review");
  await page.keyboard.press("3");
  await page.waitForTimeout(700);
  const sched = await page.evaluate(() => {
    const plans = JSON.parse(localStorage.getItem("atlas_v1_plans") || "[]");
    const c = plans.flatMap((p) => p.cards || [])[0];
    return c ? { reps: c.reps, interval: c.interval, future: c.due > Date.now() } : null;
  });
  ok("grading schedules the card forward", !!sched && sched.reps === 1 && sched.future,
    JSON.stringify(sched));

  // --- ics export --------------------------------------------------------
  // planToICS is module-scoped inside the app's IIFE, so the only way to
  // check it is to take the download a visitor would get.
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 8000 }).catch(() => null),
    page.evaluate(() => { location.hash = "#plans"; }).then(async () => {
      await new Promise((r) => setTimeout(r, 500));
      await page.locator('[data-action="open-plan"], .plan-card').first().click();
      await new Promise((r) => setTimeout(r, 600));
      await page.locator('[data-action="export-ics"]').first().click();
    }),
  ]);
  ok("calendar file downloads", !!download, download ? download.suggestedFilename() : "no download event");
  if (download) {
    const saved = path.join(SHOTS, "plan.ics");
    await download.saveAs(saved);
    const body = fs.readFileSync(saved, "utf8");
    ok("ics is well-formed",
      body.startsWith("BEGIN:VCALENDAR") && body.trim().endsWith("END:VCALENDAR"));
    ok("ics has events", (body.match(/BEGIN:VEVENT/g) || []).length > 0,
      (body.match(/BEGIN:VEVENT/g) || []).length + " events");
    // RFC 5545 folds at 75 octets; a longer line is rejected outright by
    // some calendar clients rather than shown truncated.
    ok("ics lines within 75 octets", body.split("\r\n").every((l) => l.length <= 75),
      "max " + Math.max(...body.split("\r\n").map((l) => l.length)));
  }

  // --- shortcuts modal ---------------------------------------------------
  await page.keyboard.press("?");
  await page.waitForTimeout(500);
  ok("? opens shortcuts", await page.locator("#scTitle").count() > 0);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);

  if (errors.length) note(`page errors: ${errors.slice(0, 3).join(" | ")}`);
  await ctx.close();
});
