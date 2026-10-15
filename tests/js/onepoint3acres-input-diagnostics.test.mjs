import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(path.resolve(import.meta.dirname, "../../chrome_extension/input-diagnostics.js"), "utf8");

for (const redirected of [false, true]) {
  test(`input diagnostic stays local and cleans up owned tabs (redirected=${redirected})`, async () => {
    const tabs = new Map();
    const removed = [];
    const detached = [];
    let nextId = 1;
    let activations = 0;
    const fixture = "http://127.0.0.1:18731/diagnostics/input-fixture";
    const context = vm.createContext({
      BRIDGE: "http://127.0.0.1:18731", EXTENSION_VERSION: "test",
      waitForTab: async () => {},
      setTimeout: fn => fn(),
      focusAndActivateWithCdp: async () => { activations++; },
      trustedActivateButton: async (_id, text) => {
        assert.ok(["Select test option", "Submit local test"].includes(text));
      },
      chrome: {
        windows: {
          create: async ({url}) => {
            assert.equal(url, fixture);
            const tab = { id: nextId++, url: redirected ? "https://www.1point3acres.com/" : url };
            tabs.set(tab.id, tab);
            return { id: 100, tabs: [tab] };
          },
          update: async () => {},
          get: async () => ({ state: "normal" }),
        },
        tabs: {
          query: async () => [{ id: 999 }],
          create: async ({ url }) => {
            assert.equal(url, fixture);
            const tab = { id: nextId++, url: redirected ? "https://www.1point3acres.com/" : url };
            tabs.set(tab.id, tab);
            return tab;
          },
          get: async id => tabs.get(id),
          reload: async id => { assert.ok(tabs.has(id)); },
          update: async id => { assert.ok(tabs.has(id) || id === 999); },
          remove: async id => { removed.push(id); },
        },
        debugger: {
          attach: async () => {},
          detach: async ({tabId}) => { detached.push(tabId); },
          sendCommand: async () => ({ result: { value: { trustedClicks: 1, selected: true } } }),
        },
      },
    });
    vm.runInContext(source, context);
    if (redirected) {
      const report = await context.runInputDiagnostic({ diagnostic_id: "test" });
      assert.equal(report.ok, false);
      assert.match(report.error, /left its local fixture/);
      assert.equal(activations, 0);
    } else {
      const report = await context.runInputDiagnostic({ diagnostic_id: "test" });
      assert.equal(report.forum_actions, 0);
      assert.equal(report.model_calls, 0);
      assert.equal(report.cases.length, 6);
      // A covered button reporting a click is a real failure, not an overall pass.
      assert.equal(report.ok, false);
    }
    assert.deepEqual(removed, [1, 2]);
    assert.equal(detached.length, redirected ? 1 : 7);
  });
}
