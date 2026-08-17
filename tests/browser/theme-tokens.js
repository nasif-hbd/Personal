"use strict";
/* --panel was referenced in eight places but never defined, so every card's
 * background declaration was invalid and silently dropped by the parser —
 * in both themes, on every screen, for weeks. A custom property that does
 * not resolve fails quietly, which is exactly why it needs a test.
 */
const { ok, open, shot, withBrowser, noPageErrors } = require("./harness");

withBrowser(async (browser) => {
  for (const theme of ["dark", "light"]) {
    const { ctx, page, errors } = await open(browser, {
      viewport: { width: 1440, height: 1000 }, settings: { theme },
    });

    const panel = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue("--panel").trim());
    ok(`${theme}: --panel resolves`, /^rgba?\(/.test(panel), panel);

    // A card's background must actually paint, not fall back to transparent.
    const bg = await page.evaluate(() => {
      const c = document.querySelector(".card");
      return c ? getComputedStyle(c).backgroundColor : "none";
    });
    ok(`${theme}: cards paint a background`, bg !== "rgba(0, 0, 0, 0)" && bg !== "none", bg);
    await shot(page, `panel-home-${theme}`);

    for (const view of ["plans", "catalog", "insights", "settings"]) {
      await page.evaluate((v) => { location.hash = "#" + v; }, view);
      await page.waitForTimeout(900);
      ok(`${theme}: ${view} renders`, await page.locator("#view").count() === 1);
    }
    await page.evaluate(() => { location.hash = "#catalog"; });
    await page.waitForTimeout(900);
    await shot(page, `panel-catalog-${theme}`);
    noPageErrors(errors, `${theme}: no uncaught page errors`);
    await ctx.close();
  }
});
