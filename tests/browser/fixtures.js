"use strict";
/* Plan and lesson shapes, matching what the app writes to localStorage.
 *
 * The numbers here are load-bearing: the awards and XP suites assert exact
 * totals derived from these fixtures, so changing a duration or a score
 * changes an expected value. Each builder documents what it is worth.
 */

/** One lesson. 45 minutes, one quiz question, and a note on the first few
 *  finished ones — enough shape for the certificate stats to be non-trivial. */
function lesson(i, done, score) {
  return {
    id: "c" + i,
    title: "Lesson " + i,
    type: "reading",
    durationMinutes: 45,
    done,
    doneAt: done ? Date.UTC(2026, 6, 10 + i) : 0,
    userNotes: done && i < 3 ? [{ id: "n" + i, text: "note", at: Date.now() }] : [],
    quiz: [{ q: "?", options: ["a", "b"], answer: 0 }],
    quizAttempts: done ? [{ score, at: Date.now() }] : [],
  };
}

/** A one-week plan with `total` lessons, `doneCount` of them finished,
 *  alternating quiz scores of 0.8 and 0.9, plus a rest day that must never
 *  be counted as work. */
function plan(id, title, doneCount, total) {
  return {
    id,
    title,
    subject: "Mathematics",
    durationWeeks: 1,
    dailyMinutes: 60,
    daysOff: [],
    startDate: "2026-07-01",
    createdAt: Date.now(),
    cards: [{ id: "k1", due: 0 }, { id: "k2", due: 0 }, { id: "k3", due: 0 }],
    weeks: [{ weekNumber: 1, days: [{ dayNumber: 1, classes:
      Array.from({ length: total }, (_, i) => lesson(i, i < doneCount, i % 2 ? 0.9 : 0.8))
        .concat([{ id: "rest", type: "rest", title: "Rest" }]),
    }] }],
  };
}

/** A lesson with an explicit duration, score and note count, for the XP
 *  arithmetic — which scores each of those separately. */
function scoredLesson(id, mins, done, score, notes) {
  return {
    id,
    title: "Lesson " + id,
    type: "reading",
    durationMinutes: mins,
    done,
    doneAt: done ? Date.UTC(2026, 7, 10) : 0,
    userNotes: Array.from({ length: notes }, (_, i) => ({ id: id + i, text: "n", at: 1 })),
    quiz: [{ q: "?", options: ["a", "b"], answer: 0 }],
    quizAttempts: done ? [{ score, at: 1 }] : [],
  };
}

/** Four 60-minute lessons, `doneCount` finished, worth exactly:
 *
 *    lessons  3*10 + floor(180/10)*1  = 48   (at doneCount 3)
 *    quizzes  16 + 18 + 20            = 54
 *    notes    (2+1+0)*2               = 6
 *    reviews  4 reps * 3              = 12
 *    plans    none finished           = 0
 *
 *  Streak XP moves with the wall clock, so it is deliberately not part of
 *  any fixed expectation.
 */
function xpPlan(doneCount) {
  return [{
    id: "p1",
    title: "Calculus",
    subject: "Mathematics",
    durationWeeks: 1,
    startDate: "2026-08-01",
    cards: [{ id: "k1", reps: 3, due: 0 }, { id: "k2", reps: 1, due: 0 }],
    weeks: [{ weekNumber: 1, days: [{ dayNumber: 1, classes: [
      scoredLesson("a", 60, doneCount > 0, 0.8, 2),
      scoredLesson("b", 60, doneCount > 1, 0.9, 1),
      scoredLesson("c", 60, doneCount > 2, 1.0, 0),
      scoredLesson("d", 60, doneCount > 3, 0.75, 0),
      { id: "r", type: "rest", title: "Rest" },
    ] }] }],
  }];
}

/** One video lesson with no url and no ytId — the only way to play it is to
 *  go and find it, which is the whole point of the YouTube suite. */
function linklessVideoPlan() {
  return [{
    id: "p1",
    title: "Calculus I",
    subject: "Mathematics",
    durationWeeks: 1,
    startDate: "2026-08-01",
    cards: [],
    weeks: [{ weekNumber: 1, days: [{ dayNumber: 1, classes: [
      { id: "l1", title: "Essence of calculus", channel: "3Blue1Brown", type: "video",
        durationMinutes: 60, done: false, userNotes: [], quiz: [], quizAttempts: [] },
    ] }] }],
  }];
}

module.exports = { lesson, plan, scoredLesson, xpPlan, linklessVideoPlan };
