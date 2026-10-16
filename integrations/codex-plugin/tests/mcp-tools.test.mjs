import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

test("MCP server publishes WebContentAgent send and read-only recovery tools", async (t) => {
  const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [path.join(pluginRoot, "server.mjs")],
    cwd: pluginRoot,
    stderr: "pipe",
  });
  const client = new Client({ name: "chatgpt-pdf-review-test", version: "0.1.0" });
  t.after(async () => {
    await client.close();
  });
  await client.connect(transport);
  const { tools } = await client.listTools();
  assert.deepEqual(
    tools.map((tool) => tool.name).sort(),
    ["ask_chatgpt", "read_chatgpt_conversation"],
  );
  for (const tool of tools) {
    assert.ok(tool.description);
    assert.equal(tool.inputSchema.type, "object");
  }
  const askTool = tools.find((tool) => tool.name === "ask_chatgpt");
  assert.deepEqual(askTool.inputSchema.properties.thinking_mode.enum, ["current", "prefer_max", "max"]);
  assert.equal(askTool.inputSchema.properties.thinking_mode.default, "current");
  assert.ok(askTool.inputSchema.properties.session_key);
  assert.ok(askTool.inputSchema.properties.idempotency_key);
  assert.equal(askTool.inputSchema.properties.force_send.default, false);
  assert.ok(askTool.inputSchema.properties.minimum_response_characters);
  assert.ok(askTool.inputSchema.properties.expected_response_characters);
  assert.ok(askTool.inputSchema.properties.expected_response_lines);
  assert.ok(askTool.inputSchema.properties.expected_response_sha256);
  const readTool = tools.find((tool) => tool.name === "read_chatgpt_conversation");
  assert.equal(readTool.annotations.readOnlyHint, true);
  assert.equal(readTool.annotations.destructiveHint, false);
  assert.ok(readTool.inputSchema.properties.minimum_response_characters);
  assert.ok(readTool.inputSchema.properties.expected_response_sha256);
});
