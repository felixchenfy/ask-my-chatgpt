# Installation and usage

This guide installs the direct Python MCP server. The optional personal Node
adapter is not required. Examples target Windows PowerShell and use placeholder
paths; replace them with your actual checkout and approved upload directory.

## Requirements

| Component | Required for |
| --- | --- |
| Windows, Git and [uv](https://docs.astral.sh/uv/getting-started/installation/) | Source installation; uv supplies Python 3.12 |
| Chrome and a signed-in ChatGPT account | ChatGPT browser bridge |
| Codex, Claude Code or another local stdio MCP client | Calling the bridge tools |
| LM Studio and a compatible local model | Optional web extraction |
| Playwright Chromium | Optional collection and browser integration tests |
| Node.js 22 or later | JavaScript development tests |

No OpenAI API key is needed for the browser bridge. Your ChatGPT account's access
and usage limits still apply. macOS and Linux are not validated release targets.

## Install the checkout

```powershell
git clone https://github.com/felixchenfy/WebContentAgent.git
cd WebContentAgent
uv sync --python 3.12 --frozen
uv run python -m web_content_agent.chatgpt_pairing
```

The repository is currently private, so GitHub access is required. Pairing creates
an ignored local secret and generated extension asset; it does not sign in to
ChatGPT. Do not share these files or copy another person's pairing.

In Chrome, open `chrome://extensions`, enable **Developer mode**, choose **Load
unpacked**, and select this checkout's `chrome_extension` directory. Open ChatGPT
and sign in normally. Keep this checkout in place while the extension uses it.

## Codex

Merge this into `~/.codex/config.toml`, replacing the checkout path. Keep your
other settings and servers. If `uv` is not on the client's PATH, use its absolute
executable path for `command`.

```toml
[mcp_servers.webcontentagent]
command = "uv"
args = ["--directory", "C:/path/to/WebContentAgent", "run", "wca-mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 1200
```

Reconnect the MCP session or restart the client. Confirm that `ask_chatgpt` and
`read_chatgpt_conversation` appear in its tools. CLI users can also run
`codex mcp list`. The long timeout allows the browser workflow to wait for a reply.
Reference: [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## Claude Code

Register the server from PowerShell. User scope makes it available to your coding
projects without committing a personal absolute path into each project's config.

```powershell
claude mcp add --transport stdio --scope user webcontentagent -- uv --directory "C:/path/to/WebContentAgent" run wca-mcp
claude mcp get webcontentagent
```

Open a fresh Claude Code session and use `/mcp` to check the connection. To set
an explicit 20-minute tool budget for the session:

```powershell
$env:MCP_TOOL_TIMEOUT = '1200000'
claude
```

Set that variable in the **Claude Code process environment**, not just the child
MCP server. See [official Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)
for configuration scope and timeout behavior. This is a documented setup path;
live Claude Code acceptance for this project is still pending. A reproducible
setup report is a welcome contribution.

## Other MCP clients

For clients using the `mcpServers` JSON format, merge this entry into their settings:

```json
{
  "mcpServers": {
    "webcontentagent": {
      "command": "uv",
      "args": ["--directory", "C:/path/to/WebContentAgent", "run", "wca-mcp"]
    }
  }
}
```

Use the client's own setting for a sufficiently long tool timeout. Configuration
format and location are client-specific; a generic JSON snippet is not proof of
tested compatibility with every client.

## First successful exchange

Ask your coding agent:

```text
Call ask_chatgpt with question="Reply with BRIDGE_OK." and
thinking_mode="current". Return the reply and conversation URL.
```

The tool should return a completed result, `response_text` containing the answer,
and a canonical `conversation_url`. A connected MCP server or installed extension
alone is not end-to-end acceptance. Do not resend automatically after a timeout.

For a follow-up, pass the returned URL back as `conversation_url`:

```json
{
  "question": "Summarize the project updates in this conversation and list open questions.",
  "conversation_url": "https://chatgpt.com/c/REPLACE_WITH_YOUR_CONVERSATION_ID",
  "thinking_mode": "current"
}
```

This JSON is the argument object for `ask_chatgpt`, not a shell command. Save the
real conversation URL privately. The direct Python interface uses URLs, not the
optional Node adapter's named session aliases. Explicit `current` preserves the
page's mode; the historical Python default is `prefer_max`.

## PDF uploads

Create an approved upload directory and a private JSON policy outside the checkout,
for example `C:/private/wca-pdf-policy.json`:

```json
{
  "allowedPdfRoots": ["C:/Reports/ApprovedForChatGPT"],
  "maximumPdfSizeMb": 20
}
```

For Codex, add the following table to its config:

```toml
[mcp_servers.webcontentagent.env]
CHATGPT_PDF_REVIEW_CONFIG = "C:/private/wca-pdf-policy.json"
```

For Claude Code, when registering the server, include this environment option:

```powershell
claude mcp add --env "CHATGPT_PDF_REVIEW_CONFIG=C:/private/wca-pdf-policy.json" --transport stdio --scope user webcontentagent -- uv --directory "C:/path/to/WebContentAgent" run wca-mcp
```

If that server is already registered, update its existing environment entry in
your user configuration instead of registering a duplicate. Other JSON clients
can add `"env": {"CHATGPT_PDF_REVIEW_CONFIG": "C:/private/wca-pdf-policy.json"}`
inside the server entry. Reconnect after configuration changes.

Now call `ask_chatgpt` with an absolute `attachment_path` under an approved root.
The server checks the real path, PDF type and size. Without an override, the
compatibility policy path is `~/.codex/chatgpt-pdf-review/config.json`. Text-only
requests do not need a PDF policy.

## Optional web collection

```powershell
uv run playwright install chromium
```

Install LM Studio and load a compatible local model under `local-agent-vlm`.
The bundled defaults were developed around a 32K multimodal Qwen model. Verify
memory requirements and capabilities for replacements. Legacy desktop/model
launchers can alter model preferences; they are not part of the bridge quickstart.

Through MCP, call `discover_site(url=...)`, then
`create_collection(url=..., name=..., strategy="news", max_items=3)`.
Pass the returned path to `start_collection(source_config=...)`; use the returned
run ID with `get_run_status`, `resume_run` and `validate_run`.

The workspace defaults to Desktop/WebContent. `WCA_DESKTOP_PATH` overrides its
Desktop base. Use `strategy="course"` for in-scope course traversal; `max_items`
is an optional cap. Raw evidence and cleaned output are stored separately.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| MCP tools are missing | Absolute paths, client PATH, `uv sync`, client reconnect and MCP startup logs |
| Extension is disconnected | Correct unpacked-extension folder, local pairing preparation and enabled extension |
| PDF policy/file rejected | The server's policy environment, allowed real directory, file type and size |
| Bridge is busy | Another client may own the listener; serialize work and let that owner finish |
| Existing draft or attachment blocks sending | Inspect it yourself; do not let a retry erase unrelated work |
| Confirmation is missing or delivery is uncertain | Inspect the private task record; do not infer zero delivery or resend blindly |
| Login, verification or usage limit | Resolve through the normal account UI; do not bypass it |

An eligible failed extraction may already have used automatic read-only recovery.
Check the result before requesting another read. `read_chatgpt_conversation`
retrieves an existing reply; it does not ask ChatGPT to generate a new summary.

Keep one bridge-owning client active at a time. The direct Python MCP process can
retain its listener after a call; do not terminate an active request to free it.
Report sanitized reproductions using the [bug form](../.github/ISSUE_TEMPLATE/bug_report.yml).
