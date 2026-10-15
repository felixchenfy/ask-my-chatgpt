import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../../chrome_extension/service-worker.js", import.meta.url), "utf8");
const helpers = { self: {}, URL };
for (const name of ["chatgpt-text", "chatgpt-tabs"]) {
  vm.runInNewContext(fs.readFileSync(new URL(`../../chrome_extension/${name}.js`, import.meta.url), "utf8"), helpers);
}

for (const completeText of ["Full verified review. ".repeat(200), "以下审"]) {
  test(`read loop wakes frozen prefix without resending; minimum length ${completeText.length > 100 ? "passes" : "still fails"}`, async () => {
    let now = 1;
    let foregrounded = false;
    let focusLease = false;
    let detachCount = 0;
    let wakeCount = 0;
    const callbacks = [];
    const storage = {};
    const canonical = "https://chatgpt.com/c/render-test";
    const sandbox = {
      URL, Date: { now: () => now }, setTimeout: (callback, ms) => { now += ms; callback(); },
      ChatGptText: helpers.self.ChatGptText, ChatGptTabs: helpers.self.ChatGptTabs,
      acquireChatGptTab: async () => ({ tab: { id: 1 }, source: "existing" }),
      prepareChatGptText: async () => {}, releaseChatGptTab: async () => {},
      assertChatGptDestination: async (_id, expected) => { assert.equal(expected, canonical); return expected; },
      waitForChatGptDestination: async (_id, expected) => { assert.equal(expected, canonical); return expected; },
      inspectChatGptResponse: function response() {},
      summarizeChatGptObservation: state => ({ renderEvidence: state.renderEvidence }),
      execute: async (_id, func, args) => {
        if (func.name !== "response") return { visibility: focusLease ? "visible" : "hidden", focused: true };
        assert.equal(func.name, "response");
        assert.equal(args[4], "original-user");
        return { hasNewResponse: true, submittedUserPresent: true,
          responseCandidates: [focusLease ? completeText : "以下审"],
          responseComplete: focusLease, generating: false,
          completionEvidence: { composerIdle: true },
          renderEvidence: { visibility: focusLease ? "visible" : "hidden", focused: foregrounded },
          conversationUrl: canonical, conversationUrlCanonical: true };
      },
      requestChatGpt: async (route, options) => {
        assert.ok(["/event", "/complete"].includes(route));
        if (route === "/complete") callbacks.push(JSON.parse(options.body));
      },
      chrome: { storage: { session: { get: async () => storage, set: async data => Object.assign(storage, data) } },
        debugger: {
          attach: async () => { assert.equal(foregrounded, true); assert.equal(focusLease, false); },
          sendCommand: async (_target, method, params) => {
            assert.equal(method, "Emulation.setFocusEmulationEnabled"); focusLease = params.enabled;
          },
          detach: async () => { detachCount++; assert.equal(focusLease, false); },
        },
        tabs: { get: async () => ({ id: 1 }), update: async (id, options) => {
          assert.equal(id, 1); assert.equal(options.active, true); wakeCount++; foregrounded = true;
        } } },
    };
    const wakeStart = source.indexOf("async function maybeWakeChatGptRendering");
    vm.runInNewContext(source.slice(wakeStart, source.indexOf("// ChatGPT can briefly pause", wakeStart)), sandbox);
    const readStart = source.indexOf("async function executeChatGptRead");
    vm.runInNewContext(source.slice(readStart, source.indexOf("function pageGuard", readStart)), sandbox);
    await sandbox.executeChatGptRead({ run_id: "fixture-read", conversation_url: canonical,
      expected_message_id: "original-user", minimum_response_characters: 3500, response_timeout_seconds: 60 });
    assert.equal(wakeCount, 1);
    assert.equal(focusLease, false);
    assert.equal(detachCount, 1);
    assert.equal(callbacks.length, 1);
    assert.equal(callbacks[0].ok, completeText.length > 3500);
    if (callbacks[0].ok) {
      assert.equal(callbacks[0].response_extraction_verified, true);
      assert.equal(callbacks[0].conversation_url, canonical);
      assert.ok(callbacks[0].response_text.startsWith("Full verified review."));
    } else {
      assert.match(callbacks[0].error, /unverified/);
    }
  });
}
