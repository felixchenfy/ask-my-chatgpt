import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const root = path.resolve(import.meta.dirname, "../..");
const worker = fs.readFileSync(path.join(root, "chrome_extension/service-worker.js"), "utf8");
const solve = fs.readFileSync(path.join(root, "chrome_extension/quiz-solve.js"), "utf8");
const step = worker.slice(worker.indexOf("async function solveQuizStep("),
  worker.indexOf("async function collectGoogleEvidence("));
const quiz = { question: "Q", choices: ["A", "B"] };

function setup({ pending = false, stale = false, denied = false, changeBeforeSubmit = false,
  verifying = false, inputFailed = false, deniedAtSubmit = false, stillDisabled = false } = {}) {
  const calls = [];
  let extraction = 0;
  const context = vm.createContext({
    Error, Date, setTimeout: fn => fn(),
    ONEPOINT_QUIZ_RUN_KEY: "quiz",
    chrome: { storage: { session: { set: async state => {
      calls.push(`save:${state.quiz.phase}`);
    } } } },
    extractQuiz: function extractQuiz() {},
    inspectAnswerControls: function inspectAnswerControls() {},
    inspectQuizOutcome: function inspectQuizOutcome() {},
    event: async () => {},
    request: async (url, args) => {
      calls.push(url);
      if (url === "/solve") return pending ? { status: "pending", waiting_for: "codex_review" }
        : { status: "complete", answer: { choice_index: 2, choice_text: "B", review_id: "review", decided_by: "codex" } };
      assert.ok(["/review/prepare", "/review/permit"].includes(url));
      assert.deepEqual(JSON.parse(args.body).quiz, quiz);
      if (denied || (deniedAtSubmit && url === "/review/permit")) throw new Error("already consumed");
      return { authorized: true };
    },
    execute: async (_tab, fn) => {
      if (fn.name === "extractQuiz") {
        extraction += 1;
        return stale || (changeBeforeSubmit && extraction === 2) ? { ...quiz, question: "new Q" } : quiz;
      }
      if (fn.name === "inspectAnswerControls") return { submitDisabled: stillDisabled };
      return verifying ? { completed: false, verdict: "unknown", pending_label: "正在验证..." }
        : { completed: true, verdict: "correct", evidence: "website grade" };
    },
    trustedActivateButton: async (_tab, text) => {
      calls.push(`click:${text}`);
      if (inputFailed) throw new Error("input was not delivered");
    },
  });
  vm.runInContext(`${solve}\n${step}\nglobalThis.run = solveQuizStep;`, context);
  return { calls, run: () => context.run(1, "run", quiz),
    observe: time => context.observeQuizSubmission(1, "run", time) };
}

test("waiting for Codex causes no option selection and no submission", async () => {
  const app = setup({ pending: true });
  assert.equal(await app.run(), false);
  assert.deepEqual(app.calls, ["/solve"]);
});

test("selection checks approval, then final submission consumes one-use permit", async () => {
  const app = setup();
  assert.equal((await app.run()).verdict, "correct");
  assert.deepEqual(app.calls, ["/solve", "/review/prepare", "click:B", "/review/permit", "save:awaiting_outcome", "click:提交答案"]);
});

test("selection failure records its phase and never calls final submit", async () => {
  const app = setup({ inputFailed: true });
  await assert.rejects(app.run(), error => {
    assert.equal(error.quizDiagnostic.phase, "selecting_answer");
    assert.equal(error.quizDiagnostic.finalSubmitAttempted, false);
    assert.equal(error.quizDiagnostic.submissionPermitConsumed, false);
    return true;
  });
  assert.deepEqual(app.calls, ["/solve", "/review/prepare", "click:B"]);
});

test("disabled submit after selection does not consume a permit", async () => {
  const app = setup({ stillDisabled: true });
  await assert.rejects(app.run(), /仍不可用/);
  assert.deepEqual(app.calls, ["/solve", "/review/prepare", "click:B"]);
});

test("approval expiring after selection prevents final submission", async () => {
  const app = setup({ deniedAtSubmit: true });
  await assert.rejects(app.run(), /already consumed/);
  assert.deepEqual(app.calls, ["/solve", "/review/prepare", "click:B", "/review/permit"]);
});

for (const flag of ["stale", "denied"]) {
  test(`${flag} approval cannot cause any website clicks`, async () => {
    const app = setup({ [flag]: true });
    await assert.rejects(app.run());
    assert.equal(app.calls.some(call => call.startsWith("click:")), false);
  });
}

test("question changing after selection prevents the final submit", async () => {
  const app = setup({ changeBeforeSubmit: true });
  await assert.rejects(app.run());
  assert.equal(app.calls.includes("click:B"), true);
  assert.equal(app.calls.includes("click:提交答案"), false);
  assert.equal(app.calls.includes("/review/permit"), false);
});

test("slow website verification is pending, then only observed without another click", async () => {
  const app = setup({ verifying: true });
  assert.equal(await app.run(), false);
  const calls = [...app.calls];
  assert.equal(await app.observe(Date.now()), false);
  assert.deepEqual(app.calls, calls);
  assert.equal(await app.observe(Date.now() - 300000), false);
  await assert.rejects(app.observe(Date.now() - 600001), /等待超时.*不重复提交/);
  assert.deepEqual(app.calls, calls);
});

test("a late explicit grade is accepted read-only, even after the observation deadline", async () => {
  const app = setup();
  assert.equal((await app.observe(Date.now() - 700000)).verdict, "correct");
  assert.deepEqual(app.calls, []);
});

test("a restarted worker dispatches the saved outcome phase to read-only observation", () => {
  const resume = worker.slice(worker.indexOf("async function resumeQuiz("), worker.indexOf("async function executeRun("));
  assert.match(resume, /state\.phase === "awaiting_outcome"\s*\? await observeQuizSubmission/);
});
