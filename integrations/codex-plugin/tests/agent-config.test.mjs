import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { defaultProjectRoot, resolveAgentConfig } from "../src/agent-config.mjs";

test("relocated sibling source and explicit Python override resolve without a username", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "bridge-config-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const root = path.join(directory, "WebContentAgent");
  fs.mkdirSync(path.join(root, "src", "web_content_agent"), { recursive: true });
  fs.writeFileSync(path.join(root, "src", "web_content_agent", "chatgpt_chrome_bridge.py"), "");
  const executable = path.join(directory, "test-python"); fs.writeFileSync(executable, "");
  const sibling = resolveAgentConfig(path.join(directory, "plugin"), { WCA_PYTHON_EXECUTABLE: executable });
  assert.equal(sibling.cwd, root); assert.equal(sibling.executable, executable);
  const override = resolveAgentConfig("unrelated", { WCA_PROJECT_ROOT: root, WCA_PYTHON_EXECUTABLE: executable });
  assert.equal(override.environment.WCA_PROJECT_ROOT, root);
  assert.throws(() => resolveAgentConfig(directory, {}), /missing/);
});

test("plugin inside a WebContentAgent checkout resolves the enclosing repository first", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "bridge-config-repo-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const repository = path.join(directory, "repo");
  const pluginRoot = path.join(repository, "integrations", "codex-plugin");
  for (const root of [repository, path.join(repository, "integrations", "WebContentAgent")]) {
    fs.mkdirSync(path.join(root, "src", "web_content_agent"), { recursive: true });
    fs.writeFileSync(path.join(root, "src", "web_content_agent", "chatgpt_chrome_bridge.py"), "");
  }
  fs.mkdirSync(pluginRoot, { recursive: true });
  const executable = path.join(directory, "test-python"); fs.writeFileSync(executable, "");
  const enclosing = resolveAgentConfig(pluginRoot, { WCA_PYTHON_EXECUTABLE: executable });
  assert.equal(enclosing.cwd, repository);
  assert.equal(enclosing.environment.WCA_PROJECT_ROOT, repository);
  const elsewhere = path.join(directory, "elsewhere");
  fs.mkdirSync(path.join(elsewhere, "src", "web_content_agent"), { recursive: true });
  fs.writeFileSync(path.join(elsewhere, "src", "web_content_agent", "chatgpt_chrome_bridge.py"), "");
  const override = resolveAgentConfig(pluginRoot, { WCA_PROJECT_ROOT: elsewhere, WCA_PYTHON_EXECUTABLE: executable });
  assert.equal(override.cwd, elsewhere);
});

test("this checkout's plugin resolves its own repository root", () => {
  const pluginRoot = path.resolve(import.meta.dirname, "..");
  assert.equal(defaultProjectRoot(pluginRoot), path.resolve(pluginRoot, "..", ".."));
});
