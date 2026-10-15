import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const worker = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
const source = worker.slice(worker.indexOf("async function executeRun("), worker.indexOf("async function poll("));

function setup({ verdict, pending = false, selectionFails = false, checkinDeferred = false,
  staleMoodMarker = false } = {}) {
  const calls = [];
  let result;
  let checkinReads = 0;
  const context = vm.createContext({
    ONEPOINT_QUIZ_RUN_KEY: "quiz", setTimeout: fn => fn(),
    chrome: { tabs: { create: async () => ({ id: 7 }), get: async () => ({ id: 7 }), update: async () => {} },
      storage: { session: { set: async () => {}, remove: async () => {} } } },
    event: async () => {}, waitForTab: async () => {}, navigate: async () => {},
    trustedActivateButton: async (_tab, label) => { calls.push(`activate:${label}`); },
    trustedInsertText: async () => {}, locateCheckinTarget: function locateCheckinTarget() {},
    waitForCheckinState: async fn => fn(),
    inspectCheckin: function inspectCheckin() {}, inspectQuizOutcome: function inspectQuizOutcome() {},
    extractQuiz: function extractQuiz() {},
    execute: async (_id, fn) => {
      if (fn.name === "inspectCheckin") {
        checkinReads += 1;
        if (staleMoodMarker && checkinReads <= 2) return {
          completed: false, moodSelected: false,
          noteValue: checkinReads === 1 ? "" : "今日签到",
          submitDisabled: checkinReads === 1, lastSign: "2026-09-09 10:01", alerts: []
        };
        return checkinDeferred
          ? { completed: false, moodSelected: true, noteValue: "今日签到", submitDisabled: false,
            lastSign: "2026-09-06 11:09", alerts: [] }
          : { completed: true };
      }
      if (fn.name === "inspectQuizOutcome") return { completed: Boolean(verdict), verdict,
        evidence: verdict ? "explicit website outcome" : "", pending_label: pending ? "正在验证" : null };
      calls.push("extract-unanswered");
      return { question: "Q", choices: ["A", "B"] };
    },
    collectGoogleEvidence: async () => { calls.push("search"); return []; },
    solveQuizStep: async (_tab, _run, quiz) => {
      calls.push("review-and-submit"); assert.equal(quiz.checkinVerified, !checkinDeferred);
      if (selectionFails) throw new Error("selection failed");
      return { verdict: "correct", evidence: "graded after new review" };
    },
    request: async (url, args) => { assert.equal(url, "/complete"); result = JSON.parse(args.body); },
  });
  vm.runInContext(source, context);
  return { calls, run: async () => { await context.executeRun({ run_id: "fresh" }); return result; } };
}

for (const verdict of ["correct", "incorrect", "already_completed_unknown"]) {
  test(`explicit ${verdict} plus completed check-in skips model and submission`, async () => {
    const app = setup({ verdict });
    const result = await app.run();
    assert.equal(result.checkin_verified, true);
    assert.equal(result.quiz_outcome.verdict, verdict);
    assert.deepEqual(app.calls, []);
  });
}
test("a pending old website result is observed, never answered again", async () => {
  const app = setup({ pending: true });
  const result = await app.run();
  assert.equal(result.ok, false);
  assert.equal(result.checkin_verified, true);
  assert.deepEqual(app.calls, []);
});
test("explicit unanswered form continues with a fresh review and answer", async () => {
  const app = setup();
  assert.equal((await app.run()).quiz_outcome.verdict, "correct");
  assert.deepEqual(app.calls, ["extract-unanswered", "search", "review-and-submit"]);
});
test("selection failure retains check-in completion but no quiz grade", async () => {
  const result = await setup({ selectionFails: true }).run();
  assert.equal(result.ok, false);
  assert.equal(result.checkin_verified, true);
  assert.equal(result.quiz_outcome, undefined);
});
test("a check-in that is not yet accepted does not prevent today's quiz", async () => {
  const app = setup({ checkinDeferred: true });
  const result = await app.run();
  assert.equal(result.ok, false);
  assert.equal(result.checkin_verified, false);
  assert.equal(result.quiz_outcome.verdict, "correct");
  assert.match(result.error, /签到请求暂未生效/);
  assert.deepEqual(app.calls, [
    "activate:提交签到", "extract-unanswered", "search", "review-and-submit",
  ]);
});

test("a stale mood CSS marker uses form validity and does not block the independent quiz", async () => {
  const app = setup({ staleMoodMarker: true });
  const result = await app.run();
  assert.equal(result.checkin_verified, true);
  assert.equal(result.quiz_outcome.verdict, "correct");
  assert.deepEqual(app.calls, [
    "activate:开心", "activate:提交签到", "extract-unanswered", "search", "review-and-submit"
  ]);
});
