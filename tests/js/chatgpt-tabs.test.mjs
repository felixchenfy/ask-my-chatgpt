import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const source = fs.readFileSync(path.join(root, "chrome_extension", "chatgpt-tabs.js"), "utf8");
const context = { self: {}, URL };
vm.runInNewContext(source, context);
const tabs = context.self.ChatGptTabs;

test("project conversation aliases keep the same canonical identity at every guard", () => {
  const canonical = "https://chatgpt.com/c/expected";
  const project = "https://chatgpt.com/g/g-p-example/c/expected";
  for (const [expected, actual] of [[canonical, project], [project, canonical], [project, project]]) {
    const state = tabs.destinationState(expected, actual);
    assert.equal(state.ok, true);
    assert.equal(state.canonical, true);
    assert.equal(state.url, canonical);
    assert.equal(tabs.sameDestination(expected, actual), true);
  }
  assert.equal(tabs.chooseReusableTab([{ id: 9, url: project }], canonical)?.id, 9);
  // The inline trusted-input guard is serialized without module globals.
  const inline = vm.runInNewContext(`(${tabs.destinationState.toString()})`, { URL });
  assert.equal(inline(canonical, project).ok, true);
});

test("project alias support does not admit other chats or arbitrary GPT pages", () => {
  const expected = "https://chatgpt.com/c/expected";
  for (const actual of [
    "https://chatgpt.com/g/g-p-example/c/other",
    "https://chatgpt.com/g/g-p-example",
    "https://chatgpt.com/g/g-custom/c/expected",
    "https://chatgpt.com/g/g-p-example/c/expected/extra",
    "https://chatgpt.com/g/g-p-example/c/WEB:expected",
    "https://chatgpt.com/g/g-p-example/c/expected?temporary-chat=true",
    "https://name@chatgpt.com/g/g-p-example/c/expected",
    "https://example.com/g/g-p-example/c/expected",
  ]) {
    assert.equal(tabs.destinationState(expected, actual, { allowNew: true, allowProvisional: true }).ok, false, actual);
    assert.equal(tabs.sameDestination(expected, actual), false, actual);
  }
});

test("reuses an already-open exact conversation tab", () => {
  const chosen = tabs.chooseReusableTab([
    { id: 10, url: "https://chatgpt.com/c/other", active: true },
    { id: 11, url: "https://chatgpt.com/c/expected", active: false },
  ], "https://chatgpt.com/c/expected");
  assert.equal(chosen.id, 11);
});

test("encoded local new-chat routes are provisional and cannot release an existing identity", () => {
  const home = "https://chatgpt.com/";
  const local = "https://chatgpt.com/c/local-chatgpt%3A00000000-0000-4000-8000-000000000001";
  const canonical = "https://chatgpt.com/c/resolved";
  assert.equal(tabs.destinationState(home, local).ok, false);
  const pending = tabs.destinationState(home, local, { allowNew: true });
  assert.equal(pending.ok, true); assert.equal(pending.canonical, false); assert.equal(pending.provisional, true);
  assert.equal(tabs.sameDestination(local, local.replace("%3A", ":")), true);
  assert.equal(tabs.destinationState(pending.url, canonical).ok, false);
  assert.equal(tabs.destinationState(pending.url, canonical, { allowProvisional: true }).ok, true);
  for (const actual of [local, home, "https://chatgpt.com/c/other"]) {
    assert.equal(tabs.destinationState(canonical, actual, { allowNew: true, allowProvisional: true }).ok, false);
  }
  for (const wrong of [local.replace("000000000001", "000000000002"), local.replace("%3A", "%253A"),
    local + "/extra", local.replace("local-chatgpt", "other-prefix"), local + "?temporary-chat=true"]) {
    assert.equal(tabs.destinationState(local, wrong, { allowProvisional: true }).ok, false);
  }
});

test("prefers the managed tab when duplicate exact tabs are open", () => {
  const chosen = tabs.chooseReusableTab([
    { id: 10, url: "https://chatgpt.com/c/expected", active: true },
    { id: 11, url: "https://chatgpt.com/c/expected", active: false },
  ], "https://chatgpt.com/c/expected", 11);
  assert.equal(chosen.id, 11);
});

test("does not reuse another conversation or a temporary chat", () => {
  assert.equal(tabs.chooseReusableTab([
    { id: 10, url: "https://chatgpt.com/c/other", active: true },
    { id: 11, url: "https://chatgpt.com/?temporary-chat=true", active: true },
  ], "https://chatgpt.com/c/expected"), null);
});

test("canonical destinations never drift to home, another chat, login or temporary chat", () => {
  const expected = "https://chatgpt.com/c/expected";
  for (const actual of ["https://chatgpt.com/", "https://chatgpt.com/c/other",
    "https://chatgpt.com/auth/login", "https://chatgpt.com/c/expected?temporary-chat=true",
    "https://example.com/c/expected", "https://name@chatgpt.com/c/expected", ""]) {
    assert.equal(tabs.destinationState(expected, actual, { allowProvisional: true, allowNew: true }).ok, false, actual);
  }
  assert.equal(tabs.destinationState(expected, expected + "/#main").ok, true);
});

test("canonical conversation redirected home has a bounded unavailable-destination diagnosis", () => {
  const state = tabs.destinationState(
    "https://chatgpt.com/c/expected",
    "https://chatgpt.com/",
  );
  assert.equal(state.ok, false);
  assert.equal(state.reasonCode, "canonical_redirected_home");
  assert.equal(state.expected, "https://chatgpt.com/c/expected");
  assert.equal(state.observed, "https://chatgpt.com/");
  assert.match(state.error, /unavailable to the current Chrome account/i);
});

test("new chats and provisional WEB routes have explicit one-time transitions", () => {
  const canonical = "https://chatgpt.com/c/resolved";
  assert.equal(tabs.destinationState("https://chatgpt.com/", canonical).ok, false);
  assert.equal(tabs.destinationState("https://chatgpt.com/", canonical, { allowNew: true }).ok, true);
  assert.equal(tabs.destinationState("https://chatgpt.com/c/WEB:123", canonical).ok, false);
  const resolved = tabs.destinationState("https://chatgpt.com/c/WEB:123", canonical, { allowProvisional: true });
  assert.equal(resolved.ok, true);
  assert.equal(tabs.destinationState(resolved.url, "https://chatgpt.com/c/other", { allowProvisional: true }).ok, false);
  assert.equal(tabs.destinationState("https://chatgpt.com/c/WEB:123", "https://chatgpt.com/", { allowProvisional: true }).ok, false);
});

test("new submission follows home -> WEB -> canonical, then pins the canonical identity", () => {
  const home = "https://chatgpt.com/";
  const web = "https://chatgpt.com/c/WEB:00c508cd-5561-4478-9a09-b339329f5946";
  const canonical = "https://chatgpt.com/c/resolved";
  assert.equal(tabs.destinationState(home, web).ok, false); // No transition before submission.
  const pending = tabs.destinationState(home, web, { allowNew: true });
  assert.equal(pending.ok, true);
  assert.equal(pending.provisional, true);
  assert.equal(pending.canonical, false);
  assert.equal(tabs.destinationState(web, web, { allowProvisional: true }).ok, true);
  assert.equal(tabs.destinationState(web, canonical).ok, false);
  const resolved = tabs.destinationState(web, canonical, { allowProvisional: true });
  assert.equal(resolved.canonical, true);
  for (const wrong of [home, "https://chatgpt.com/c/other", web]) {
    assert.equal(tabs.destinationState(resolved.url, wrong, { allowNew: true, allowProvisional: true }).ok, false);
  }
  assert.equal(tabs.destinationState(web, "https://chatgpt.com/c/WEB:other", { allowProvisional: true }).ok, false);
});
