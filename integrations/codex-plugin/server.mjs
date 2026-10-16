import { callAgent } from "./src/agent-process.mjs";
import { resolveAgentConfig } from "./src/agent-config.mjs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const pluginRoot = path.dirname(fileURLToPath(import.meta.url));
const adapterPath = path.join(pluginRoot, "scripts", "ask-chatgpt-adapter.py");
const server = new McpServer({ name: "chatgpt-pdf-review", version: "0.1.0" });

function callWebContentAgent(input, signal) {
  return callAgent(input, { ...resolveAgentConfig(pluginRoot), args: [adapterPath], signal });
}

server.registerTool(
  "ask_chatgpt",
  {
    title: "Ask signed-in ChatGPT",
    description: "Send one question and optional allowed PDF through WebContentAgent's signed-in Chrome companion, wait for the completed answer, and return the canonical conversation URL.",
    inputSchema: {
      question: z.string().min(1).describe("Exact question to send once."),
      conversation_url: z.string().url().optional().describe("https://chatgpt.com/c/... conversation URL or its /g/g-p-.../c/... project alias. Omit for a new conversation."),
      session_key: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/).optional().describe("Stable key that reuses one ChatGPT conversation and managed tab across calls."),
      idempotency_key: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/).optional().describe("Stable operation key used to block duplicate submissions."),
      new_conversation: z.boolean().default(false).describe("Start a new conversation even when session_key already has one. Cannot be combined with conversation_url."),
      force_send: z.boolean().default(false).describe("Bypass duplicate and cooldown safeguards only when the user explicitly requested a resend."),
      attachment_path: z.string().optional().describe("Optional absolute path to an allowed local PDF."),
      minimum_response_characters: z.number().int().min(1).max(1_000_000).optional().describe("Minimum non-newline character count required before an open-ended response can be verified."),
      expected_response_characters: z.number().int().min(1).max(1_000_000).optional().describe("Exact expected non-newline character count for deterministic extraction."),
      expected_response_lines: z.number().int().min(1).max(100_000).optional().describe("Exact expected normalized line count for deterministic extraction."),
      expected_response_sha256: z.string().regex(/^[0-9a-fA-F]{64}$/).optional().describe("SHA-256 of the expected normalized UTF-8 response text."),
      timeout_seconds: z.number().int().min(1).max(3600).default(900),
      thinking_mode: z.enum(["current", "prefer_max", "max"]).default("current").describe("Preserve the current setting by default, prefer Max without blocking, or strictly require Max."),
    },
    annotations: { readOnlyHint: false, destructiveHint: true, openWorldHint: true },
  },
  async (input, { signal }) => {
    try {
      const value = await callWebContentAgent(input, signal);
      return {
        content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
        structuredContent: value,
        isError: value.ok === false,
      };
    } catch (error) {
      const value = { error: "webcontentagent_bridge_failed", message: error.message };
      return {
        content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
        structuredContent: value,
        isError: true,
      };
    }
  },
);

server.registerTool(
  "read_chatgpt_conversation",
  {
    title: "Read an existing ChatGPT conversation",
    description: "Read the latest assistant response from a canonical ChatGPT conversation URL without typing, uploading, or sending another message.",
    inputSchema: {
      conversation_url: z.string().url().describe("Canonical https://chatgpt.com/c/... URL returned by ask_chatgpt."),
      expected_message_id: z.string().min(1).max(256).optional().describe("Submitted message ID required when recovering a particular send, not an arbitrary latest reply."),
      minimum_response_characters: z.number().int().min(1).max(1_000_000).optional().describe("Minimum non-newline character count required before the response can be verified."),
      expected_response_characters: z.number().int().min(1).max(1_000_000).optional().describe("Exact expected non-newline character count."),
      expected_response_lines: z.number().int().min(1).max(100_000).optional().describe("Exact expected normalized line count."),
      expected_response_sha256: z.string().regex(/^[0-9a-fA-F]{64}$/).optional().describe("SHA-256 of the expected normalized UTF-8 response text."),
      timeout_seconds: z.number().int().min(1).max(600).default(120),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
  },
  async (input, { signal }) => {
    try {
      const value = await callWebContentAgent({
        action: "read_chatgpt_conversation",
        ...input,
      }, signal);
      return {
        content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
        structuredContent: value,
        isError: value.ok === false,
      };
    } catch (error) {
      const value = { error: "webcontentagent_bridge_failed", message: error.message };
      return {
        content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
        structuredContent: value,
        isError: true,
      };
    }
  },
);

await server.connect(new StdioServerTransport());
