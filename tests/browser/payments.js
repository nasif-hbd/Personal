"use strict";
/* Checkout: a configured rail becomes a one-tap link, an unconfigured one
 * falls back to instructions, and nothing offers a button that would do
 * nothing on the device you are holding.
 *
 * The runner starts the API with fixture payment details; without them the
 * rails are hidden by design, so this suite needs that server.
 */
const { API, ok, open, shot, withBrowser, noPageErrors, requireApi } = require("./harness");

async function checkout(page, method) {
  await page.evaluate(() => { location.hash = "#upgrade"; });
  await page.waitForTimeout(900);
  await page.locator('[data-action="pick-plan"]').first().click();
  await page.waitForTimeout(400);
  await page.locator(`[data-action="pick-method"][data-method="${method}"]`).click();
  await page.waitForTimeout(400);
}

withBrowser(async (browser) => {
  await requireApi();

  const settings = { theme: "dark", model: "claude-sonnet-5", effort: "medium",
    backendUrl: API, apiKey: "", accessCode: "", driveConnected: false, ytApiKey: "" };

  for (const [name, viewport, phone] of [
    ["desktop", { width: 1440, height: 960 }, false],
    ["phone", { width: 390, height: 844 }, true],
  ]) {
    const { ctx, page, errors } = await open(browser, {
      viewport, phone, theme: "dark", settings, settle: 2200,
    });

    // bKash has a configured checkout link -> one-tap on every device.
    await checkout(page, "bkash");
    const link = page.locator(".pay-now");
    ok(`${name}: bKash offers a direct checkout`, await link.count() === 1);
    ok(`${name}: it opens the real link`,
      (await link.getAttribute("href")) === "https://shop.bkash.com/mindora/pay",
      await link.getAttribute("href"));
    ok(`${name}: opens in a new tab`, (await link.getAttribute("target")) === "_blank");
    if (name === "desktop") await shot(page, "pay-direct");

    // Google Pay -> UPI deep link carrying the amount.
    await checkout(page, "gpay");
    const upi = await page.locator(".pay-now").getAttribute("href");
    ok(`${name}: Google Pay uses a UPI deep link`, upi.startsWith("upi://pay?"), upi.slice(0, 52));
    ok(`${name}: the amount is prefilled`, /[?&]am=499/.test(upi));

    // Nagad has no link -> dialler on phones, nothing on desktop.
    await checkout(page, "nagad");
    const nagad = await page.locator(".pay-now").count();
    if (phone) {
      ok("phone: Nagad offers the dialler", nagad === 1,
        await page.locator(".pay-now").getAttribute("href"));
      await shot(page, "pay-ussd");
    } else {
      ok("desktop: no dialler link where it would do nothing", nagad === 0);
    }
    ok(`${name}: manual steps remain as fallback`, await page.locator(".pay-steps").count() === 1);
    noPageErrors(errors, `${name}: no uncaught page errors`);
    await ctx.close();
  }
});
