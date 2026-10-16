# Contributing to WebContentAgent

Contributions are welcome: fixes, documentation, setup reports, synthetic browser
fixtures, accessibility improvements and well-scoped feature proposals all help.
Maintained by [Feiyu Chen](https://github.com/felixchenfy).

## Find a useful first contribution

- Validate the documented Claude Code setup and describe the exact environment.
- Make a confusing installation step easier to follow.
- Reproduce a browser-layout failure with a small synthetic fixture.
- Improve error messages while preserving privacy and submission safeguards.
- Propose portability improvements with a concrete test plan.

See the [roadmap](docs/roadmap.md). For a large change, open an issue explaining
the user problem and proposed scope before implementing it. Use issues for
ordinary questions and bugs; follow [SECURITY.md](SECURITY.md) for vulnerabilities.

## Set up and validate

Fork the repository, or use a branch if you are a collaborator.
Clone your fork, create a descriptive branch, and run:

```powershell
uv sync --python 3.12 --frozen
uv run playwright install chromium
$env:PYTHONUTF8 = '1'
$env:PYTEST_ADDOPTS = '-m "not live and not lmstudio"'
.\scripts\test.ps1
```

Use Python 3.12 and Node.js 22 or later. The script checks Ruff formatting/lint,
Python tests and JavaScript tests. Offline tests should work without personal
configuration, a ChatGPT account or a loaded LM Studio model. Live tests are
separate and must not run in CI or send messages without authorization.
See the [testing guide](docs/testing.md) for the inventory and conventions.

## Submit a focused pull request

Explain the problem, the resulting behavior and what you tested. For behavioral
changes, add a regression at the lowest useful layer. Include an example when
it makes the change easier to review, and document limitations honestly.

Keep runtime changes in their owning layer. Preserve exact message identity,
durable duplicate protection, bounded recovery and filesystem scope. Do not
weaken a guard to make a test pass. Avoid unrelated refactors and generated logs.

Use synthetic prompts and identities in fixtures. Never commit browser profiles,
cookies, tokens, real conversation URLs, private PDF content or signed-in DOM
captures. Setup must not silently load models, change global preferences or
install schedules. Review `git diff --cached` before committing.

## AI-generated and AI-assisted changes

The project's code was generated with Codex and Claude Code under human direction.
AI-assisted contributions are welcome. Name the tools used in the PR and describe
how you reviewed the change and verified its behavior. You remain responsible for
the patch, its provenance and its tests; a model's claim that checks passed is
not test evidence. Do not submit private prompts or hidden reasoning transcripts.

Documentation is written in English. Please communicate respectfully and follow
the [community guidelines](CODE_OF_CONDUCT.md). By submitting a contribution, you
agree that it can be distributed under this project's [MIT license](LICENSE).
