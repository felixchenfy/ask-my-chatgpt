# Compatibility and roadmap

This is a Windows-first developer preview. These priorities are invitations to
contribute, not release commitments or claims that the work is already complete.

| Area | Current evidence | Useful next contribution |
| --- | --- | --- |
| Codex bridge | Historical live use plus offline regressions | Repeat current text/PDF/follow-up acceptance with synthetic content |
| Claude Code | Standard MCP configuration documented; MCP stdio round trip checked by `scripts/chatgpt-live-check.py` | Record a real Claude Code session and any setup differences |
| Fresh installation | Clean-checkout offline checks, Windows CI, opt-in live check script | Improve first-run diagnostics and client-specific troubleshooting |
| Browser changes | Fixtures for observed page shapes | Add complete synthetic preparation/confirmation/recovery cases |
| Periodic project reports | Prompt recipe using an external scheduler | Share a reproducible setup with scoped consent and overlap handling |
| macOS/Linux | Offline suite passes on Linux (Windows-only tests skipped); browser workflow not validated | Identify Windows assumptions and propose tested portability changes |

Keep provenance and benchmark claims specific. A passing offline fixture is not
live website acceptance; one successful exchange is not a measured success rate.
Historical blog results retain their original dates.

Broad arbitrary browser control, credential export and bypassing access controls
are outside this project's scope. No background reporting is enabled by default.

Test checkpoint, October 15, 2026: on Windows, 481 Python tests passed with
0 skipped and 182 JavaScript tests passed; on Linux, 476 Python tests passed and
5 Windows-only tests were skipped. Six live/model tests are excluded from
offline runs. The live ChatGPT check (new chat, same-chat follow-up, read-only
read) passed on Windows. See [testing](testing.md) and the
[release notes](release-notes.md).
