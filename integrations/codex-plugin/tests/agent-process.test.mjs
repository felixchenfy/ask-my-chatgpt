import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test from "node:test";
import { callAgent } from "../src/agent-process.mjs";

test("real child round-trips Unicode JSON over the adapter protocol", async () => {
  const result = await callAgent({ question: "中文😀" }, {
    executable: process.execPath, args: ["-e", `
      require('node:readline').createInterface({input:process.stdin}).once('line', line => {
        process.stdout.write(JSON.stringify(JSON.parse(line))); process.exit(0);
      });`], cwd: process.cwd(),
  });
  assert.equal(result.question, "中文😀");
  assert.equal(result._cancel_protocol, 1);
});

test("cancel reaches a real child before any simulated submission", async () => {
  const controller = new globalThis.AbortController();
  let output = "";
  const pending = callAgent({ question: "cancel me" }, {
    executable: process.execPath, args: ["-e", `
      require('node:readline').createInterface({input:process.stdin}).on('line', line => {
        const msg=JSON.parse(line);
        if(msg.action==='cancel') {
          process.stdout.write(JSON.stringify({cancelled:true, submitted:false})); process.exit(0);
        } else process.stderr.write('READY');
      });`], cwd: process.cwd(), signal: controller.signal,
    spawnProcess(...args) {
      const child = spawn(...args);
      child.stdout.on("data", chunk => { output += chunk; });
      child.stderr.once("data", () => controller.abort(new Error("test cancelled")));
      return child;
    },
  });
  await assert.rejects(pending, /test cancelled/);
  assert.deepEqual(JSON.parse(output), { cancelled: true, submitted: false });
});

test("already cancelled requests do not create a child", () => {
  assert.throws(() => callAgent({}, {
    signal: globalThis.AbortSignal.abort(new Error("already cancelled")),
    spawnProcess() { assert.fail("must not spawn"); },
  }), /already cancelled/);
});
