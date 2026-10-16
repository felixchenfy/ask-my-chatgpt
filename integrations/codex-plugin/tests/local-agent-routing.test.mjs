import assert from "node:assert/strict";
import childProcess from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "..");

test("installed MCP route launches the fast source-backed Node adapter", () => {
  const config = JSON.parse(fs.readFileSync(path.join(root, ".mcp.json"), "utf8"));
  const server = config.mcpServers.chatgpt_pdf_review;
  assert.equal(server.command.toLowerCase(), "cmd.exe");
  assert.deepEqual(server.args, [
    "/d",
    "/s",
    "/c",
    "call",
    "scripts\\launch-mcp.cmd",
  ]);
  assert.equal(server.cwd, ".");

  const launcher = fs.readFileSync(path.join(root, "scripts", "launch-mcp.cmd"), "utf8");
  assert.match(launcher, /if not defined CHATGPT_BRIDGE_SOURCE set "CHATGPT_BRIDGE_SOURCE=%~dp0\.\."/);
  assert.doesNotMatch(launcher, /%USERPROFILE%|Desktop/i);
  assert.match(launcher, /node/i);
  assert.match(launcher, /server\.mjs/i);

  const serverSource = fs.readFileSync(path.join(root, "server.mjs"), "utf8");
  const adapter = fs.readFileSync(path.join(root, "scripts", "ask-chatgpt-adapter.py"), "utf8");
  assert.match(serverSource, /registerTool\(\s*["']ask_chatgpt["']/);
  assert.match(serverSource, /registerTool\(\s*["']read_chatgpt_conversation["']/);
  assert.match(serverSource, /thinking_mode/);
  assert.match(serverSource, /ask-chatgpt-adapter\.py/);
  assert.match(fs.readFileSync(path.join(root, "src", "agent-process.mjs"), "utf8"), /PYTHONUTF8/);
  assert.match(adapter, /WCA_PROJECT_ROOT/);
  assert.match(adapter, /ask_chatgpt_data/);
  assert.match(adapter, /read_chatgpt_conversation_data/);
  assert.match(serverSource, /expected_response_sha256/);
  assert.match(adapter, /expected_response_characters/);
  assert.match(adapter, /minimum_response_characters/);
  assert.match(adapter, /sys\.stdin\.reconfigure\(encoding=["']utf-8["']/);
  assert.doesNotMatch(serverSource, /ChatGptBrowserController/);
});

test("skill requires the local ask_chatgpt workflow", () => {
  const skill = fs.readFileSync(
    path.join(root, "skills", "chatgpt-pdf-review", "SKILL.md"),
    "utf8",
  );
  assert.match(skill, /Use the `ask_chatgpt` MCP tool/);
  assert.match(skill, /Do not use Codex browser-control tools/);
  assert.match(skill, /references\/local-agent-workflow\.md/);
  assert.match(skill, /和 GPT 聊一下/);
  assert.match(skill, /ask ChatGPT/i);
  assert.match(skill, /read_chatgpt_conversation/);
  assert.match(skill, /completed_after_read_recovery/);
  assert.match(skill, /automatic_read_recovery_attempted/);
  assert.match(skill, /repair-installed-cache\.ps1/);
  assert.match(skill, /\/c\/WEB:/);
  assert.match(skill, /expected_response_sha256/);
  assert.match(skill, /minimum_response_characters/);
  assert.match(skill, /Codex-only refinement/);
  assert.match(skill, /references\/extension-reconnect\.md/);
  assert.match(skill, /done.*ready.*reloaded/i);

  const reconnect = fs.readFileSync(
    path.join(
      root,
      "skills",
      "chatgpt-pdf-review",
      "references",
      "extension-reconnect.md",
    ),
    "utf8",
  );
  assert.match(reconnect, /status: extension_not_connected/);
  assert.match(reconnect, /message_submitted: false/);
  // Retry decisions are exercised against persisted state by the sibling
  // test_chatgpt_zero_submission_retry.py, not by prescribing a key suffix here.
  assert.match(reconnect, /do not set `force_send: true`/i);
  assert.match(reconnect, /Do not ask the user to copy a diagnostic block/i);
});

test("cache repair restores only missing compatibility entrypoints", () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), "chatgpt-plugin-cache-repair-"));
  const sourceRoot = path.join(tempRoot, "source");
  const cacheRoot = path.join(tempRoot, "cache");
  const emptyVersion = path.join(cacheRoot, "0.1.0+codex.empty");
  const intactVersion = path.join(cacheRoot, "0.1.0+codex.intact");

  fs.mkdirSync(path.join(sourceRoot, "skills"), { recursive: true });
  fs.cpSync(
    path.join(root, "skills", "chatgpt-pdf-review"),
    path.join(sourceRoot, "skills", "chatgpt-pdf-review"),
    { recursive: true },
  );
  fs.mkdirSync(path.join(sourceRoot, "scripts"), { recursive: true });
  fs.mkdirSync(path.join(sourceRoot, "src"), { recursive: true });
  fs.copyFileSync(path.join(root, "src", "agent-process.mjs"), path.join(sourceRoot, "src", "agent-process.mjs"));
  fs.copyFileSync(path.join(root, "src", "agent-config.mjs"), path.join(sourceRoot, "src", "agent-config.mjs"));
  fs.mkdirSync(path.join(sourceRoot, ".codex-plugin"), { recursive: true });
  fs.copyFileSync(path.join(root, ".mcp.json"), path.join(sourceRoot, ".mcp.json"));
  fs.copyFileSync(
    path.join(root, "scripts", "launch-mcp.cmd"),
    path.join(sourceRoot, "scripts", "launch-mcp.cmd"),
  );
  fs.copyFileSync(
    path.join(root, "scripts", "ask-chatgpt-adapter.py"),
    path.join(sourceRoot, "scripts", "ask-chatgpt-adapter.py"),
  );
  fs.copyFileSync(path.join(root, "server.mjs"), path.join(sourceRoot, "server.mjs"));
  fs.copyFileSync(
    path.join(root, ".codex-plugin", "plugin.json"),
    path.join(sourceRoot, ".codex-plugin", "plugin.json"),
  );

  fs.mkdirSync(emptyVersion, { recursive: true });
  fs.mkdirSync(path.join(intactVersion, "skills", "chatgpt-pdf-review"), { recursive: true });
  fs.mkdirSync(path.join(intactVersion, "scripts"), { recursive: true });
  const sentinelSkill = "existing skill must not be overwritten";
  const sentinelLauncher = "existing launcher must not be overwritten";
  fs.writeFileSync(
    path.join(intactVersion, "skills", "chatgpt-pdf-review", "SKILL.md"),
    sentinelSkill,
  );
  fs.writeFileSync(path.join(intactVersion, "scripts", "launch-mcp.cmd"), sentinelLauncher);

  try {
    const completed = childProcess.spawnSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        path.join(root, "scripts", "repair-installed-cache.ps1"),
        "-SourceRoot",
        sourceRoot,
        "-CacheRoot",
        cacheRoot,
      ],
      { encoding: "utf8" },
    );
    assert.equal(completed.status, 0, completed.stderr || completed.stdout);
    assert.ok(fs.existsSync(path.join(emptyVersion, ".mcp.json")));
    assert.ok(fs.existsSync(path.join(emptyVersion, "scripts", "launch-mcp.cmd")));
    assert.ok(fs.existsSync(path.join(emptyVersion, "scripts", "ask-chatgpt-adapter.py")));
    assert.ok(fs.existsSync(path.join(emptyVersion, "server.mjs")));
    assert.ok(fs.existsSync(path.join(emptyVersion, "src", "agent-process.mjs")));
    assert.equal(
      fs.readFileSync(path.join(emptyVersion, "src", "agent-config.mjs"), "utf8"),
      fs.readFileSync(path.join(sourceRoot, "src", "agent-config.mjs"), "utf8"),
    );
    assert.ok(
      fs.existsSync(
        path.join(emptyVersion, "skills", "chatgpt-pdf-review", "references", "local-agent-workflow.md"),
      ),
    );
    assert.equal(
      fs.readFileSync(path.join(emptyVersion, "skills", "chatgpt-pdf-review", "references", "composer-readiness-and-retry.md"), "utf8"),
      fs.readFileSync(path.join(sourceRoot, "skills", "chatgpt-pdf-review", "references", "composer-readiness-and-retry.md"), "utf8"),
    );
    assert.equal(
      fs.readFileSync(path.join(intactVersion, "skills", "chatgpt-pdf-review", "SKILL.md"), "utf8"),
      sentinelSkill,
    );
    assert.equal(
      fs.readFileSync(path.join(intactVersion, "scripts", "launch-mcp.cmd"), "utf8"),
      sentinelLauncher,
    );
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test("project validation wrapper supplies UTF-8 and isolated PyYAML", () => {
  const wrapper = fs.readFileSync(
    path.join(root, "scripts", "validate-plugin.ps1"),
    "utf8",
  );
  const updater = fs.readFileSync(
    path.join(root, "scripts", "update-installed-plugin.ps1"),
    "utf8",
  );
  const readme = fs.readFileSync(path.join(root, "README.md"), "utf8");
  const packageJson = JSON.parse(
    fs.readFileSync(path.join(root, "package.json"), "utf8"),
  );

  assert.match(wrapper, /PYTHONUTF8/);
  assert.match(wrapper, /PYTHONIOENCODING/);
  assert.match(wrapper, /run --with pyyaml python \$skillValidator/);
  assert.match(wrapper, /run --with pyyaml python \$pluginValidator/);
  assert.match(readme, /\\scripts\\validate-plugin\.ps1/);
  assert.match(readme, /ModuleNotFoundError: No module named ['"]yaml['"]/);
  assert.match(readme, /UnicodeDecodeError/);
  assert.match(packageJson.scripts["validate:plugin"], /validate-plugin\.ps1/);
  assert.match(packageJson.scripts["update:plugin"], /update-installed-plugin\.ps1/);
  assert.match(updater, /read_marketplace_name\.py/);
  assert.match(updater, /update_plugin_cachebuster\.py/);
  assert.match(updater, /plugin add/);
  assert.match(updater, /repair-installed-cache\.ps1/);
});
