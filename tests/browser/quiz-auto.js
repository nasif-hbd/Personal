"use strict";
/* A video lesson with no quiz gets one written for it when you mark it done,
 * and the lesson stays incomplete until that quiz is passed.
 *
 * The backend is stubbed, streaming the quiz back as SSE the way the real
 * one does — with a delay, so the waiting state is genuinely exercised
 * rather than skipped by an instant stub.
 */
const { ok, shot, withBrowser, WEB, noPageErrors } = require("./harness");

const QUIZ = JSON.stringify([
  { q: "What did the lesson say about ratios?",
    options: ["A comparison of two quantities", "A kind of triangle", "A prime number", "A unit of time"], answer: 0 },
  { q: "Which operation keeps a ratio equivalent?",
    options: ["Adding 1 to one side", "Multiplying both sides", "Squaring one side", "Reversing the order"], answer: 1 },
  { q: "What is a rate?",
    options: ["A ratio with different units", "A negative number", "A type of graph", "An angle"], answer: 0 },
  { q: "How do you simplify a ratio?",
    options: ["Divide by the GCD", "Add the terms", "Multiply by 10", "Round both terms"], answer: 0 },
]);

const lessonNamed = (page, title) => page.evaluate((t) =>
  JSON.parse(localStorage.getItem("atlas_v1_plans"))
    .flatMap((pl) => pl.weeks.flatMap((w) => w.days.flatMap((d) => d.classes)))
    .filter((c) => c.title === t)[0], title);

withBrowser(async (browser) => {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: "dark" });
  await ctx.route(/fonts\.g|youtube|ytimg/, (r) => r.abort());

  await ctx.route("**/api/chat", async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    route.fulfill({
      status: 200, contentType: "text/event-stream",
      body: `data: ${JSON.stringify({ type: "content_block_delta", delta: { text: QUIZ } })}\n\n`,
    });
  });
  await ctx.route("**/api/billing/**", (r) => r.fulfill({ status: 200,
    contentType: "application/json", body: JSON.stringify({
      freeForAll: true, plans: [], methods: [], codeEnabled: true, status: "none", active: false }) }));
  await ctx.route("**/api/health", (r) => r.fulfill({ status: 200,
    contentType: "application/json", body: JSON.stringify({
      ok: true, chat: true, storage: "drive", quota: {},
      billing: { freeForAll: true, enabled: false } }) }));
  await ctx.route("**/api/state", (r) => r.fulfill({ status: 200,
    contentType: "application/json", body: '{"state":{}}' }));

  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript(() => {
    localStorage.setItem("atlas_v1_settings", JSON.stringify({
      theme: "dark", model: "claude-sonnet-5", effort: "medium",
      backendUrl: "https://stub.invalid", apiKey: "", accessCode: "",
      driveConnected: false, ytApiKey: "",
    }));
  });
  await page.goto(`${WEB}/index.html`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2200);

  await page.locator('[data-action="add-featured-path"][data-path="ai"]').click();
  await page.waitForTimeout(900);
  if (!(await page.locator('[data-action="edit-quiz"]').count())) {
    await page.locator('[data-action="open-plan"], .plan-card').first().click();
    await page.waitForTimeout(700);
  }

  const before = await lessonNamed(page, "AI For Everyone");
  ok("found the lesson with no quiz of its own",
    !!before && (!before.quiz || !before.quiz.length),
    before && `${before.title} (type=${before.type})`);

  // Completing a video lesson routes through the same gate the player uses.
  await page.locator(`input.chk[data-action="toggle-class-done"][data-class="${before.id}"]`).click();
  await page.waitForTimeout(600);
  ok("waiting state shows while it writes", await page.locator(".quiz-preparing").count() === 1);
  await shot(page, "quiz-preparing");

  await page.waitForSelector("#quizTitle", { timeout: 8000 });
  await page.waitForTimeout(400);
  const title = await page.locator("#quizTitle").innerText();
  ok("quiz auto-starts after the video", /Quick check/i.test(title), title);
  ok("four questions generated", await page.locator(".quiz-q").count() === 4);
  ok("questions are about the lesson", /ratio/i.test(await page.locator(".quiz-body").innerText()));
  await shot(page, "quiz-auto");

  const saved = await lessonNamed(page, "AI For Everyone");
  ok("quiz saved to the lesson", saved.quiz && saved.quiz.length === 4);
  ok("lesson not completed by the video alone", saved.done === false);

  noPageErrors(errors);
  await ctx.close();
});
