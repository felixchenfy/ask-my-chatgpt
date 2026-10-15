import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(path.resolve(import.meta.dirname, "../../chrome_extension/trusted-input.js"), "utf8");

const worker = fs.readFileSync(path.resolve(import.meta.dirname, "../../chrome_extension/service-worker.js"), "utf8");
const wrapper = worker.slice(worker.indexOf("async function trustedActivateButton("),
  worker.indexOf("async function trustedReplaceText("));

for (const inactive of [false, true]) {
  test(`Windows foreground focus is not a substitute for target verification (inactive=${inactive})`, async () => {
    const calls = [];
    const context = vm.createContext({
      setTimeout: fn => fn(),
      chrome: {
        tabs: { get: async () => ({ id: 7, windowId: 8, active: !inactive }), update: async () => {} },
        windows: { get: async () => ({ focused: false, state: "normal" }), update: async () => {} },
        debugger: { attach: async () => calls.push("attach"), detach: async () => calls.push("detach") },
      },
      focusAndActivateWithCdp: async () => calls.push("verified-target-input"),
    });
    vm.runInContext(wrapper, context);
    if (inactive) {
      await assert.rejects(context.trustedActivateButton(7, "Option"), /无法激活/);
      assert.deepEqual(calls, []);
    } else {
      await context.trustedActivateButton(7, "Option");
      assert.deepEqual(calls, ["attach", "verified-target-input", "detach"]);
    }
  });
}

for (const condition of ["focus_changed", "missing_observation", "dispatch_error", "missing_target"]) {
  test(`activation fails safely and releases its resources: ${condition}`, async () => {
    const calls = [];
    const context = vm.createContext({});
    vm.runInContext(source, context);
    const send = async (method, params) => {
      calls.push({ method, params });
      if (method === "Runtime.evaluate" && params.returnByValue === false) {
        return { result: condition === "missing_target" ? {} : { objectId: "button" } };
      }
      if (method === "Runtime.callFunctionOn") {
        return { result: { value: { ready: true, focused: true, x: 10, y: 20 } } };
      }
      if (method === "Input.dispatchMouseEvent" && condition === "dispatch_error") {
        throw new Error("lost input transport response");
      }
      if (method === "Runtime.evaluate" && params.awaitPromise) {
        return condition === "missing_observation" ? { exceptionDetails: { text: "page navigated" } }
          : { result: { value: { trustedClick: false, exactTargetFocused: false, trace: [] } } };
      }
      return {};
    };
    await assert.rejects(context.focusAndActivateWithCdp(send, "document.querySelector('button')", "test"));
    assert.equal(calls.some(call => call.method === "Input.dispatchKeyEvent"), false);
    assert.equal(calls.at(-1).method, "Emulation.setFocusEmulationEnabled");
    assert.equal(calls.at(-1).params.enabled, false);
    if (condition !== "missing_target") {
      assert.equal(calls.at(-2).method, "Runtime.releaseObject");
      assert.match(calls.at(-3).params.functionDeclaration, /state\?\.target !== this/);
      assert.match(calls.at(-3).params.functionDeclaration, /removeEventListener/);
    }
  });
}
