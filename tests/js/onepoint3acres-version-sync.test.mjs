import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";

const root = path.resolve(import.meta.dirname, "..", "..");
const source = fs.readFileSync(
  path.join(root, "chrome_extension", "version-sync.js"),
  "utf8",
);
const context = vm.createContext({});
vm.runInContext(source, context);

test("requests one reload for a new version mismatch", () => {
  const command = {
    action: "reload_extension",
    expected_extension_version: "0.4.11",
    protocol_version: 1,
  };
  const decision = context.decideExtensionVersionAction(command, "0.4.10", null);
  assert.equal(decision.blockTasks, true);
  assert.equal(decision.reload, true);
  assert.deepEqual({ ...decision.guard }, {
    fromVersion: "0.4.10",
    toVersion: "0.4.11",
    protocolVersion: 1,
  });
});

test("suppresses a reload loop for the same version pair", () => {
  const command = {
    action: "reload_extension",
    expected_extension_version: "0.4.11",
    protocol_version: 1,
  };
  const guard = { fromVersion: "0.4.10", toVersion: "0.4.11", protocolVersion: 1 };
  const decision = context.decideExtensionVersionAction(command, "0.4.10", guard);
  assert.equal(decision.reload, false);
  assert.equal(decision.blockTasks, true);
});

test("clears the guard only after a compatible command response", () => {
  const guard = { fromVersion: "0.4.10", toVersion: "0.4.11", protocolVersion: 1 };
  const ready = context.decideExtensionVersionAction({ action: "idle" }, "0.4.11", guard);
  assert.equal(ready.blockTasks, false);
  assert.equal(ready.clearGuard, true);

  const incompatible = context.decideExtensionVersionAction(
    { action: "incompatible_extension" },
    "0.4.11",
    guard,
  );
  assert.equal(incompatible.blockTasks, true);
  assert.equal(incompatible.reload, false);
  assert.equal(incompatible.clearGuard, false);
});
