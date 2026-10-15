import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const context = { self: {} };
for (const name of ["chatgpt-text", "poll-scheduler"]) {
  vm.runInNewContext(fs.readFileSync(new URL(`../../chrome_extension/${name}.js`, import.meta.url), "utf8"), context);
}
const api = context.self.ChatGptText;
const user = { id: "submitted", text: "Review the new PDF.", order: 3 };
const oldUser = { id: "old-user", text: "Old prompt", order: 1 };
const oldAnswer = { id: "old-answer", text: "Old answer.", order: 2 };
const answer = { id: "new-answer", text: "New review.", order: 4 };
const selection = overrides => api.responseNodesForSubmittedTurn({
  users: [oldUser, user], visibleUsers: [oldUser, user],
  assistants: [oldAnswer, answer], visibleAssistants: [oldAnswer, answer],
  userCountBefore: 1, submittedQuestion: user.text, submittedMessageId: user.id,
  nodeText: n => n.text, nodeId: n => n.id, follows: (a, b) => a.order < b.order,
  ...overrides,
});

test("recovery of an unanswered new PDF never accepts a prior review", () => {
  assert.equal(selection({ readOnly: true, visibleAssistants: [oldAnswer] }).nodes.length, 0);
  assert.deepEqual([...selection({ readOnly: true }).nodes].map(n => n.id), [answer.id]);
});
test("missing submitted node cannot be replaced by a concurrent question", () => {
  const concurrent = { id: "other", text: "Different question", order: 5 };
  const result = selection({ visibleUsers: [oldUser, concurrent],
    users: [oldUser, user, concurrent], submittedMessageId: "" });
  assert.equal(result.nodes.length, 0);
});
test("a missing durable ID fails closed even when prompt text matches", () => {
  assert.equal(selection({ submittedMessageId: "unmounted" }).nodes.length, 0);
});
test("historical repeated prompt without new user evidence is not accepted", () => {
  assert.equal(selection({ submittedMessageId: "", userCountBefore: 2 }).nodes.length, 0);
});
test("emoji and all Python splitlines boundaries have consistent metrics", () => {
  assert.equal(api.responseExpectationState("😀", 1, 1).matches, true);
  for (const boundary of ["\r", "\r\n", "\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]) {
    assert.equal(api.normalizeResponseText(`abc${boundary}def`), "abc\ndef");
    assert.equal(api.responseExpectationState(`abc${boundary}def`, 6, 2).matches, true);
  }
});

test("render wake is bounded and only for a stalled incomplete response", () => {
  const hidden = { responseComplete: false, renderEvidence: { visibility: "hidden" } };
  assert.equal(api.renderWakeNeeded(hidden, 7999, false), false);
  assert.equal(api.renderWakeNeeded(hidden, 8000, false), true);
  assert.equal(api.renderWakeNeeded(hidden, 8000, true), false);
  assert.equal(api.renderWakeNeeded({ ...hidden, responseComplete: true }, 8000, false), false);
  const idlePrefix = { hasNewResponse: true, generating: false,
    completionEvidence: { composerIdle: true }, renderEvidence: { visibility: "visible" } };
  assert.equal(api.renderWakeNeeded(idlePrefix, 8000, false), true);
  assert.equal(api.renderWakeNeeded({ ...idlePrefix, generating: true }, 8000, false), false);
  assert.equal(api.renderWakeNeeded({}, 8000, false), false);
});

test("render wake focuses only the verified tab once across worker restarts, without submitting", async () => {
  const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
  const start = source.indexOf("async function maybeWakeChatGptRendering");
  const end = source.indexOf("// ChatGPT can briefly pause", start);
  const actions = [];
  const storage = {};
  let rejectDestination = false;
  const sandbox = {
    Date: { now: () => 9000 }, ChatGptText: api,
    assertChatGptDestination: async (_tab, url) => {
      assert.equal(url, "https://chatgpt.com/c/target");
      if (rejectDestination) throw new Error("conversation_mismatch");
    },
    requestChatGpt: async route => { assert.equal(route, "/event"); actions.push("event"); },
    execute: async () => ({ visibility: "visible", focused: true }),
    chrome: {
      debugger: {
        attach: async target => { assert.equal(target.tabId, 5); actions.push("attach"); },
        sendCommand: async (target, method, params) => {
          assert.equal(target.tabId, 5);
          assert.equal(method, "Emulation.setFocusEmulationEnabled");
          actions.push(params.enabled ? "focus-on" : "focus-off");
        },
        detach: async target => { assert.equal(target.tabId, 5); actions.push("detach"); },
      },
      storage: { session: { get: async () => storage, set: async value => Object.assign(storage, value) } },
      tabs: { get: async () => ({ id: 5, windowId: 7 }), update: async (id, options) => {
        assert.equal(id, 5); assert.equal(options.active, true); actions.push("tab-active");
      } },
      windows: { get: async () => ({ state: "minimized" }), update: async (id, options) => {
        assert.equal(id, 7); actions.push(options.state || "focused");
      } },
    },
  };
  vm.runInNewContext(source.slice(start, end), sandbox);
  const state = { responseComplete: false, renderEvidence: { visibility: "hidden" } };
  const watch = () => ({ text: "以下审", unchangedSince: 0, attempted: false });
  const firstWatch = watch();
  await sandbox.maybeWakeChatGptRendering(5, "run-1", state, "以下审", firstWatch, "https://chatgpt.com/c/target");
  assert.deepEqual(actions, ["normal", "focused", "tab-active", "attach", "focus-on", "event"]);
  await sandbox.maybeWakeChatGptRendering(5, "run-1", state, "以下审", firstWatch, "https://chatgpt.com/c/target");
  await sandbox.maybeWakeChatGptRendering(5, "run-1", state, "以下审", watch(), "https://chatgpt.com/c/target");
  assert.equal(actions.length, 6);
  rejectDestination = true;
  await assert.rejects(sandbox.maybeWakeChatGptRendering(5, "run-2", state, "以下审", watch(), "https://chatgpt.com/c/target"), /conversation_mismatch/);
  assert.equal(actions.length, 6);
  await sandbox.releaseChatGptRendering(firstWatch);
  assert.deepEqual(actions.slice(-2), ["focus-off", "detach"]);
  await sandbox.releaseChatGptRendering(firstWatch);
  assert.equal(actions.length, 8);
});
test("render lease fails closed and cleans up only its own debugger", async () => {
  const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
  const start = source.indexOf("async function maybeWakeChatGptRendering");
  for (const failure of ["attach", "enable", "hidden", "disable"]) {
    const actions = [];
    const sandbox = {
      Date: { now: () => 9000 }, ChatGptText: api,
      assertChatGptDestination: async () => "https://chatgpt.com/c/target",
      execute: async () => ({ visibility: failure === "hidden" ? "hidden" : "visible" }),
      requestChatGpt: async route => { assert.equal(route, "/event"); },
      chrome: {
        storage: { session: { get: async () => ({}), set: async () => {} } },
        tabs: { get: async () => ({ id: 5 }), update: async () => {} },
        debugger: {
          attach: async () => { if (failure === "attach") throw new Error("Other debugger owns this tab"); actions.push("attach"); },
          sendCommand: async (_target, method, params) => {
            assert.equal(method, "Emulation.setFocusEmulationEnabled");
            actions.push(params.enabled ? "enable" : "disable");
            if (failure === (params.enabled ? "enable" : "disable")) throw new Error("CDP command failed");
          },
          detach: async () => { actions.push("detach"); },
        },
      },
    };
    vm.runInNewContext(source.slice(start, source.indexOf("// ChatGPT can briefly pause", start)), sandbox);
    const watch = { text: "prefix", unchangedSince: 0, attempted: false };
    const wake = () => sandbox.maybeWakeChatGptRendering(5, "test", {
      responseComplete: false, renderEvidence: { visibility: "hidden" },
    }, "prefix", watch, "https://chatgpt.com/c/target");
    if (failure === "disable") await wake();
    else await assert.rejects(wake);
    await sandbox.releaseChatGptRendering(watch);
    if (failure === "attach") assert.deepEqual(actions, []);
    else assert.deepEqual(actions, ["attach", "enable", "disable", "detach"]);
    assert.equal(watch.debuggerTarget, undefined);
  }
});

test("busy unrelated workflow does not block ChatGPT lane or run it twice", async () => {
  let finishOther;
  let finishChat;
  let calls = 0;
  let secondStarted;
  const second = new Promise(resolve => { secondStarted = resolve; });
  const other = context.self.PollScheduler.singleFlight(() => new Promise(r => { finishOther = r; }));
  const chat = context.self.PollScheduler.singleFlight(() => {
    calls++;
    if (calls === 2) secondStarted();
    return new Promise(r => { finishChat = r; });
  });
  const a = other(); const b = chat();
  assert.equal(calls, 1);
  const c = chat();
  assert.equal(calls, 1);
  finishChat(); await second;
  assert.equal(calls, 2);
  finishChat(); finishOther();
  await Promise.all([a,b,c]);
});

test("redirected send/read workflow stops before upload, mode changes, typing or submission", async () => {
  const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
  const tabContext = { self: {}, URL };
  vm.runInNewContext(fs.readFileSync(new URL("../../chrome_extension/chatgpt-tabs.js", import.meta.url), "utf8"), tabContext);
  const start = source.indexOf("async function executeChatGpt(command)");
  const end = source.indexOf("function pageGuard()", start);
  for (const action of ["executeChatGpt", "executeChatGptRead"]) {
    const completions = [];
    const failMutation = () => { assert.fail("A redirected task must not change or send anything"); };
    const sandbox = {
      setTimeout: callback => callback(), URL,
      requestChatGpt: async (route, options) => { if (route === "/complete") completions.push(JSON.parse(options.body)); },
      acquireChatGptTab: async () => ({ tab: { id: 1 }, source: "created" }),
      prepareChatGptText: async () => {},
      assertChatGptDestination: async (_tab, expected, options) => {
        const state = tabContext.self.ChatGptTabs.destinationState(expected, "https://chatgpt.com/", options);
        if (!state.ok) throw new Error(state.error);
        return state.url;
      },
      waitForChatGptDestination: async (_tab, expected, options) => {
        const state = tabContext.self.ChatGptTabs.destinationState(expected, "https://chatgpt.com/", options);
        if (!state.ok) throw new Error(state.error);
        return state.url;
      },
      ensureChatGptThinkingMode: failMutation, trustedAttachFile: failMutation,
      trustedReplaceText: failMutation, submitChatGptForm: failMutation,
      endChatGptSubmission: function end() {},
      execute: async (_tab, func) => { if (func.name !== "end") failMutation(); },
      releaseChatGptTab: async () => {}, releaseChatGptRendering: async () => {},
      chrome: { tabs: { get: async () => ({ url: "https://chatgpt.com/" }), update: async () => {} } },
    };
    vm.runInNewContext(source.slice(start, end), sandbox);
    await sandbox[action]({ run_id: "offline-test", conversation_url: "https://chatgpt.com/c/expected", attachment_path: "fixture.pdf" });
    assert.equal(completions.length, 1);
    assert.equal(completions[0].ok, false);
    assert.match(completions[0].error, /conversation_mismatch/);
  }
});

test("actual execute wrapper preserves structured destination evidence for the guard", async () => {
  const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
  const tabContext = { self: {}, URL };
  vm.runInNewContext(fs.readFileSync(new URL("../../chrome_extension/chatgpt-tabs.js", import.meta.url), "utf8"), tabContext);
  const start = source.indexOf("async function execute(tabId");
  const end = source.indexOf("async function trustedClick", start);
  const sandbox = {
    chrome: { scripting: { executeScript: async ({ args }) => [{ result:
      tabContext.self.ChatGptTabs.destinationState(args[0], "https://chatgpt.com/", args[1])
    }] } },
  };
  vm.runInNewContext(source.slice(start, end), sandbox);
  await assert.rejects(
    sandbox.assertChatGptDestination(1, "https://chatgpt.com/c/expected"),
    error => error.destinationEvidence?.reasonCode === "canonical_redirected_home",
  );
});

test("initial destination wait tolerates only a transient canonical-to-home route", async () => {
  const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
  const start = source.indexOf("async function waitForChatGptDestination");
  const end = source.indexOf("async function trustedClick", start);
  let now = 0, calls = 0;
  const mismatch = reasonCode => Object.assign(new Error("conversation_mismatch"), {
    destinationEvidence: { reasonCode, expected: "https://chatgpt.com/c/expected", observed: "https://chatgpt.com/" }
  });
  const sandbox = {
    Date: { now: () => now }, setTimeout: callback => { now += 500; callback(); },
    assertChatGptDestination: async () => {
      calls++;
      if (calls < 3) throw mismatch("canonical_redirected_home");
      return "https://chatgpt.com/c/expected";
    },
  };
  vm.runInNewContext(source.slice(start, end), sandbox);
  assert.equal(await sandbox.waitForChatGptDestination(1, "https://chatgpt.com/c/expected"),
    "https://chatgpt.com/c/expected");
  assert.equal(calls, 3);

  calls = 0;
  sandbox.assertChatGptDestination = async () => { calls++; throw mismatch("destination_mismatch"); };
  await assert.rejects(sandbox.waitForChatGptDestination(1, "https://chatgpt.com/c/expected"));
  assert.equal(calls, 1);
});

test("initial destination wait remains bounded and retains retry evidence", async () => {
  const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
  const start = source.indexOf("async function waitForChatGptDestination");
  const end = source.indexOf("async function trustedClick", start);
  let now = 0;
  const sandbox = {
    Date: { now: () => now }, setTimeout: callback => { now += 500; callback(); },
    assertChatGptDestination: async () => { throw Object.assign(new Error("conversation_mismatch"), {
      destinationEvidence: { reasonCode: "canonical_redirected_home", expected: "expected", observed: "home" }
    }); },
  };
  vm.runInNewContext(source.slice(start, end), sandbox);
  await assert.rejects(
    sandbox.waitForChatGptDestination(1, "https://chatgpt.com/c/expected", {}, 1500),
    error => error.destinationEvidence?.waitElapsedMs === 1500
      && error.destinationEvidence?.observationCount === 4,
  );
});

test("send workflow acknowledges WEB once, resolves same turn, and never submits twice", async () => {
  const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
  const helpers = { self: {}, URL };
  for (const name of ["chatgpt-text", "chatgpt-tabs"]) {
    vm.runInNewContext(fs.readFileSync(new URL(`../../chrome_extension/${name}.js`, import.meta.url), "utf8"), helpers);
  }
  let now = 1;
  let sends = 0;
  let polls = 0;
  let wrote = false;
  const acknowledgements = [];
  const completions = [];
  const provisional = "https://chatgpt.com/c/WEB:pending";
  const canonical = "https://chatgpt.com/c/resolved";
  const sandbox = {
    URL, Date: { now: () => now }, setTimeout: (callback, delay) => { now += delay; callback(); },
    ChatGptTabs: helpers.self.ChatGptTabs, ChatGptText: helpers.self.ChatGptText,
    CHATGPT_RESPONSE_STABLE_MS: 20000, CHATGPT_EXPECTATION_SETTLE_MS: 60000,
    inspectChatGptThrottle: function throttle() {}, inspectChatGptComposer: function composer() {},
    inspectChatGptSubmission: function submission() {}, inspectChatGptResponse: function response() {},
    beginChatGptSubmission: function begin() {}, endChatGptSubmission: function end() {},
    acquireChatGptTab: async () => ({ tab: { id: 1 }, source: "created" }),
    prepareChatGptText: async () => {}, assertChatGptDestination: async (_tab, wanted) => wanted,
    waitForChatGptDestination: async (_tab, wanted) => wanted,
    ensureChatGptThinkingMode: async () => ({ requestedMode: "current" }),
    trustedAttachFile: async () => {}, trustedReplaceText: async () => { wrote = true; },
    submitChatGptForm: async () => { sends++; }, releaseChatGptTab: async () => {},
    summarizeChatGptObservation: () => ({}),
    maybeWakeChatGptRendering: async () => {}, releaseChatGptRendering: async () => {},
    requestChatGpt: async (route, options) => {
      const body = JSON.parse(options.body);
      if (route === "/submitted") acknowledgements.push(body);
      if (route === "/complete") completions.push(body);
      return { ok: true };
    },
    execute: async (_tab, func, args) => {
      if (func.name === "begin" || func.name === "end") return {};
      if (func.name === "throttle") return { throttled: false };
      if (func.name === "composer") return { ready: true, attachmentReady: true, composerPoint: {}, sendPoint: {}, sendReady: true,
        composerValueNormalized: wrote ? "Review PDF" : "", expectedQuestionNormalized: "Review PDF",
        attachmentVisible: true, attachmentSelected: true, assistantCount: 0, userCount: 0 };
      if (func.name === "submission") return { submitted: true, messageId: "original", conversationUrl: provisional };
      assert.equal(func.name, "response");
      polls++;
      assert.equal(args[4], "original");
      assert.equal(args[5], polls <= 2 ? provisional : canonical);
      return { hasNewResponse: true, submittedUserPresent: true, responseCandidates: ["Complete review."],
        responseComplete: true, generating: false, conversationUrl: polls === 1 ? provisional : canonical,
        conversationUrlCanonical: polls > 1 };
    },
  };
  vm.runInNewContext(source.slice(source.indexOf("async function executeChatGpt(command)"), source.indexOf("async function executeChatGptRead")), sandbox);
  await sandbox.executeChatGpt({ run_id: "fixture", question: "Review PDF", attachment_path: "fixture.pdf" });
  assert.equal(sends, 1);
  assert.equal(acknowledgements.length, 2);
  assert.deepEqual(acknowledgements.map(item => item.conversation_url), [provisional, canonical]);
  assert.deepEqual(acknowledgements.map(item => item.message_id), ["original", "original"]);
  assert.equal(completions.length, 1);
  assert.equal(completions[0].ok, true);
  assert.equal(completions[0].conversation_url, canonical);
});
