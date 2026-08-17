"use strict";
/* Nothing on the home screen should be perpetually animating. A rotating
 * border looks like a demo and costs battery on every phone that opens it.
 */
const { ok, open, shot, withBrowser, note } = require("./harness");

withBrowser(async (browser) => {
  const { ctx, page, errors } = await open(browser, {
    viewport: { width: 1440, height: 960 }, theme: "dark", settle: 1800,
  });

  const running = await page.evaluate(() =>
    document.getAnimations()
      .filter((a) => a.playState === "running")
      .map((a) => {
        const t = a.effect && a.effect.target;
        const name = a.animationName || (a.effect && a.effect.getKeyframes && "css");
        const iter = a.effect && a.effect.getTiming ? a.effect.getTiming().iterations : 1;
        return { name, iter,
          sel: t ? `${t.tagName.toLowerCase()}.${(t.className || "").toString().split(" ")[0]}` : "?" };
      })
      .filter((a) => a.iter === Infinity));
  note(`infinite animations: ${running.length ? JSON.stringify(running) : "none"}`);
  ok("no rotating border on the hero", !running.some((a) => /spin/i.test(a.name || "")));

  const edge = await page.evaluate(() => {
    const el = document.querySelector(".edge-lit");
    if (!el) return null;
    const cs = getComputedStyle(el, "::before");
    return { bg: cs.backgroundImage.slice(0, 40), anim: cs.animationName };
  });
  ok("hero edge is a still gradient",
    edge && edge.anim === "none" && /linear-gradient/.test(edge.bg),
    edge ? `${edge.anim} / ${edge.bg}` : "no .edge-lit");
  await shot(page, "home-still");
  if (errors.length) console.log(`      page error: ${errors[0]}`);
  await ctx.close();
});
