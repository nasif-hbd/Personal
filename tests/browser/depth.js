"use strict";
/* Depth that responds to the pointer: cards lean toward it, the flashcard
 * turns over. All of it must switch off for anyone who asked their system
 * for reduced motion.
 */
const { ok, open, shot, withBrowser } = require("./harness");

withBrowser(async (browser) => {
  // --- normal motion -----------------------------------------------------
  {
    const { ctx, page, errors } = await open(browser, {
      viewport: { width: 1440, height: 960 }, theme: "dark", settle: 1600,
    });

    // Course cards lean toward the pointer.
    await page.evaluate(() => { location.hash = "#catalog"; });
    await page.waitForTimeout(1200);
    const card = page.locator(".course-card").first();
    const box = await card.boundingBox();
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
    await page.waitForTimeout(220);
    const tilted = await card.evaluate((el) => el.style.transform);
    ok("course card tilts toward the pointer", /rotate[XY]/.test(tilted), tilted.slice(0, 58));
    await page.mouse.move(5, 5);
    await page.waitForTimeout(250);
    ok("tilt resets when the pointer leaves", (await card.evaluate((el) => el.style.transform)) === "");

    // Flashcard flip.
    await page.evaluate(() => { location.hash = "#plans"; });
    await page.waitForTimeout(500);
    await page.evaluate(() => { location.hash = "#home"; });
    await page.waitForTimeout(500);
    await page.locator('[data-action="add-featured-path"]').first().click();
    await page.waitForTimeout(800);
    if (!(await page.locator('[data-action="add-card"]').count())) {
      await page.locator('[data-action="open-plan"], .plan-card').first().click();
      await page.waitForTimeout(700);
    }
    await page.locator('[data-action="add-card"]').first().click();
    await page.waitForTimeout(500);
    await page.locator("#cardFront").fill("What turns over?");
    await page.locator("#cardBack").fill("This card does.");
    await page.locator("#btnSaveCard").click();
    await page.waitForTimeout(700);
    await page.evaluate(() => { location.hash = "#review"; });
    await page.waitForTimeout(800);
    await page.locator("#btnStartReview").click();
    await page.waitForTimeout(600);

    ok("both faces exist before the flip", await page.locator(".review-face.back").count() === 1);
    ok("card starts unflipped",
      !(await page.locator("#reviewCard").evaluate((el) => el.classList.contains("flipped"))));
    await shot(page, "flip-front");
    await page.locator("#btnReveal").click();
    await page.waitForTimeout(160);
    ok("card is mid-turn right after reveal", await page.locator("#reviewCard").evaluate((el) => {
      const t = getComputedStyle(el).transform;
      return t !== "none" && t !== "matrix(1, 0, 0, 1, 0, 0)";
    }));
    await shot(page, "flip-mid");
    await page.waitForTimeout(800);
    ok("grade row appears after the turn", await page.locator(".grade-row").count() === 1);
    await shot(page, "flip-back");
    if (errors.length) console.log(`      page error: ${errors[0]}`);
    await ctx.close();
  }

  // --- reduced motion ----------------------------------------------------
  {
    const { ctx, page } = await open(browser, {
      viewport: { width: 1440, height: 960 }, theme: "dark",
      reducedMotion: "reduce", settle: 1400,
    });
    await page.evaluate(() => { location.hash = "#catalog"; });
    await page.waitForTimeout(1000);
    const card = page.locator(".course-card").first();
    const box = await card.boundingBox();
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height * 0.2);
    await page.waitForTimeout(300);
    ok("reduced motion: no tilt", (await card.evaluate((el) => el.style.transform)) === "");
    await ctx.close();
  }
});
