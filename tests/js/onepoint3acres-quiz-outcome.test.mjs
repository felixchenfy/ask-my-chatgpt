import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const code = fs.readFileSync(new URL("../../chrome_extension/quiz-outcome.js", import.meta.url), "utf8");
const inspect = vm.runInNewContext(`${code}\ninspectQuizOutcome`);
const doc = (text, disabled = true) => ({ body: { innerText: text }, querySelectorAll: () => [{ innerText: "提交答案", disabled }] });

for (const [text, verdict] of [
  ["抱歉，你答题失败。扣除 大米 1 升", "incorrect"],
  ["回答错误", "incorrect"],
  ["恭喜你回答正确！获得奖励 大米 1 升", "correct"],
  ["答题成功", "correct"],
  ["今日已答题", "already_completed_unknown"],
  ["明日再来", "already_completed_unknown"],
  ["每日答题\n答对奖励 1 大米，答错扣除 1 大米。", "unknown"],
  ["【题目】哪个答案能让你回答正确？", "unknown"],
  ["", "unknown"],
  ["页面加载中", "unknown"],
  ["回答正确\n回答错误", "ambiguous"],
]) {
  test(`classifies explicit page state: ${text || "empty"}`, () => {
    const result = inspect(doc(text));
    assert.equal(result.verdict, verdict);
    assert.equal(result.completed, !["unknown", "ambiguous"].includes(verdict));
  });
}

test("a missing or disabled submit button never proves success", () => {
  for (const disabled of [true, false]) assert.equal(inspect(doc("", disabled)).completed, false);
  assert.equal(inspect({ body: { innerText: "" }, querySelectorAll: () => [] }).completed, false);
});

test("serialized function does not depend on service-worker globals", () => {
  const result = vm.runInNewContext(`(${inspect.toString()})()`, { document: doc("回答错误") });
  assert.equal(result.verdict, "incorrect");
});

test("website verification is pending, not a grade or proof of a completed answer", () => {
  const result = inspect({ body: { innerText: "每日答题\n正在验证..." },
    querySelectorAll: selector => selector === "button" ? [{ innerText: "正在验证..." }] : [] });
  assert.equal(result.pending, true);
  assert.equal(result.pending_label, "正在验证...");
  assert.equal(result.completed, false);
  assert.equal(result.verdict, "unknown");
});
