import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const source = fs.readFileSync(path.join(root, "chrome_extension", "service-worker.js"), "utf8");

function extractBlock(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(start, -1, `${startMarker} must exist in the Chrome companion`);
  assert.notEqual(end, -1, `${endMarker} must follow ${startMarker}`);
  return source.slice(start, end).trim();
}

function fakeNode({ text = "", attributes = {}, popup = false } = {}) {
  return {
    innerText: text,
    offsetParent: {},
    disabled: false,
    getAttribute(name) {
      return attributes[name] ?? null;
    },
    closest() {
      return popup ? {} : null;
    },
    getBoundingClientRect() {
      return { left: 10, top: 20, width: 80, height: 30 };
    },
  };
}

function loadInspector({ selected = [], interactive = [] } = {}) {
  const context = {
    self: {},
    getComputedStyle: () => ({ display: "block", visibility: "visible" }),
    document: {
      getElementById() { return null; },
      querySelector() { return null; },
      querySelectorAll(selector) {
        if (selector.startsWith('form ')) return [];
        return selector.startsWith('[aria-selected="true"]') ? selected : interactive;
      },
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, "chrome_extension", "chatgpt-text.js"), "utf8"), context);
  context.ChatGptText = context.self.ChatGptText;
  vm.runInNewContext(
    `${extractBlock("function inspectChatGptThinkingMode", "async function ensureChatGptThinkingMode")}\nself.inspect = inspectChatGptThinkingMode;`,
    context,
  );
  return context.self.inspect;
}

test("does not verify Max from an unrelated selected widget", () => {
  const selectedMax = fakeNode({ text: "Max", attributes: { "aria-checked": "true" } });
  const inspect = loadInspector({ selected: [selectedMax], interactive: [selectedMax] });
  const state = inspect("prefer_max");
  assert.equal(state.verified, false);
});

test("rejects an unlinked popup Max option", () => {
  const maxOption = fakeNode({ text: "Max", attributes: { role: "menuitemradio" }, popup: true });
  const inspect = loadInspector({ interactive: [maxOption] });
  const state = inspect("prefer_max");
  assert.equal(state.verified, false);
  assert.equal(state.maxOptions.length, 0);
});

test("opens a thinking control, selects Max, and verifies the result", async () => {
  const inspectSource = extractBlock("function inspectChatGptThinkingMode", "async function ensureChatGptThinkingMode");
  const ensureSource = extractBlock("async function ensureChatGptThinkingMode", "function inspectChatGptSubmission");
  const states = [
    {
      requestedMode: "prefer_max",
      verified: false,
      maxOptions: [],
      triggerCandidates: [{ label: "Thinking", point: { x: 50, y: 35 } }],
    },
    {
      requestedMode: "prefer_max",
      verified: false,
      maxOptions: [{ label: "Max", point: { x: 75, y: 90 } }],
      triggerCandidates: [],
    },
    {
      requestedMode: "prefer_max",
      verified: true,
      selectedLabel: "Max",
      maxOptions: [],
      triggerCandidates: [],
    },
  ];
  const clicks = [];
  const context = {
    self: {},
    execute: async () => states.shift(),
    trustedClick: async (_tabId, point) => clicks.push(point),
    setTimeout: callback => {
      callback();
      return 0;
    },
  };
  vm.runInNewContext(
    `${inspectSource}\n${ensureSource}\nself.ensure = ensureChatGptThinkingMode;`,
    context,
  );

  const state = await context.self.ensure(7, "prefer_max");
  assert.equal(state.verified, true);
  assert.equal(state.selectionAttempted, true);
  assert.equal(state.selectedLabel, "Max");
  assert.deepEqual(clicks, [{ x: 50, y: 35 }, { x: 75, y: 90 }]);
});

test("prefer_max continues when Max is unavailable, while strict max stops", async () => {
  const inspectSource = extractBlock("function inspectChatGptThinkingMode", "async function ensureChatGptThinkingMode");
  const ensureSource = extractBlock("async function ensureChatGptThinkingMode", "function inspectChatGptSubmission");
  const unavailable = {
    verified: false,
    selectedLabel: "current page setting",
    maxOptions: [],
    triggerCandidates: [],
  };
  const context = {
    self: {},
    execute: async () => unavailable,
    trustedClick: async () => {},
    setTimeout,
  };
  vm.runInNewContext(
    `${inspectSource}\n${ensureSource}\nself.ensure = ensureChatGptThinkingMode;`,
    context,
  );

  const preferred = await context.self.ensure(7, "prefer_max");
  assert.equal(preferred.verified, false);
  assert.equal(preferred.selectionAttempted, false);
  assert.match(preferred.fallbackReason, /did not expose/i);
  await assert.rejects(context.self.ensure(7, "max"), error => {
    assert.match(error.message, /strict Max was requested/);
    assert.equal(error.thinkingState.verified, false);
    assert.equal(error.thinkingState.selectionAttempted, false);
    assert.match(error.thinkingState.fallbackReason, /did not expose/i);
    return true;
  });
});

test("strict failure retains an attempted menu inspection and its final reason", async () => {
  let clicks = 0;
  const states = [
    { verified: false, triggerCandidates: [{ label: "Thinking", point: { x: 1, y: 2 } }], maxOptions: [] },
    { verified: false, triggerCandidates: [], maxOptions: [], unsupportedReason: "unsupported_thinking_effort_slider" }
  ];
  const context = {
    self: {}, execute: async () => states.shift(), trustedClick: async () => { clicks++; },
    setTimeout: callback => { callback(); }
  };
  vm.runInNewContext(
    `${extractBlock("function inspectChatGptThinkingMode", "function inspectChatGptSubmission")}\nself.ensure = ensureChatGptThinkingMode;`, context
  );
  await assert.rejects(context.self.ensure(7, "max"), error => {
    assert.equal(error.thinkingState.selectionAttempted, true);
    assert.equal(error.thinkingState.unsupportedReason, "unsupported_thinking_effort_slider");
    assert.equal(error.thinkingState.verified, false);
    return true;
  });
  assert.equal(clicks, 1);
});
