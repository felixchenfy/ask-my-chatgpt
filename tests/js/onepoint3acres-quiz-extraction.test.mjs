import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const worker = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
const source = worker.slice(worker.indexOf("function extractQuiz()"), worker.indexOf("function inspectAnswerControls("));

function read({ reverse = false, missingSubmit = false, captcha = false } = {}) {
  // Shape observed on the live 2026-09-03 page; no pixel or viewport dependency.
  const node = (innerText, order) => ({ innerText, order,
    compareDocumentPosition(other) { return other.order > this.order ? 4 : 2; },
    getBoundingClientRect() { throw new Error("Geometry must not select answers"); } });
  const heading = node("【题目】遇到不文明言论，如何合理应对？", 1);
  const choices = ['点楼层内的“反对"', '点楼层内”举报": 举报不文明内容',
    '严于律己，不因为别人言行不文明而以同样方式还以颜色。', '这些都是'];
  if (reverse) choices.reverse();
  const buttons = choices.map((text, index) => node(text, index + 2));
  if (!missingSubmit) buttons.push(node("提交答案", 10));
  const body = { innerText: captcha ? "人机验证" : "每日答题" };
  const container = { parentElement: body, querySelectorAll: () => buttons };
  heading.parentElement = container;
  const document = { body, querySelectorAll(selector) {
    if (selector === "h1,h2,h3") return [heading];
    // Other page controls can overlap the quiz vertically, but aren't options.
    return [node("发帖", 0), ...buttons, node("换一批", 12)];
  } };
  const context = vm.createContext({ document, location: { pathname: "/next/daily-question" } });
  vm.runInContext(source, context);
  return JSON.parse(JSON.stringify(context.extractQuiz()));
}

test("quiz extraction uses its own DOM container, preserving all exact ordered choices", () => {
  const quiz = read();
  assert.equal(quiz.question, "遇到不文明言论，如何合理应对？");
  assert.equal(quiz.choices.length, 4);
  assert.equal(quiz.choices[0], '点楼层内的“反对"');
  assert.equal(quiz.choices[3], "这些都是");
});
test("changed order remains changed, never sorted to satisfy approval", () => {
  assert.equal(read({ reverse: true }).choices[0], "这些都是");
});
test("missing submit or access challenge still blocks extraction", () => {
  assert.ok(read({ missingSubmit: true }).error);
  assert.equal(read({ captcha: true }).error, "页面要求人工验证");
});
