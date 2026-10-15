import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const root = path.resolve(import.meta.dirname, "..", "..");
const source = fs.readFileSync(
  path.join(root, "chrome_extension", "trusted-input.js"),
  "utf8",
);
const context = vm.createContext({});
vm.runInContext(source, context);

test("focuses the exact textarea, replaces text, and verifies the value", async () => {
  const calls = [];
  let runtimeChecks = 0;
  const sendCommand = async (method, params) => {
    calls.push({ method, params });
    if (method === "DOM.getDocument") return { root: { nodeId: 1 } };
    if (method === "DOM.querySelector") return { nodeId: 7 };
    if (method === "Runtime.evaluate") {
      runtimeChecks += 1;
      return runtimeChecks === 1
        ? { result: { value: { found: true, focused: true, activeTag: "TEXTAREA" } } }
        : { result: { value: { found: true, focused: true, value: "今日签到" } } };
    }
    return {};
  };

  const result = await context.focusAndReplaceTextWithCdp(
    sendCommand,
    'textarea[name="todaysay"]',
    "今日签到",
  );

  assert.equal(result.value, "今日签到");
  assert.deepEqual(
    calls.map((call) => call.method),
    [
      "DOM.getDocument",
      "DOM.querySelector",
      "DOM.focus",
      "Runtime.evaluate",
      "Input.dispatchKeyEvent",
      "Input.dispatchKeyEvent",
      "Input.insertText",
      "Runtime.evaluate",
    ],
  );
  assert.equal(calls[2].params.nodeId, 7);
  assert.equal(calls[4].params.type, "rawKeyDown");
  assert.equal(calls[4].params.modifiers, 2);
});

test("stops before typing when DOM focus did not take effect", async () => {
  const calls = [];
  const sendCommand = async (method, params) => {
    calls.push({ method, params });
    if (method === "DOM.getDocument") return { root: { nodeId: 1 } };
    if (method === "DOM.querySelector") return { nodeId: 7 };
    if (method === "Runtime.evaluate") {
      return { result: { value: { found: true, focused: false, activeTag: "BODY" } } };
    }
    return {};
  };

  await assert.rejects(
    context.focusAndReplaceTextWithCdp(sendCommand, "textarea", "今日签到"),
    /当前焦点：BODY/,
  );
  assert.equal(calls.some((call) => call.method === "Input.insertText"), false);
});

test("clicks the exact resolved button at its verified current viewport point", async () => {
  const calls = [];
  const sendCommand = async (method, params) => {
    calls.push({ method, params });
    if (method === "Runtime.evaluate" && params.returnByValue === false) {
      return { result: { objectId: "button-object" } };
    }
    if (method === "Runtime.callFunctionOn") {
      return {
        result: {
          value: {
            ready: true,
            focused: true,
            documentFocused: true,
            x: 410.5,
            y: 275.25,
          },
        },
      };
    }
    if (method === "Runtime.evaluate" && params.awaitPromise === true) {
      return {
        result: {
          value: {
            documentFocused: true,
            trustedClick: true,
            trace: [
              { type: "pointerdown", isTrusted: true, targetTag: "BUTTON" },
              { type: "mousedown", isTrusted: true, targetTag: "BUTTON" },
              { type: "mouseup", isTrusted: true, targetTag: "BUTTON" },
              { type: "click", isTrusted: true, targetTag: "BUTTON" },
            ],
          },
        },
      };
    }
    return {};
  };

  const result = await context.focusAndActivateWithCdp(
    sendCommand,
    "document.querySelector('button')",
    "提交按钮",
  );

  assert.deepEqual(
    calls.map((call) => call.method),
    [
      "Page.bringToFront",
      "Emulation.setFocusEmulationEnabled",
      "Runtime.evaluate",
      "Runtime.callFunctionOn",
      "Input.dispatchMouseEvent",
      "Input.dispatchMouseEvent",
      "Input.dispatchMouseEvent",
      "Runtime.evaluate",
      "Runtime.callFunctionOn",
      "Runtime.releaseObject",
      "Emulation.setFocusEmulationEnabled",
    ],
  );
  assert.equal(calls[1].params.enabled, true);
  assert.equal(calls[3].params.objectId, "button-object");
  assert.match(calls[3].params.functionDeclaration, /scrollIntoView/);
  assert.match(calls[3].params.functionDeclaration, /__wcaTrustedActivationTrace/);
  assert.equal(calls[4].params.type, "mouseMoved");
  assert.equal(calls[5].params.type, "mousePressed");
  assert.equal(calls[5].params.x, 410.5);
  assert.equal(calls[6].params.type, "mouseReleased");
  assert.equal(calls[7].params.awaitPromise, true);
  assert.match(calls[7].params.expression, /setTimeout/);
  assert.equal(calls.at(-1).params.enabled, false);
  assert.equal(result.trace.at(-1).type, "click");
  assert.equal(result.trace.at(-1).isTrusted, true);
});

test("falls back to trusted Enter when a hidden page receives no mouse click", async () => {
  const calls = [];
  let traceReads = 0;
  const sendCommand = async (method, params) => {
    calls.push({ method, params });
    if (method === "Runtime.evaluate" && params.returnByValue === false) {
      return { result: { objectId: "button-object" } };
    }
    if (method === "Runtime.callFunctionOn") {
      return {
        result: {
          value: {
            ready: true,
            focused: true,
            documentFocused: false,
            visibilityState: "hidden",
            x: 410.5,
            y: 275.25,
          },
        },
      };
    }
    if (method === "Runtime.evaluate" && params.awaitPromise === true) {
      traceReads += 1;
      return traceReads === 1
        ? {
            result: {
              value: {
                documentFocused: false,
                visibilityState: "hidden",
                trustedClick: false,
                exactTargetFocused: true,
                trace: [
                  { type: "pointerdown", isTrusted: true, targetTag: "BUTTON" },
                  { type: "mousedown", isTrusted: true, targetTag: "BUTTON" },
                ],
              },
            },
          }
        : {
            result: {
              value: {
                documentFocused: false,
                visibilityState: "hidden",
                trustedClick: true,
                exactTargetFocused: true,
                trace: [
                  { type: "pointerdown", isTrusted: true, targetTag: "BUTTON" },
                  { type: "mousedown", isTrusted: true, targetTag: "BUTTON" },
                  { type: "click", isTrusted: true, targetTag: "BUTTON" },
                ],
              },
            },
          };
    }
    return {};
  };

  const result = await context.focusAndActivateWithCdp(
    sendCommand,
    "document.querySelector('button')",
    "提交按钮",
  );

  assert.deepEqual(
    calls.map((call) => call.method),
    [
      "Page.bringToFront",
      "Emulation.setFocusEmulationEnabled",
      "Runtime.evaluate",
      "Runtime.callFunctionOn",
      "Input.dispatchMouseEvent",
      "Input.dispatchMouseEvent",
      "Input.dispatchMouseEvent",
      "Runtime.evaluate",
      "Input.dispatchKeyEvent",
      "Input.dispatchKeyEvent",
      "Runtime.evaluate",
      "Runtime.callFunctionOn",
      "Runtime.releaseObject",
      "Emulation.setFocusEmulationEnabled",
    ],
  );
  assert.match(calls[7].params.expression, /if \(trustedClick \|\| false\)/);
  assert.equal(calls[8].params.type, "keyDown");
  assert.equal(calls[8].params.text, "\r");
  assert.equal(calls[8].params.unmodifiedText, "\r");
  assert.equal(calls[8].params.key, "Enter");
  assert.equal(calls[8].params.code, "Enter");
  assert.equal(calls[8].params.windowsVirtualKeyCode, 13);
  assert.equal(calls[9].params.type, "keyUp");
  assert.equal(calls[9].params.key, "Enter");
  assert.match(calls[10].params.expression, /if \(trustedClick \|\| true\)/);
  assert.equal(result.trace.at(-1).type, "click");
  assert.equal(result.trace.at(-1).isTrusted, true);
});

test("rejects activation when the exact button receives no trusted click", async () => {
  const calls = [];
  let traceReads = 0;
  const sendCommand = async (method, params) => {
    calls.push({ method, params });
    if (method === "Runtime.evaluate" && params.returnByValue === false) {
      return { result: { objectId: "button-object" } };
    }
    if (method === "Runtime.callFunctionOn") {
      return {
        result: {
          value: {
            ready: true,
            focused: true,
            documentFocused: true,
            x: 410.5,
            y: 275.25,
          },
        },
      };
    }
    if (method === "Runtime.evaluate" && params.awaitPromise === true) {
      traceReads += 1;
      return {
        result: {
          value: {
            documentFocused: true,
            trustedClick: false,
            exactTargetFocused: true,
            trace: [{ type: "click", isTrusted: false, targetTag: "BUTTON" }],
          },
        },
      };
    }
    return {};
  };

  await assert.rejects(
    context.focusAndActivateWithCdp(sendCommand, "document.querySelector('button')", "提交按钮"),
    /提交按钮没有收到可信 click 事件/,
  );
  assert.equal(traceReads, 2);
  assert.deepEqual(
    calls
      .filter((call) => call.method === "Input.dispatchKeyEvent")
      .map((call) => call.params.type),
    ["keyDown", "keyUp"],
  );
  assert.equal(calls.at(-2).method, "Runtime.releaseObject");
  assert.equal(calls.at(-1).method, "Emulation.setFocusEmulationEnabled");
  assert.equal(calls.at(-1).params.enabled, false);
});

test("stops before activation when the current hit point is covered", async () => {
  const calls = [];
  const sendCommand = async (method, params) => {
    calls.push({ method, params });
    if (method === "Runtime.evaluate" && params.returnByValue === false) {
      return { result: { objectId: "button-object" } };
    }
    if (method === "Runtime.callFunctionOn") {
      return { result: { value: { ready: false, x: 10, y: 20, hitTag: "DIV" } } };
    }
    return {};
  };

  await assert.rejects(
    context.focusAndActivateWithCdp(sendCommand, "document.querySelector('button')", "提交按钮"),
    /提交按钮当前无法被准确点击；命中元素：DIV/,
  );
  assert.equal(calls.some((call) => call.method === "Input.dispatchMouseEvent"), false);
  assert.equal(calls.at(-2).method, "Runtime.releaseObject");
  assert.equal(calls.at(-1).method, "Emulation.setFocusEmulationEnabled");
  assert.equal(calls.at(-1).params.enabled, false);
});
