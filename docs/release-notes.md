# Recovery fix — 2026-10-16

- **`duplicate_blocked` now carries the original message id.** If a caller is
  interrupted after its message was submitted and re-runs with the same key, the
  blocked result includes the original run's `submitted_message_id`,
  `conversation_url` and `message_submitted`, so the single bound read
  (`read_chatgpt_conversation` with `expected_message_id`) needs no digging in task
  records. Nothing is submitted again; protection is unchanged.

# Public preparation — 2026-10-15

- **Security:** the optional 1point3acres desktop app no longer uses a fixed
  token from the source code, and its loopback server no longer answers
  cross-origin requests from web pages (`Access-Control-Allow-Origin: *` was
  removed). Each machine now gets a random token in `runtime/state/quiz-pairing.json`;
  an old fixed token is replaced automatically. Reload the extension once after updating.
- **Duplicate-send protection on macOS/Linux:** the shared task-state lock now uses
  `fcntl.flock` outside Windows, so two MCP processes cannot both reserve the same
  request. On Windows the `msvcrt` lock is unchanged; it no longer appends a byte
  to the lock file on every acquisition.
- **Tests:** new `tests/unit/test_api_contracts.py` covers every MCP tool, the
  CLI, and both loopback HTTP APIs (auth, CORS, routes). Windows-only tests are
  skipped on other platforms; one racy diagnostic assertion now waits correctly.
- **Live check:** `scripts/chatgpt-live-check.py --confirm-send` drives the real
  MCP stdio server through a new chat, a follow-up in the same chat and a
  read-only read. Pre-publication run on Windows, 2026-10-15: passed. The new
  chat completed in 51.2 s and the same-chat follow-up in 35.7 s; the read-only
  read completed in 7.5 s and submitted nothing.
- **Docs:** README reorganized from user features to developer reference;
  new [testing guide](testing.md); design notes for the desktop app and local
  authentication.

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
