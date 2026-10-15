# Security reporting

WebContentAgent can send approved content through a signed-in browser and reads
local files selected for upload. Message identity, duplicate protection, pairing,
path containment and account boundaries are security-sensitive parts of the project.

## Reporting a concern

Do not disclose an exploit, token, cookie, account conversation or private capture
in a public issue. If GitHub shows **Security -> Report a vulnerability** for this
repository, use that private channel. Otherwise, contact the maintainer through
your existing private collaboration channel. If you have no private contact path,
open an issue requesting a private security-reporting channel **without technical
details**, and wait for the maintainer's reply before sending the report.

Provide the affected commit, a synthetic reproduction, expected and actual
behavior, and the likely impact. Test only against accounts and environments you
are authorized to use. Do not send a real user's content to demonstrate a bug.

## Support scope

This is a developer preview. Security fixes target the current main branch;
there is no long-term-support release or guaranteed response time. Keep your
checkout and unpacked extension aligned. See [privacy and permissions](docs/privacy.md).
