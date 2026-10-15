# Repository presentation decisions

Reviewed on October 15, 2026. There is no single best public-repository template;
this layout is chosen for a small, browser-integrated, AI-generated developer tool.

## What visitors should learn first

The README leads with the user benefit: connect implementation work in a coding
agent with project discussion in ChatGPT. It then discloses AI-generated code,
states compatibility, shows an example and provides the shortest installation path.
Optional collection features follow the primary workflow.

Long setup, troubleshooting and reporting recipes live in versioned `docs/` files
with relative links. This keeps examples reviewable alongside code without adding
a second documentation build or requiring readers to search incident journals.

CONTRIBUTING explains small useful changes, validation and responsibility for
AI-assisted patches. Issue forms request reproducible, sanitized evidence; the PR
template asks for purpose and checks. SECURITY and community guidelines provide
reporting paths without publishing the maintainer's private contact details.

The README does not claim Claude Code live acceptance, an installed scheduler,
perfect conversation memory or current PDF benchmarks. Public presentation is
prepared now; repository visibility remains private until explicitly changed.

## Sources and how they informed this layout

- [GitHub: About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes): purpose, value, starting steps, help and maintenance belong at the entry point; use relative links for local documentation.
- [GitHub: Contributor guidelines](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors): make the contribution process discoverable through a recognized CONTRIBUTING file.
- [GitHub: Issue form syntax](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms): structured bug and feature requests with required reproduction fields.
- [Microsoft Playwright MCP README](https://github.com/microsoft/playwright-mcp/blob/main/README.md): a useful example of prerequisites and client-specific setup close to the quickstart. Its broader browser capabilities are not claims about this project.
- [Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli): stdio command, arguments, environment and timeout configuration.
- [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp): registration syntax, user scope and client-side timeout settings.

These sources informed structure and client configuration. Project behavior is
checked against this repository's code and tests, not inferred from another tool.
