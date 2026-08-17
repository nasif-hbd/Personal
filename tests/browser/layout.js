"use strict";
/* The navigation changes shape with the screen: a sticky left rail from
 * 901px up, a horizontal bar below that, a tab bar on phones. Nothing may
 * push the page sideways at any width.
 */
const { ok, open, shot, withBrowser } = require("./harness");

withBrowser(async (browser) => {
  for (const [name, viewport, phone] of [
    ["desktop", { width: 1440, height: 900 }, false],
    ["laptop", { width: 1024, height: 768 }, false],
    ["tablet", { width: 820, height: 1000 }, false],
    ["phone", { width: 390, height: 844 }, true],
  ]) {
    const { ctx, page, errors } = await open(browser, {
      viewport, phone, theme: "dark", settle: 1500,
    });
    await page.locator('[data-action="add-featured-path"]').first().click();
    await page.waitForTimeout(700);

    const nav = await page.locator(".topnav").boundingBox();
    const view = await page.locator("#view").boundingBox();
    const vertical = nav.height > 400;
    const wide = viewport.width >= 901;

    ok(`${name}: nav is ${wide ? "a left rail" : "horizontal"}`, vertical === wide,
      `nav ${Math.round(nav.width)}x${Math.round(nav.height)}`);
    if (wide) {
      ok(`${name}: content sits beside rail`, view.x >= nav.width - 2,
        `view.x=${Math.round(view.x)} rail=${Math.round(nav.width)}`);
      ok(`${name}: nav icons visible`, await page.locator(".nav-ic svg").first().isVisible());
      // Sticky: scroll down, the rail must stay on screen.
      await page.evaluate(() => window.scrollTo(0, 1200));
      await page.waitForTimeout(400);
      const after = await page.locator(".topnav").boundingBox();
      ok(`${name}: rail stays stuck`, after.y <= 1, `y=${Math.round(after.y)}`);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(300);
    } else {
      ok(`${name}: tab bar for phones`,
        (await page.locator("#tabbar").isVisible()) === (viewport.width <= 720));
    }

    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    ok(`${name}: no horizontal overflow`, overflow === 0, overflow + "px");
    await shot(page, `rail-${name}`);
    if (errors.length) console.log(`      ${name} page error: ${errors[0]}`);
    await ctx.close();
  }
});
