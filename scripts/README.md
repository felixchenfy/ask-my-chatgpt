# Scripts

Run these from the repository root in PowerShell. Most are thin wrappers around
`uv run wca …` or `uv run python …`.

## Setup and checks

| Script | Purpose |
| --- | --- |
| `setup.ps1` | Install dependencies and the Playwright browser |
| `test.ps1` | Offline checks: ruff, pytest and the Chrome Companion JS tests |
| `doctor.ps1` | Check the local environment |
| `mcp-doctor.ps1` | Check the MCP server |
| `start-mcp.ps1` | Start the MCP server (`wca-mcp`) by hand |

## ChatGPT bridge

| Script | Purpose |
| --- | --- |
| `chatgpt-live-check.py` | Opt-in live check over real MCP stdio; **sends** two synthetic messages, needs `--confirm-send` |
| `check-chatgpt-companion.py` | Check the Companion's version without sending or reading anything |
| `inspect-chatgpt-composer.py` | Inspect the composer of an existing failed request; never sends |
| `correct-unsent-chatgpt-mode.py` | Fix a thinking-mode setting on a request that was not sent; never contacts Chrome |

## Web-content collector

| Script | Purpose |
| --- | --- |
| `login.ps1`, `run.ps1`, `resume.ps1`, `monitor-run.ps1` | Wrappers for the `wca` collector commands |
| `run-demo.ps1` | Doctor, model load and a demo run in one go |

## Local model (LM Studio, Bionic, OpenCode)

| Script | Purpose |
| --- | --- |
| `load-model.ps1`, `model-settings.ps1` | Load the pinned local model with the expected settings |
| `configure-lmstudio-mcp.ps1` | Register the MCP server with LM Studio / Bionic |
| `start-ui-agent.ps1` (`Start-WebContentAgent.cmd`) | Start Bionic with the local agent |
| `start-local-agent-vscode.ps1`, `start-opencode-vscode.ps1` (and their `.cmd` launchers) | Start VS Code / OpenCode with the local model |
| `test-model-tools.ps1`, `test_model_tools.py` | Check that the local model calls the MCP tools |

## Extra: 1point3acres assistant

Optional and separate from the ChatGPT bridge; see
[`src/web_content_agent/onepoint3acres/README.md`](../src/web_content_agent/onepoint3acres/README.md).
None of these run unless you start them.

| Script | Purpose |
| --- | --- |
| `start-onepoint3acres-app.ps1`, `install-onepoint3acres-shortcut.ps1` | Start the desktop app, or add a desktop shortcut for it |
| `codex-daily-review.py` | Local client the supervising Codex run uses to review and decide; no LLM API calls |
| `simulate-quiz-app.py` | Replay a saved quiz through the running app; no website actions |
| `diagnose-chrome-input.py` | Test the Companion's input on an inert local page; no quiz or model requests |
| `compare-quiz-models.py`, `replay-quiz-model.py`, `verify-quiz-reasoning.py`, `verify-selected-quiz-model.py` | Offline model experiments and checks; no website actions |
