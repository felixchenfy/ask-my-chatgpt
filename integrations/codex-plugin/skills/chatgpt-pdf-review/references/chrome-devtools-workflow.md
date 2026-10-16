# Legacy Chrome DevTools workflow

This direct browser-control route is retained only as historical implementation documentation. It is not the authoritative skill workflow because current Codex browser controls can require action-time confirmation before sending. Use [local-agent-workflow.md](local-agent-workflow.md) instead.

Use the globally configured `chrome-devtools` MCP with Chrome remote debugging enabled at `chrome://inspect/#remote-debugging`. Chrome must remain open and signed in to ChatGPT. If Chrome blocks the connection behind its own browser prompt, report that external blocker; do not replace this route with another browser profile. Do not inspect unrelated tabs, cookies, storage, credentials, or network authorization data.

1. List pages and select the intended `chatgpt.com` page without bringing unrelated pages forward. For a continuation, navigate directly to the known conversation URL. For a new review, use `https://chatgpt.com/` and ensure Temporary chat is off.
2. Verify the visible account state and the composer. If ChatGPT requests login, MFA, CAPTCHA, verification, or recovery, stop and ask the user to complete it manually.
3. Attach the PDF with `upload_file`. Chrome DevTools may restrict readable files to OS temporary directories. If the source path is rejected, copy only the requested PDF to `%LOCALAPPDATA%\Temp\<original-readable-filename>.pdf`, verify the exact source and destination, upload that copy, and leave the original unchanged.
4. ChatGPT's real `input#upload-files` is inside a hidden parent. If uploading through the visible Add-files menu does not trigger a chooser, use `evaluate_script` to temporarily expose that existing input and give it an accessible label. Take a snapshot, call `upload_file` on the resulting input UID, then immediately restore the input and parent attributes/styles. Do not inject a replacement input or call ChatGPT's private APIs.
5. Verify that the attachment chip shows the exact filename and that upload processing has completed. Fill the exact review prompt.
6. Take a fresh snapshot, revalidate the destination, filename, and prompt, and click the semantic Send button once without pausing the workflow.
7. Never retry after an uncertain click result; inspect the existing conversation to determine whether the first click succeeded.
8. Wait until Stop answering disappears and the newest assistant response is stable. Return that response and the canonical `https://chatgpt.com/c/...` URL.
9. Detach the MCP client when finished. Chrome and the ChatGPT conversation may remain open. Remove an upload-only temporary copy when safely possible; disclose it if cleanup is blocked.
