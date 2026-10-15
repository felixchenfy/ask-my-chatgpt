# Developer preview — 2026-10-15

This release packages the existing Python worker, MCP server and Chrome Companion
with a standalone setup guide. It starts with a sanitized root commit and is
initially hosted privately. Personal logs, captures, environment instructions and
historical incident journals are excluded from Git while remaining local.

Recent bridge maintenance handles narrowly observed page changes: raw pre-wrap
message bodies, upload-progress placeholders, collapsed long prompts, and
language-less code fences. Windows bridge processes acquire exclusive port
ownership before creating tasks. These repairs retain identity and duplicate-send
guards; they do not guarantee compatibility with every future page layout.

Historical Codex PDF experiments remain dated evidence. No live ChatGPT request,
Claude Code acceptance, model load or scheduled task is part of release checking.
See the GitHub checks for offline validation of this snapshot.
