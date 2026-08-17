"use strict";
/* Certificates: issued only for finished work, stating figures that came
 * from the record rather than from a guess, and printing as a sheet on its
 * own rather than as a slice of the app.
 */
const { ok, open, shot, withBrowser, noPageErrors } = require("./harness");
const { plan } = require("./fixtures");

withBrowser(async (browser) => {
  const settings = { theme: "dark", model: "claude-sonnet-5", effort: "medium",
    backendUrl: "", apiKey: "", accessCode: "", driveConnected: false, ytApiKey: "" };
  const plans = [
    plan("p-full", "Calculus, start to finish", 4, 4),
    plan("p-half", "Linear algebra basics", 1, 4),
  ];

  for (const [name, viewport, phone, theme] of [
    ["desktop", { width: 1440, height: 1000 }, false, "dark"],
    ["phone", { width: 390, height: 844 }, true, "light"],
  ]) {
    // Seeded once: re-seeding on reload would overwrite what the app just
    // saved, which is precisely what the reload assertions below check.
    const { ctx, page, errors } = await open(browser, {
      viewport, phone, theme, settings, plans, hash: "#awards",
    });

    ok(`${name}: Awards opens on its own hash`, await page.locator(".awards-wrap").count() === 1);
    ok(`${name}: one certificate for the finished plan`, await page.locator(".cert").count() === 1);
    ok(`${name}: the unfinished plan gets none`, await page.locator('[data-cert="p-half"]').count() === 0);
    ok(`${name}: it names the finished course`,
      (await page.locator(".cert-course").innerText()).includes("Calculus"));
    ok(`${name}: the partial plan is listed as on the way`,
      (await page.locator(".pending-row").innerText()).includes("Linear algebra"));

    // Stats come from the recorded work, never from a guess.
    const stats = await page.locator(".cert-stats").innerText();
    ok(`${name}: lesson count matches what was ticked`, /\b4\b[\s\S]*LESSONS/i.test(stats), stats.replace(/\n/g, " "));
    ok(`${name}: hours come from the minutes watched`, /\b3\b[\s\S]*HOURS/i.test(stats));
    ok(`${name}: quiz average is the real mean`, /85%/.test(stats), stats.replace(/\n/g, " "));

    const id1 = await page.locator(".cert-id").innerText();
    ok(`${name}: reference has the issued shape`, /^MD-[A-Z0-9]{3}-[A-Z0-9]{4}$/.test(id1), id1);

    // The name prompt appears until it is answered, then the sheet carries it.
    ok(`${name}: it asks whose name goes on the certificate`, await page.locator(".name-prompt").count() === 1);
    ok(`${name}: the sheet says so until then`,
      (await page.locator(".cert-name").innerText()).includes("Your name here"));
    await page.fill("#learnerNameInput", "Nasif Hossain");
    await page.click("#btnSaveName");
    await page.waitForTimeout(500);
    ok(`${name}: the name lands on the sheet`,
      (await page.locator(".cert-name").innerText()).trim() === "Nasif Hossain");
    ok(`${name}: and the prompt stops asking`, await page.locator(".name-prompt").count() === 0);

    // Reload: the name persists and the reference is unchanged.
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    ok(`${name}: the name survives a reload`,
      (await page.locator(".cert-name").innerText()).trim() === "Nasif Hossain");
    ok(`${name}: the reference is stable`, (await page.locator(".cert-id").innerText()) === id1);

    if (!phone) {
      const dot = page.locator("#awardDot");
      ok("desktop: nav badge shows the count", await dot.isVisible() && (await dot.innerText()) === "1");
    }

    ok(`${name}: no horizontal overflow`,
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await shot(page, `awards-${name}`, { fullPage: true });

    // Print layout: the sheet is the only thing on the page. #app is a grid,
    // so hiding the nav without collapsing the grid left the sheet in a
    // 236px column — hence the explicit width assertion.
    await page.emulateMedia({ media: "print" });
    await page.evaluate(() => {
      document.body.classList.add("printing-cert");
      document.querySelector(".cert").classList.add("printing");
    });
    await page.waitForTimeout(250);
    ok(`${name}: print hides the actions`, !await page.locator(".cert-actions").isVisible());
    ok(`${name}: print keeps the sheet`, await page.locator(".cert-sheet").isVisible());
    ok(`${name}: print reveals a sheet never scrolled to`,
      await page.evaluate(() => getComputedStyle(document.querySelector(".cert")).opacity === "1"));
    const sheet = await page.locator(".cert-sheet").boundingBox();
    ok(`${name}: the sheet gets the whole page, not a column`,
      sheet.width > viewport.width * 0.7,
      `${Math.round(sheet.width)}px of ${viewport.width}`);
    await shot(page, `awards-print-${name}`);
    await page.emulateMedia({ media: "screen" });

    noPageErrors(errors, `${name}: no uncaught page errors`);
    await ctx.close();
  }
});
