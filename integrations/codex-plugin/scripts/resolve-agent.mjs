import { fileURLToPath } from "node:url";
import { resolveAgentConfig } from "../src/agent-config.mjs";
console.log(JSON.stringify(resolveAgentConfig(fileURLToPath(new URL("../", import.meta.url)))));
