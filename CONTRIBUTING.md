# Contributing

Install with `uv sync --python 3.12 --frozen` and install Playwright Chromium.
Use Python 3.12 and Node.js 22 or later. Run `scripts/test.ps1` with
`PYTEST_ADDOPTS=-m "not live and not lmstudio"` for offline checks.

Keep changes in their owning layer and add a regression fixture for behavioral
fixes. Never use real prompts, signed-in page captures, tokens, cookies or account
URLs as fixtures. Synthetic examples must make private identities unnecessary.
Live tests require separately authorized accounts and must not be triggered in CI.

Preserve exact submission identity, durable duplicate protection and bounded
recovery. Do not weaken guards to make a test pass. Describe validation limits.
Do not load models, edit global client settings or install schedules in setup.
