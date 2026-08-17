"use strict";
/* Ticking a lesson done opens its quiz rather than completing it. Passing is
 * what completes it; failing rolls the watch progress back so the lesson has
 * to be revisited, not just re-guessed.
 */
const { ok, open, shot, withBrowser, noPageErrors } = require("./harness");

/** Every lesson in storage that carries a quiz, first one wins. The app is
 *  inside an IIFE, so localStorage is the only honest window into what it
 *  actually saved. */
const firstQuizLesson = (page) => page.evaluate(() =>
  JSON.parse(localStorage.getItem("atlas_v1_plans"))
    .flatMap((pl) => pl.weeks.flatMap((w) => w.days.flatMap((d) => d.classes)))
    .filter((c) => c.quiz && c.quiz.length)[0]);

withBrowser(async (browser) => {
  const { ctx, page, errors } = await open(browser, {
    viewport: { width: 1440, height: 1000 }, theme: "dark", settle: 1500,
  });

  await page.locator('[data-action="add-featured-path"]').first().click();
  await page.waitForTimeout(900);
  if (!(await page.locator('[data-action="edit-quiz"]').count())) {
    await page.locator('[data-action="open-plan"], .plan-card').first().click();
    await page.waitForTimeout(700);
  }

  // Author a 4-question quiz by hand.
  await page.locator('[data-action="edit-quiz"]').first().click();
  await page.waitForTimeout(500);
  ok("quiz editor opens", await page.locator("#eqTitle").count() === 1);
  for (const [q, a] of [
    ["What is a limit?", "A value approached"],
    ["What is continuity?", "No breaks"],
    ["What is a derivative?", "Rate of change"],
    ["What is an integral?", "Accumulated area"],
  ]) {
    await page.locator("#nqQ").fill(q);
    await page.locator("#nqA").fill(a);
    await page.locator("#nqW").fill("Wrong one\nWrong two\nWrong three");
    await page.locator("#btnAddQuestion").click();
    await page.waitForTimeout(350);
  }
  ok("four questions saved", await page.locator(".quiz-item").count() === 4,
    (await page.locator(".quiz-item").count()) + " items");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(500);

  // Ticking done must open the quiz, not complete the lesson.
  await page.locator('input.chk[data-action="toggle-class-done"]').first().click();
  await page.waitForTimeout(700);
  ok("ticking done opens the quiz", await page.locator("#quizTitle").count() === 1);
  ok("lesson NOT completed by ticking", (await firstQuizLesson(page)).done === false);
  await shot(page, "quiz-open");

  const pickAnswers = async (correctCount) => {
    const data = (await firstQuizLesson(page)).quiz;
    for (let i = 0; i < data.length; i++) {
      const right = data[i].answer;
      const pick = i < correctCount ? right : (right === 0 ? 1 : 0);
      await page.locator(`input[name="q${i}"][value="${pick}"]`).check();
      await page.waitForTimeout(120);
    }
  };

  // Answer 2/4 -> 50% -> must fail.
  await pickAnswers(2);
  ok("submit enabled once all answered", await page.locator("#btnSubmitQuiz").isEnabled());
  await page.locator("#btnSubmitQuiz").click();
  await page.waitForTimeout(900);
  const failText = await page.locator(".quiz-result").innerText();
  ok("50% fails", /Not quite/i.test(failText), failText.split("\n")[1]);
  await shot(page, "quiz-fail");

  const afterFail = await firstQuizLesson(page);
  ok("failing does not complete", afterFail.done === false);
  ok("failing resets watch progress", afterFail.watchPct === 0, "watchPct=" + afterFail.watchPct);
  ok("attempt recorded", afterFail.quizAttempts.length === 1);

  // Retake with 3/4 -> 75% -> pass.
  await page.locator('[data-action="retake-quiz"]').click();
  await page.waitForTimeout(700);
  await pickAnswers(3);
  await page.locator("#btnSubmitQuiz").click();
  await page.waitForTimeout(900);
  ok("75% passes", /Passed/i.test(await page.locator(".quiz-result").innerText()));
  await shot(page, "quiz-pass");
  const afterPass = await firstQuizLesson(page);
  ok("passing completes the lesson", afterPass.done === true);
  ok("two attempts recorded", afterPass.quizAttempts.length === 2);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(600);
  ok("card shows the passing score", /75%/.test(await page.locator(".quiz-btn").first().innerText()));

  noPageErrors(errors);
  await ctx.close();
});
