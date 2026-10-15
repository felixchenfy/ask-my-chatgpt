import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const source = fs.readFileSync(path.join(root, "chrome_extension", "chatgpt-text.js"), "utf8");
const context = { self: {} };
vm.runInNewContext(source, context);
const text = context.self.ChatGptText;

test("composer comparison tolerates rich-text whitespace normalization", () => {
  const expected = "Review this article:\n\n- explain MCP\n- keep facts";
  const rendered = "Review this article:\u00a0  - explain MCP   - keep facts";
  assert.equal(text.normalizeComposerText(expected), text.normalizeComposerText(rendered));
});

test("repeated source-card titles are rejected as an unverified response", () => {
  const extracted = [
    "Useful opening sentence",
    "Feiyu's Technical Blog",
    "Feiyu's Technical Blog",
    "Feiyu's Technical Blog",
    "Feiyu's Technical Blog",
    "Feiyu's Technical Blog",
  ].join("\n");
  assert.equal(text.responseLooksSuspicious(extracted), true);
});

test("a complete multi-section editorial review is accepted", () => {
  const review = [
    "Series-level feedback",
    "Define Codex before introducing the architecture.",
    "Part 1",
    "Keep the deprecated design short and historical.",
    "Part 2",
    "Explain the local agent before the MCP adapter.",
  ].join("\n");
  assert.equal(text.responseLooksSuspicious(review), false);
  const selected = text.chooseBestResponse(["Short hidden fragment", review]);
  assert.equal(selected.text, review);
  assert.equal(selected.suspicious, false);
});

test("a standalone machine-readable final marker is a terminal boundary", () => {
  const response = [
    "1. First complete finding.",
    "2. Second complete finding.",
    "BRIDGE-0.4.28-REGRESSION-OK",
  ].join("\n");
  assert.equal(text.responseLooksSuspicious(response), false);
});

test("a long prose fragment without a terminal boundary remains unverified", () => {
  assert.equal(
    text.responseLooksSuspicious("The quiet lighthouse blinked three times"),
    true,
  );
  assert.equal(
    text.responseLooksSuspicious("The quiet lighthouse blinked three times at midnight."),
    false,
  );
});

test("short exact answers remain valid", () => {
  assert.equal(text.responseLooksSuspicious("4"), false);
  assert.equal(text.responseLooksSuspicious("OK"), false);
  assert.equal(text.responseLooksSuspicious("第000"), true);
  assert.equal(text.responseLooksSuspicious("第0001行:"), true);
});

test("only a current-turn copy action plus an idle composer proves completion", () => {
  assert.equal(text.responseCompletionSignalReady({
    readOnly: true,
    responseNodeCount: 1,
    currentTurnCopyActionCount: 0,
  }), false);
  assert.equal(text.responseCompletionSignalReady({
    readOnly: true,
    responseNodeCount: 1,
    currentTurnCopyActionCount: 1,
  }), true);
  assert.equal(text.responseCompletionSignalReady({
    currentTurnCopyActionCount: 0,
    composerIdle: true,
  }), false);
  assert.equal(text.responseCompletionSignalReady({
    currentTurnCopyActionCount: 1,
    composerIdle: false,
  }), false);
  assert.equal(text.responseCompletionSignalReady({
    currentTurnCopyActionCount: 1,
    composerIdle: true,
  }), true);
});

test("unrelated or hidden page busy state does not mark the response as generating", () => {
  assert.equal(text.responseGeneratingSignalActive({
    unrelatedVisibleBusyCount: 2,
    hiddenBusyCount: 3,
  }), false);
  assert.equal(text.responseGeneratingSignalActive({
    visibleStopControlCount: 1,
  }), true);
  assert.equal(text.responseGeneratingSignalActive({
    currentTurnVisibleBusyCount: 1,
  }), true);
});

test("deterministic response expectations count characters without newlines", () => {
  assert.deepEqual(
    { ...text.responseExpectationState("abc\ndef", 6, 2) },
    { characterCount: 6, lineCount: 2, matches: true },
  );
  assert.equal(text.responseExpectationState("abc\ndef", 10, 2).matches, false);
  assert.equal(text.responseExpectationState("abc\ndef", 6, 3).matches, false);
  assert.equal(text.responseExpectationState("abc\ndef", 0, 0, 7).matches, false);
  assert.equal(text.responseExpectationState("abc\ndef", 0, 0, 6).matches, true);
});

test("response selection follows the submitted user turn instead of assistant counts", () => {
  const nodes = [
    { id: "old-user", order: 1, text: "Earlier question" },
    { id: "old-answer", order: 2, text: "Earlier answer" },
    { id: "submitted-user", order: 3, text: "Review both public posts." },
    { id: "streaming-fragment", order: 4, text: "1" },
    { id: "final-answer", order: 5, text: "1. Verdict: PUBLISH-READY." },
  ];
  const selected = text.responseNodesForSubmittedTurn({
    assistants: [nodes[1], nodes[3], nodes[4]],
    visibleAssistants: [nodes[1], nodes[3], nodes[4]],
    users: [nodes[0], nodes[2]],
    visibleUsers: [nodes[0], nodes[2]],
    assistantCountBefore: 3,
    userCountBefore: 1,
    submittedQuestion: "Review both public posts.",
    nodeText: node => node.text,
    follows: (first, second) => first.order < second.order,
  });
  assert.deepEqual(selected.nodes.map(node => node.id), ["streaming-fragment", "final-answer"]);
  assert.equal(selected.concurrentUserDetected, false);
});

test("response selection rejects a newer concurrent user turn", () => {
  const submitted = { id: "submitted", order: 1, text: "Review this." };
  const response = { id: "response", order: 2, text: "Review." };
  const newerUser = { id: "newer", order: 3, text: "Unrelated follow-up" };
  const selected = text.responseNodesForSubmittedTurn({
    assistants: [response],
    visibleAssistants: [response],
    users: [submitted, newerUser],
    visibleUsers: [submitted, newerUser],
    submittedQuestion: "Review this.",
    nodeText: node => node.text,
    follows: (first, second) => first.order < second.order,
  });
  assert.equal(selected.concurrentUserDetected, true);
});
