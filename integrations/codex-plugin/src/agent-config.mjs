import fs from "node:fs";
import path from "node:path";

const BRIDGE_MODULE = path.join("src", "web_content_agent", "chatgpt_chrome_bridge.py");

function isWebContentAgentRoot(candidate) {
  return fs.existsSync(path.join(candidate, BRIDGE_MODULE));
}

// WCA_PROJECT_ROOT always wins. Otherwise prefer the repository that contains this
// plugin (integrations/codex-plugin -> ../..), then a sibling WebContentAgent checkout.
export function defaultProjectRoot(pluginRoot) {
  const candidates = [
    path.resolve(pluginRoot, "..", ".."),
    path.resolve(pluginRoot, "..", "WebContentAgent"),
  ];
  return candidates.find(isWebContentAgentRoot) ?? candidates[0];
}

export function resolveAgentConfig(pluginRoot, environment = process.env) {
  const root = path.resolve(environment.WCA_PROJECT_ROOT || defaultProjectRoot(pluginRoot));
  const executable = environment.WCA_PYTHON_EXECUTABLE
    ? path.resolve(environment.WCA_PYTHON_EXECUTABLE)
    : path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  if (!isWebContentAgentRoot(root) || !fs.existsSync(executable)) {
    throw new Error("WebContentAgent source or Python environment is missing. Set WCA_PROJECT_ROOT and, if needed, WCA_PYTHON_EXECUTABLE.");
  }
  return { executable, cwd: root, environment: { WCA_PROJECT_ROOT: root } };
}
