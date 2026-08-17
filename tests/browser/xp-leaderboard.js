"use strict";
/* XP is derived from recorded work, so it must move only when the work does.
 * The leaderboard is opt-in and must publish nothing until you say so.
 */
const { API, ok, open, shot, withBrowser, noPageErrors, requireApi } = require("./harness");
const { xpPlan } = require("./fixtures");

const boot = (browser, settings, plans, viewport) =>
  open(browser, { settings, plans, viewport, hash: "#awards", settle: 2000 });

const xpOf = async (page) =>
  Number((await page.locator(".level-meta .desc").innerText()).replace(/[^0-9].*$/, ""));

/** Open the board tab and wait for it to actually finish loading. */
async function openBoard(page) {
  await page.locator('[data-action="awards-tab"][data-tab="board"]').click();
  await page.locator("#boardNameInput, .board-me, .chat-empty").first().waitFor({ timeout: 15000 });
  await page.waitForTimeout(250);
}

withBrowser(async (browser) => {
  await requireApi();

  // --- XP is derived from the work --------------------------------------
  {
    const { ctx, page, errors } = await boot(browser, { theme: "dark" }, xpPlan(3));
    ok("the level card shows", await page.locator(".level-card").count() === 1);

    // The breakdown lives in a <details>; open it before reading.
    await page.locator(".xp-detail summary").click();
    await page.waitForTimeout(300);
    const parts = {};
    for (const li of await page.locator(".xp-list li").all()) {
      const t = await li.innerText();
      const [label] = t.split("\n");
      parts[label.trim()] = Number(((t.match(/\+([\d,]+)/) || ["", "0"])[1]).replace(/,/g, ""));
    }
    ok("lessons score base plus duration", parts["Lessons finished"] === 48, JSON.stringify(parts));
    ok("quizzes score the best attempt", parts["Quiz scores"] === 54, String(parts["Quiz scores"]));
    ok("notes are capped per lesson", parts["Notes written"] === 6, String(parts["Notes written"]));
    ok("reviews score every repetition", parts["Cards reviewed"] === 12, String(parts["Cards reviewed"]));
    ok("an unfinished plan pays no bonus", !("Plans completed" in parts), JSON.stringify(parts));

    const shown = await xpOf(page);
    const sum = Object.values(parts).reduce((a, c) => a + c, 0);
    ok("the total is the sum of its parts", shown === sum, `${shown} vs ${sum}`);
    ok("level follows the curve", /Level \d+/.test(await page.locator("#topSub").innerText()));
    await shot(page, "xp-level");
    noPageErrors(errors);
    await ctx.close();
  }

  // Finishing the plan must add the bonus.
  {
    const { ctx, page } = await boot(browser, { theme: "dark" }, xpPlan(4));
    await page.locator(".xp-detail summary").click();
    await page.waitForTimeout(300);
    const t = await page.locator(".xp-list").innerText();
    ok("a finished plan pays its bonus", /Plans completed[\s\S]*?\+150/.test(t), t.replace(/\n/g, " | "));
    await ctx.close();
  }

  // Doing less must score less — the point of deriving rather than accruing.
  {
    const a = await boot(browser, { theme: "dark" }, xpPlan(1));
    const one = await xpOf(a.page);
    await a.ctx.close();
    const c = await boot(browser, { theme: "dark" }, xpPlan(3));
    const three = await xpOf(c.page);
    await c.ctx.close();
    ok("untick work and the XP goes with it", three > one, `${three} > ${one}`);
  }

  // --- Leaderboard -------------------------------------------------------
  // Without a server: XP still works, and the board explains itself.
  {
    const { ctx, page } = await boot(browser, { theme: "dark" }, xpPlan(3));
    await openBoard(page);
    ok("offline: the board says it needs a server",
      /needs a server/i.test(await page.locator("#awardsPanel").innerText()));
    ok("offline: nothing is published", await page.locator("#boardNameInput").count() === 0);
    await ctx.close();
  }

  // With a server: opt in, appear, then leave.
  {
    const { ctx, page, errors } = await boot(browser, { theme: "dark", backendUrl: API }, xpPlan(3));
    await openBoard(page);
    ok("joining is opt-in, not automatic", await page.locator("#boardNameInput").count() === 1);
    ok("and nothing is listed until you do",
      !/your place/i.test(await page.locator("#awardsPanel").innerText()));

    await page.fill("#boardNameInput", "Nasif");
    await page.click("#btnJoinBoard");
    await page.locator(".board-me").waitFor({ timeout: 15000 });
    const panel = await page.locator("#awardsPanel").innerText();
    ok("joining reports your rank out of the field",
      /your place[\s\S]*#\d+ of \d+/i.test(panel), panel.split("\n").slice(0, 2).join(" "));
    ok("your row is on the board", await page.locator(".board-row").count() >= 1);
    ok("the board shows the name you chose",
      await page.locator(".board-row.mine .board-name").count() === 1
      && (await page.locator(".board-row.mine .board-name").innerText()).trim() === "Nasif",
      await page.locator(".board-row.mine .board-name").count() ? "" : "no row marked as mine");
    ok("and admits the scores are self-reported",
      /can't prove them|self-reported|reported by each/i.test(panel));
    await shot(page, "xp-board");

    // A reload must find you still listed.
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
    await openBoard(page);
    ok("you stay listed across a reload",
      /your place/i.test(await page.locator("#awardsPanel").innerText()));

    await page.locator('[data-action="leave-board"]').click();
    await page.locator("#boardNameInput").waitFor({ timeout: 15000 });
    ok("leaving takes you off again", await page.locator("#boardNameInput").count() === 1);
    noPageErrors(errors);
    await ctx.close();
  }

  // --- Phone -------------------------------------------------------------
  {
    const { ctx, page } = await boot(browser, { theme: "light" }, xpPlan(4), { width: 390, height: 844 });
    ok("phone: level card fits", await page.locator(".level-card").isVisible());
    ok("phone: no horizontal overflow",
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await openBoard(page);
    ok("phone: board tab still fits",
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await shot(page, "xp-phone", { fullPage: true });
    await ctx.close();
  }
});
