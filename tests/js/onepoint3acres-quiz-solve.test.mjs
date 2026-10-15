import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const root = path.resolve(import.meta.dirname, "..", "..");
const source = fs.readFileSync(path.join(root, "chrome_extension", "quiz-solve.js"), "utf8");
const context = vm.createContext({ Error });
vm.runInContext(`${source}\nglobalThis.interpretForTest = interpretQuizSolveResult;`, context);
vm.runInContext(`globalThis.matchForTest = assertReviewedQuizMatches;`, context);

test("background solve reports only explicit verified checkin while pending", async () => {
  const worker = fs.readFileSync(path.join(root, "chrome_extension", "service-worker.js"), "utf8");
  const start = worker.indexOf("async function solveQuizStep(");
  const end = worker.indexOf("\nasync function ", start + 1);
  const bodies = [];
  const page = vm.createContext({
    interpretQuizSolveResult: () => ({ pending: true }),
    request: async (_url, options) => { bodies.push(JSON.parse(options.body)); return {}; },
  });
  vm.runInContext(worker.slice(start, end), page);
  for (const flag of [true, false, undefined, "true"]) {
    assert.equal(await page.solveQuizStep(1, "run", {
      question: "Q", choices: ["A", "B"], checkinVerified: flag,
    }), false);
    assert.equal(bodies.at(-1).checkin_verified, flag === true);
  }
});

test("Codex review pending contains no answer", () => {
  const result = context.interpretForTest({ status: "pending", waiting_for: "codex_review" });
  assert.equal(result.pending, true);
  assert.equal(result.answer, null);
});

test("reviewed quiz requires identical question, ordered choices, and selected text", () => {
  const quiz = { question: "Q", choices: ["A", "B"] };
  const answer = { choice_index: 2, choice_text: "B" };
  context.matchForTest(quiz, quiz, answer);
  for (const live of [null, { completed: true }, { error: "captcha" },
    { ...quiz, question: "Q2" }, { ...quiz, choices: ["B", "A"] }]) {
    assert.throws(() => context.matchForTest(quiz, live, answer));
  }
  assert.throws(() => context.matchForTest(quiz, quiz, { ...answer, choice_text: "A" }));
  assert.throws(() => context.matchForTest(quiz, quiz, { ...answer, choice_index: "2" }));
});

test("returns pending without keeping the service worker alive", () => {
  assert.deepEqual({ ...context.interpretForTest({ status: "pending" }) }, {
    pending: true,
    answer: null,
  });
});

test("mismatch diagnostics distinguish extraction errors from actual changed choices", () => {
  const quiz = { question: "Q", choices: ["A", "B"] };
  const answer = { choice_index: 2, choice_text: "B" };
  for (const [live, message] of [
    [{ error: "没有找到明确题干" }, "页面读取失败"],
    [{ ...quiz, choices: ["B", "A"] }, "选项文字或顺序不一致"],
    [{ ...quiz, question: "other" }, "题干不一致"],
  ]) {
    assert.throws(() => context.matchForTest(quiz, live, answer), error => {
      assert.ok(error.message.includes(message));
      assert.equal(error.quizDiagnostic.expected, quiz);
      assert.equal(error.quizDiagnostic.live, live);
      return true;
    });
  }
});

test("returns a completed answer on a later service-worker wake", () => {
  const answer = { choice_index: 2, choice_text: "answer" };
  const result = context.interpretForTest({ status: "complete", answer });
  assert.equal(result.pending, false);
  assert.equal(result.answer.choice_index, 2);
});

test("propagates a completed background solve error", () => {
  assert.throws(
    () => context.interpretForTest({
      status: "error",
      error: "RuntimeError: confidence too low",
    }),
    /confidence too low/,
  );
});
