# Privacy and publication

Private prompts, replies, session URLs, PDF paths, local tokens, browser state,
diagnostic captures and downloaded content belong outside Git. Generated pairing
assets, runtime, output, scratch directories and personal agent instructions are
ignored. Incident journals are kept locally; only the explicitly listed release
documents enter this repository.

The extension can operate approved ChatGPT and optional forum/search tabs using
its declared Chrome permissions. Its debugger permission supports native input,
file staging and bounded rendering recovery. It is powerful; inspect the source
and manifest before installing. Do not distribute generated pairing files.

The bridge sends chosen prompts/PDFs to the signed-in ChatGPT service. It does
not make these uploads offline. Local extraction uses localhost LM Studio.
The project does not export browser cookies or copy account credentials.

The initial release uses a new root commit, excluding personal development
history. This removes that history from this repository, not from independent
backups or copies someone else may already possess. Never rely on history
rewriting alone if an actual credential has been exposed: revoke it as well.

Report security concerns privately to the maintainer; do not put live tokens,
account conversations or private DOM captures in public issues.
