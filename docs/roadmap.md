# Compatibility and roadmap

This is a Windows-first developer preview. These priorities are invitations to
contribute, not release commitments or claims that the work is already complete.

| Area | Current evidence | Useful next contribution |
| --- | --- | --- |
| Codex bridge | Historical live use plus offline regressions | Repeat current text/PDF/follow-up acceptance with synthetic content |
| Claude Code | Standard MCP configuration documented | Record a real client round trip and any setup differences |
| Fresh installation | Clean-checkout offline checks and Windows CI | Improve first-run diagnostics and client-specific troubleshooting |
| Browser changes | Fixtures for observed page shapes | Add complete synthetic preparation/confirmation/recovery cases |
| Periodic project reports | Prompt recipe using an external scheduler | Share a reproducible setup with scoped consent and overlap handling |
| macOS/Linux | Not validated targets | Identify Windows assumptions and propose tested portability changes |

Keep provenance and benchmark claims specific. A passing offline fixture is not
live website acceptance; one successful exchange is not a measured success rate.
Historical blog results retain their original dates.

Broad arbitrary browser control, credential export and bypassing access controls
are outside this project's scope. No background reporting is enabled by default.

Documentation checkpoint, October 15, 2026: 432 Python and 182 JavaScript offline
tests passed, with six live/model tests excluded. These checks are not live
end-to-end acceptance. Mermaid diagrams were rendered separately.
