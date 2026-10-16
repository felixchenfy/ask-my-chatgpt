import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const readMode = process.argv[2] === "--read";
const pdfPath = process.argv[2];
const reviewPrompt = process.argv[3];
const sessionKey = process.argv[4];
const idempotencyKey = process.argv[5];
const conversationUrl = process.argv[6];
const expectedCharacters = Number(process.argv[7] || 0) || undefined;
const expectedLines = Number(process.argv[8] || 0) || undefined;
const expectedSha256 = process.argv[9] || undefined;
const readExpectedCharacters = Number(process.argv[4] || 0) || undefined;
const readExpectedLines = Number(process.argv[5] || 0) || undefined;
const readExpectedSha256 = process.argv[6] || undefined;
const readExpectedMessageId = process.argv[7] || undefined;

if ((readMode && !reviewPrompt) || (!readMode && (!pdfPath || !reviewPrompt))) {
  throw new Error(
    "Usage: node scripts/live-smoke-test-client.mjs --read <conversation-url> " +
      "[expected-characters] [expected-lines] [expected-sha256] [expected-message-id] | " +
      "<pdf-path> <review-prompt> [session-key] [idempotency-key] [conversation-url] " +
      "[expected-characters] [expected-lines] [expected-sha256]",
  );
}

const transport = new StdioClientTransport({
  command: process.execPath,
  args: [path.join(pluginRoot, "server.mjs")],
  cwd: pluginRoot,
  stderr: "inherit",
});
const client = new Client({ name: "chatgpt-pdf-review-live-smoke-test", version: "0.1.0" });
await client.connect(transport);

function payload(result) {
  if (result.structuredContent) return result.structuredContent;
  const text = result.content?.find((item) => item.type === "text")?.text;
  return text ? JSON.parse(text) : result;
}

const result = readMode
  ? await client.callTool(
      {
        name: "read_chatgpt_conversation",
        arguments: {
          conversation_url: reviewPrompt,
          ...(readExpectedCharacters ? { expected_response_characters: readExpectedCharacters } : {}),
          ...(readExpectedLines ? { expected_response_lines: readExpectedLines } : {}),
          ...(readExpectedSha256 ? { expected_response_sha256: readExpectedSha256 } : {}),
          ...(readExpectedMessageId ? { expected_message_id: readExpectedMessageId } : {}),
          timeout_seconds: 180,
        },
      },
      undefined,
      { timeout: 210_000 },
    )
  : await client.callTool(
      {
        name: "ask_chatgpt",
        arguments: {
          attachment_path: pdfPath,
          question: reviewPrompt,
          ...(sessionKey ? { session_key: sessionKey } : {}),
          ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
          ...(conversationUrl ? { conversation_url: conversationUrl } : {}),
          ...(expectedCharacters ? { expected_response_characters: expectedCharacters } : {}),
          ...(expectedLines ? { expected_response_lines: expectedLines } : {}),
          ...(expectedSha256 ? { expected_response_sha256: expectedSha256 } : {}),
          timeout_seconds: 900,
        },
      },
      undefined,
      { timeout: 930_000 },
    );
process.stdout.write(`${readMode ? "READ_RESULT" : "ASK_RESULT"} ${JSON.stringify(payload(result))}\n`);
if (result.isError) process.exitCode = 1;
await client.close();
