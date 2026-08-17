"use strict";
/* The feedback dock: reachable everywhere, surviving navigation, and never
 * silently dropping what someone typed.
 */
const { API, ok, open, shot, withBrowser, noPageErrors, requireApi } = require("./harness");

const boot = (browser, settings, viewport) =>
  open(browser, {
    settings, viewport,
    phone: !!viewport && viewport.width < 500,
    settle: 1800,
  });

withBrowser(async (browser) => {
  await requireApi();

  // --- with a server -----------------------------------------------------
  {
    const { ctx, page, errors } = await boot(browser, { theme: "dark", backendUrl: API });
    ok("the button is there without opening anything", await page.locator("#fbFab").isVisible());
    ok("and the panel starts closed", !await page.locator("#fbPanel").isVisible());

    const box = await page.locator("#fbFab").boundingBox();
    const vw = page.viewportSize();
    ok("it sits bottom-right",
      box.x + box.width > vw.width - 90 && box.y + box.height > vw.height - 90,
      `x2=${Math.round(box.x + box.width)}/${vw.width} y2=${Math.round(box.y + box.height)}/${vw.height}`);

    // It must not be swallowed by the dock's own bounding box.
    ok("the dock does not blanket the page",
      await page.evaluate(() => {
        const el = document.elementFromPoint(60, window.innerHeight - 40);
        return !el || !el.closest(".fb-dock");
      }));

    await page.locator("#fbFab").click();
    await page.waitForTimeout(400);
    ok("clicking opens the panel", await page.locator("#fbPanel").isVisible());
    ok("and marks itself expanded",
      await page.locator("#fbFab").getAttribute("aria-expanded") === "true");

    // Too-short messages are refused in the browser, before a round trip.
    await page.fill("#fbMessage", "hm");
    await page.click("#fbSend");
    await page.waitForTimeout(400);
    ok("a two-character message is refused locally",
      /more detail/i.test(await page.locator("#fbNote").innerText()));
    ok("and the panel stays open so nothing is lost",
      await page.locator("#fbPanel").isVisible());

    // A real one goes through, with the kind and context attached.
    let posted = null;
    await page.route("**/api/feedback", async (route) => {
      posted = JSON.parse(route.request().postData());
      await route.continue();
    });
    await page.locator('.fb-kind[data-kind="bug"]').click();
    await page.fill("#fbMessage", "The catalog filter forgets my subject.");
    await page.fill("#fbContact", "someone@example.com");
    await page.click("#fbSend");
    await page.waitForTimeout(1500);

    ok("the message is sent", posted && posted.message === "The catalog filter forgets my subject.",
      posted ? posted.message : "nothing posted");
    ok("the chosen kind rides along", posted && posted.kind === "bug", posted && posted.kind);
    ok("a reply address is included", posted && posted.contact === "someone@example.com");
    ok("context the owner would have to ask for is attached",
      posted && posted.meta && posted.meta.view && posted.meta.screen,
      JSON.stringify(posted && posted.meta));
    ok("the honeypot is empty for a real person", posted && posted.website === "");
    ok("the panel closes on success", !await page.locator("#fbPanel").isVisible());
    ok("and it says so", /reached us/i.test(await page.locator("#toasts").innerText()));

    // The box must be empty next time, not still holding the last message.
    await page.locator("#fbFab").click();
    await page.waitForTimeout(400);
    ok("the box is clear for the next message",
      (await page.locator("#fbMessage").inputValue()) === "");
    await shot(page, "fb-open");

    // Navigating re-renders the whole view; the dock must survive it.
    await page.keyboard.press("Escape");
    await page.evaluate(() => { location.hash = "#catalog"; });
    await page.waitForTimeout(900);
    ok("the dock survives navigation", await page.locator("#fbFab").isVisible());
    await page.evaluate(() => { location.hash = "#awards"; });
    await page.waitForTimeout(900);
    ok("and is on every screen", await page.locator("#fbFab").isVisible());

    // Half-typed text must not vanish when the view behind it re-renders.
    await page.locator("#fbFab").click();
    await page.waitForTimeout(400);
    await page.fill("#fbMessage", "half-written thought");
    await page.evaluate(() => { location.hash = "#plans"; });
    await page.waitForTimeout(900);
    ok("a half-typed message survives a re-render",
      (await page.locator("#fbMessage").inputValue()) === "half-written thought");

    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    ok("escape closes the panel", !await page.locator("#fbPanel").isVisible());
    noPageErrors(errors);
    await ctx.close();
  }

  // --- rate limit is enforced server-side --------------------------------
  // The runner starts the API with FEEDBACK_DAILY_CAP=5, so the sixth try
  // is the one that must be refused.
  {
    const { ctx, page } = await boot(browser, { theme: "dark", backendUrl: API });
    let lastNote = "";
    for (let i = 0; i < 7; i++) {
      await page.locator("#fbFab").click();
      await page.waitForTimeout(300);
      await page.fill("#fbMessage", `Flooding attempt number ${i} with enough text.`);
      await page.click("#fbSend");
      await page.waitForTimeout(900);
      const note = await page.locator("#fbNote").innerText().catch(() => "");
      if (note) lastNote = note;
      if (await page.locator("#fbPanel").isVisible()) await page.keyboard.press("Escape");
      await page.waitForTimeout(250);
    }
    ok("the server stops one person flooding the inbox",
      /already today|tomorrow/i.test(lastNote), lastNote || "(no message shown)");
    await ctx.close();
  }

  // --- without a server: mailto fallback ---------------------------------
  {
    const { ctx, page } = await boot(browser, { theme: "dark", backendUrl: "" });
    ok("offline: the button is still offered", await page.locator("#fbFab").isVisible());
    await page.locator("#fbFab").click();
    await page.waitForTimeout(400);
    await page.fill("#fbMessage", "No server here, but this should still reach someone.");

    let target = "";
    await page.route("mailto:**", (r) => { target = r.request().url(); r.abort(); });
    page.on("request", (r) => { if (r.url().startsWith("mailto:")) target = r.url(); });
    await page.click("#fbSend");
    await page.waitForTimeout(1200);
    // The address itself is not asserted here: it lives in one place in the
    // app, and copying it into a test only makes a second thing to update.
    ok("offline: it falls back to a mailbox",
      /^mailto:[^@\s]+@[^?\s]+/.test(target), target.slice(0, 60) || "(no mailto)");
    ok("offline: the message is carried in the body",
      /No%20server%20here/.test(target), target.slice(0, 120));
    await ctx.close();
  }

  // --- phone -------------------------------------------------------------
  {
    const { ctx, page } = await boot(browser, { theme: "light", backendUrl: API },
      { width: 390, height: 844 });
    const fab = await page.locator("#fbFab").boundingBox();
    const bar = await page.locator("#tabbar").boundingBox();
    ok("phone: the button clears the tab bar", fab.y + fab.height < bar.y + 2,
      `fab ends ${Math.round(fab.y + fab.height)}, bar starts ${Math.round(bar.y)}`);
    // Labelled, because an unlabelled circle above the tab bar is not
    // something anyone finds — but still a corner button, not a bar.
    ok("phone: it is a labelled pill, not a strip",
      fab.width >= 90 && fab.width < 200, `${Math.round(fab.width)}px`);
    ok("phone: it says what it is", /feedback/i.test(await page.locator("#fbFab").innerText()));
    await page.locator("#fbFab").click();
    await page.waitForTimeout(500);
    ok("phone: the panel fits the screen",
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    ok("phone: the send button is reachable", await page.locator("#fbSend").isVisible());
    await shot(page, "fb-phone");
    await ctx.close();
  }
});
