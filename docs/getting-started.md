# Getting started

Use a source checkout and its uv environment. The bridge is independent of LM
Studio; start with text before configuring PDF uploads or collection.

## Register the MCP server

Run the following command from any client that supports local stdio MCP servers:

```text
uv --directory C:/path/to/WebContentAgent run wca-mcp
```

Use an absolute checkout path. If the client cannot find `uv`, use its absolute
executable path. This server exposes both bridge and collection tools.

A client JSON configuration can use this server entry (merge it into the client's
existing configuration, rather than replacing other servers):

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

Codex clients using TOML can express the same command as:

```toml
[mcp_servers.webcontentagent]
command = "uv"
args = ["--directory", "C:/path/to/WebContentAgent", "run", "wca-mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 1200
```

Restart/reconnect the client's MCP session and confirm `ask_chatgpt` and
`read_chatgpt_conversation` are listed. Client configuration locations vary;
use that client's MCP settings. Live Claude Code acceptance is not yet recorded.
Allow a long tool timeout: a ChatGPT reply may exceed a client's default limit.
The Codex fields above follow the [official MCP configuration reference](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## Prepare Chrome

Run `uv run python -m web_content_agent.chatgpt_pairing` once. Open Chrome's
extension management page, enable developer mode, and load this checkout's
`chrome_extension` directory. Pairing is local generated state; do not copy it to
another machine or commit it. Sign in through the normal website yourself.

Ask the client to call `ask_chatgpt` with a short question. For the direct Python
interface, explicitly use `thinking_mode="current"` to preserve the page setting;
its historical default is `prefer_max`. The result includes the conversation URL.
Pass that URL as `conversation_url` for follow-ups. Unlike the optional personal
Node adapter, this interface does not manage named session aliases.

## PDF uploads

Create a private JSON file outside the checkout, for example
`C:/private/wca-pdf-policy.json`:

```json
{
  "allowedPdfRoots": ["C:/Reports/ApprovedForChatGPT"],
  "maximumPdfSizeMb": 20
}
```

Set `CHATGPT_PDF_REVIEW_CONFIG` to that file in the MCP server's environment.
For the JSON example, add an `env` object inside `webcontentagent`. For TOML,
add `[mcp_servers.webcontentagent.env]` with that variable. Without an override,
the existing compatibility path is `~/.codex/chatgpt-pdf-review/config.json`.
The server reads this policy for PDF requests; text-only requests do not need it.
Pass an absolute PDF path under an approved root as `attachment_path`.

## Collect web content

Install LM Studio and load a suitable model locally under `local-agent-vlm`.
The bundled configuration was developed around a 32K multimodal Qwen model;
capabilities and memory requirements must be verified for replacements. Do not
run the legacy model loader unless you want its pinned model and settings.

Through MCP, call `discover_site(url=...)`, then
`create_collection(url=..., name=..., strategy="news", max_items=3)`.
Use the returned configuration path in `start_collection(source_config=...)`.
Call `get_run_status`, `resume_run`, and `validate_run` with the returned run ID.
The default workspace is the user's Desktop/WebContent; `WCA_DESKTOP_PATH`
overrides the Desktop base. Course traversal uses `strategy="course"`; its
`max_items` is an optional limit, not a fixed three-page cap.

No quickstart step runs the optional forum assistant or installs an automation.
Its review endpoint must be driven by a separately authorized supervisor; an
unreviewed model suggestion does not authorize submission.
