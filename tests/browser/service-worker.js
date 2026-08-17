"use strict";
/* A returning visitor, with the service worker installed and controlling.
 *
 * This is the shape of the bug that hides best: the code is right, the
 * deploy succeeded, and the browser is still serving last week's HTML. The
 * worker is network-first for navigations precisely so that cannot happen,
 * and this suite is what holds it to that.
 */
const { ok, open, shot, withBrowser, note } = require("./harness");

withBrowser(async (browser) => {
  for (const [name, viewport, phone] of [
    ["desktop", { width: 1440, height: 900 }, false],
    ["phone", { width: 390, height: 844 }, true],
  ]) {
    const { ctx, page, errors } = await open(browser, {
      viewport, phone, serviceWorkers: "allow", waitUntil: "load", settle: 2500,
    });
    const first = await page.evaluate(() =>
      navigator.serviceWorker.controller ? "controlling" : "none");

    // Second visit — this is where a stale shell would show up.
    await page.reload({ waitUntil: "load" });
    await page.waitForTimeout(2500);
    const second = await page.evaluate(async () => {
      const r = await navigator.serviceWorker.getRegistration();
      return r ? (navigator.serviceWorker.controller ? "controlling" : "registered") : "none";
    });
    note(`${name} service worker: ${first}, then ${second}`);

    ok(`${name}: the worker takes control by the second visit`, second !== "none", second);
    ok(`${name}: the dock element exists`, await page.locator("#fbDock").count() === 1);
    ok(`${name}: the button is visible`, await page.locator("#fbFab").isVisible().catch(() => false));

    // What HTML did the worker actually hand over? A cached shell from
    // before the dock shipped would answer this with hasDock false.
    const served = await page.evaluate(async () => {
      const r = await fetch("./index.html", { cache: "no-store" });
      const t = await r.text();
      return { bytes: t.length, hasDock: t.includes("fb-dock") };
    });
    ok(`${name}: the served HTML is current, not a cached shell`,
      served.hasDock, JSON.stringify(served));

    await shot(page, `sw-${name}`);
    if (errors.length) console.log(`      ${name} page error: ${errors[0]}`);
    await ctx.close();
  }
});
