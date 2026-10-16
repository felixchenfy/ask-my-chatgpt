import { spawn } from "node:child_process";
import { clearTimeout, setTimeout } from "node:timers";

// Keep stdin open for cooperative cancellation. Python revokes the submit permit
// before acknowledging cancellation; killing a process alone is not a safe cancel.
export function callAgent(input, { executable, args, cwd, signal, spawnProcess = spawn,
  cancelGraceMs = 5000, environment = {} } = {}) {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const child = spawnProcess(executable, args, {
      cwd, env: { ...process.env, ...environment, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
      windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let timer;
    const cancel = () => {
      if (!child.stdin.destroyed) child.stdin.write(`${JSON.stringify({ action: "cancel" })}\n`);
      timer ??= setTimeout(() => child.kill(), cancelGraceMs);
      timer.unref();
    };
    const cleanup = () => { clearTimeout(timer); signal?.removeEventListener("abort", cancel); };
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.stderr.on("data", chunk => { stderr += chunk; });
    child.stdin.on("error", () => {}); // Cancellation can race a normal child exit.
    child.on("error", error => { cleanup(); reject(error); });
    child.on("close", code => {
      cleanup();
      if (signal?.aborted) { reject(signal.reason || new Error("Request cancelled")); return; }
      if (code !== 0) { reject(new Error(stderr.trim() || `WebContentAgent adapter exited with code ${code}`)); return; }
      try { resolve(JSON.parse(stdout)); }
      catch (error) { reject(new Error(`WebContentAgent adapter returned invalid JSON: ${error.message}`)); }
    });
    child.stdin.write(`${JSON.stringify({ ...input, _cancel_protocol: 1 })}\n`);
    signal?.addEventListener("abort", cancel, { once: true });
    if (signal?.aborted) cancel();
  });
}
