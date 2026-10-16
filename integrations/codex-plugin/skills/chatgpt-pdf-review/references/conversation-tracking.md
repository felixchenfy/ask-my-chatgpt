# Conversation tracking

Use `%USERPROFILE%\.codex\chatgpt-pdf-review\conversations.json` as a small cross-task registry. The durable identity is the human-readable session key plus its canonical ChatGPT conversation URL, not a tab ID, MCP process, or transient prepared-review ID. Use the registry alias as `session_key` when it matches the required character policy; otherwise derive a stable slug.

The file contains a top-level `conversations` object keyed by a short, human-readable alias:

```json
{
  "version": 1,
  "conversations": {
    "report-review": {
      "url": "https://chatgpt.com/c/example",
      "title": "Report review",
      "last_pdf": "report-v2.pdf",
      "last_status": "completed",
      "updated_at": "2026-08-22T18:00:00Z"
    }
  }
}
```

Preserve existing entries and unknown fields. Use readable aliases; if the user does not provide one, derive a concise alias from the PDF or ChatGPT title and report it. Store no cookies, tokens, browser profile data, prompt bodies, or response bodies in this registry.

- After a new send creates its canonical `/c/...` URL, create or update the alias.
- Treat a supplied `/c/WEB:...` URL as a provisional navigation route only. Never save it; wait for the bridge's stable canonical `/c/...` result.
- A complete `/g/g-p-<project-id>/c/<conversation-id>` project route refers to the same conversation as `/c/<conversation-id>`. Save the normalized `/c/...` URL returned by the bridge; never equate different conversation IDs or infer a conversation from a project landing page.
- After a response check, update `last_status` and `updated_at`.
- After automatic or compatibility read-only recovery, keep the same stable URL and record whether the response was recovered or remained unverified.
- After a follow-up PDF, update `last_pdf`, `last_status`, and `updated_at` while preserving the URL.
- If an alias and an explicit URL conflict, stop and ask which destination is intended.
- If a tracked URL no longer opens or the user lacks access, report that without deleting the entry.
