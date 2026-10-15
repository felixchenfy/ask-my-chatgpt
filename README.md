# WebContentAgent

Connect coding agents such as **Codex and Claude Code to ChatGPT through your
signed-in Chrome browser**. Send a question or PDF, retrieve the corresponding
reply, and continue the same conversation through MCP (Model Context Protocol).

The project also provides local-model web extraction and supervised browser
workflows. This is a **Windows-first developer preview**. Codex has historical
live-use evidence; Claude Code can use the standard Python MCP interface, but
client-specific live acceptance is still pending.

## What it does

| Capability | Result |
| --- | --- |
| ChatGPT bridge | Questions, optional PDF attachments, conversation continuation and verified reply extraction |
| Recovery | Durable submission records, duplicate-send protection and bounded read-only recovery |
| Web collection | News/article selection, in-scope course traversal, images and cleaned Markdown |
| Local inference | LM Studio handles semantic decisions; Python owns traversal, storage and validation |
| Durable jobs | Checkpoints, resume, raw evidence, manifests and failure diagnostics |
| Optional daily assistant | Signed-in Chrome actions with an explicit review and one-use submission permit |

## Start here

Requirements: Windows, Python 3.12 (managed by uv), uv, and Chrome for the bridge.
Node.js is needed for JavaScript tests. LM Studio is needed only for local-model
collection and the optional model-assisted workflow, not for the ChatGPT bridge.

```powershell
git clone https://github.com/felixchenfy/WebContentAgent.git
cd WebContentAgent
uv sync --python 3.12 --frozen
uv run playwright install chromium
uv run python -m web_content_agent.chatgpt_pairing
```

Load `chrome_extension/` as an unpacked extension in Chrome, sign in to ChatGPT
yourself, then register the Python MCP server in your coding client. Follow
[Getting started](docs/getting-started.md) for the MCP command, PDF allowlist,
and collection example. The repository is initially private; cloning requires
access. No separate Node adapter repository is required for the direct Python
MCP route. Existing personal plugin installations may continue using their adapter.

## How it works

```text
Codex / Claude Code -> Python MCP -> local bridge -> Chrome Companion -> ChatGPT
                                      |
                               private task records

CLI / MCP -> collection worker -> Playwright + local LM Studio
                     |
              raw / cleaned / checkpoints
```

The bridge uses the signed-in ChatGPT website, not an OpenAI API key. Questions
and PDFs sent through it leave the machine and go to ChatGPT. Local-model
collection sends inference requests only to localhost. These are separate paths.

## Reliability and scope

A failed confirmation does not prove that a message was never sent. Inspect the
durable state and recover the existing reply; do not resend an ambiguous request.
Verified extraction checks message ownership and completion, not factual accuracy.
Website changes can still require maintenance. Historical PDF trials are not an
exactly-once delivery guarantee or a current-version benchmark.

The extension requests `debugger`, `scripting`, `storage`, `tabs`, and `alarms`
permissions, with hosts limited to ChatGPT, the optional forum/search workflow,
and two loopback endpoints. See [privacy and permissions](docs/privacy.md).

## Development

```powershell
# Offline checks; no ChatGPT submission or LM Studio model loading
$env:PYTEST_ADDOPTS = '-m "not live and not lmstudio"'
.\scripts\test.ps1
```

See [CONTRIBUTING](CONTRIBUTING.md), [design](docs/design.md), and
[release notes](docs/release-notes.md). Legacy model/desktop launchers remain
advanced opt-in tools: some adjust LM Studio preferences and load a pinned model.
The quickstart does not run them or install a schedule.

## Articles

- [Codex-to-ChatGPT bridge](https://feiyuchen-blog.pages.dev/blog/codex-talks-to-chatgpt-through-a-local-agent/)
- [Local web extraction](https://feiyuchen-blog.pages.dev/blog/local-llm-web-agent/)
- [Supervised daily browser tasks](https://feiyuchen-blog.pages.dev/blog/signed-in-chrome-daily-assistant/)

MIT licensed. Model weights, downloaded content and third-party services are not
included and retain their own terms.
