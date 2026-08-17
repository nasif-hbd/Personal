"use strict";
/* The panel must be reachable from the navigation, not only from a floating
 * button someone has to notice. This suite exists because it once wasn't:
 * on phones the label was hidden down to a 44px circle and nothing pointed
 * at it, so the feature was live and effectively invisible.
 */
const { ok, open, shot, withBrowser, noPageErrors } = require("./harness");

withBrowser(async (browser) => {
  // Desktop: an entry in the left rail.
  {
    const { ctx, page, errors } = await open(browser, {
      viewport: { width: 1440, height: 1000 }, waitUntil: "load", settle: 2200,
    });

    const rail = page.locator('.navrow [data-action="open-feedback"]');
    ok("desktop: Feedback is in the rail", await rail.count() === 1);
    ok("desktop: it reads as a nav item",
      (await rail.innerText()).trim() === "Feedback", await rail.innerText());
    ok("desktop: its icon rendered", await rail.locator("svg").count() === 1);
    ok("desktop: the panel starts closed", !await page.locator("#fbPanel").isVisible());

    await rail.click();
    await page.waitForTimeout(500);
    ok("desktop: clicking it opens the panel", await page.locator("#fbPanel").isVisible());
    ok("desktop: and focuses the box for typing",
      await page.evaluate(() => document.activeElement && document.activeElement.id) === "fbMessage");

    // It must not behave like a destination — the view should not change.
    ok("desktop: it does not navigate away",
      (await page.locator("#topTitle").innerText()).trim().toLowerCase() === "home",
      await page.locator("#topTitle").innerText());
    await shot(page, "fbnav-desktop");
    noPageErrors(errors, "desktop: no uncaught page errors");
    await ctx.close();
  }

  // Phone: in the More sheet, and the corner button says what it is again.
  {
    const { ctx, page, errors } = await open(browser, {
      viewport: { width: 390, height: 844 }, phone: true,
      waitUntil: "load", settle: 2200,
    });

    const fab = page.locator("#fbFab");
    ok("phone: the corner button is labelled again",
      (await fab.innerText()).trim() === "Feedback", await fab.innerText());
    const box = await fab.boundingBox();
    ok("phone: it is a labelled pill, not a bare circle", box.width > 90,
      `${Math.round(box.width)}x${Math.round(box.height)}`);
    const bar = await page.locator("#tabbar").boundingBox();
    ok("phone: it still clears the tab bar", box.y + box.height < bar.y + 2,
      `ends ${Math.round(box.y + box.height)}, bar at ${Math.round(bar.y)}`);
    ok("phone: no horizontal overflow",
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));

    await page.locator("#btnMore").tap();
    await page.waitForTimeout(500);
    const item = page.locator('.sheet-item[data-action="open-feedback"]');
    ok("phone: Feedback is in the More sheet", await item.count() === 1);
    await item.tap();
    await page.waitForTimeout(700);
    ok("phone: it opens the panel", await page.locator("#fbPanel").isVisible());
    ok("phone: and the sheet gets out of the way",
      !await page.locator("#moreSheet").isVisible());
    await shot(page, "fbnav-phone");
    noPageErrors(errors, "phone: no uncaught page errors");
    await ctx.close();
  }
});
