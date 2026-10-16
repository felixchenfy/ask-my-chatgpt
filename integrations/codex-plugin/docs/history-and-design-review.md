# Bridge history and design review

September 3, 2026. This checkpoint records the existing plugin implementation
after reviewing its complete five-commit history and the sibling agent's journal,
submission policy, regression evidence, and current source. It is not a new
runtime release or a new ChatGPT acceptance test.

## What Git records

| Commit | Recorded change |
| --- | --- |
| `a7bbdf3` | Original confirmed PDF-review plugin and browser procedure |
| `8302ad5` | Attachment-label handling |
| `3eba1d2` | Stable Chrome configuration for persistent login |
| `01246ad` | Independent round-005 PDF acceptance artifacts |
| `1f1fe4f` | Round-006 same-conversation PDF/citation acceptance artifacts |

The two September acceptance commits recorded test artifacts, not the intervening
production implementation. Before this checkpoint, the fast MCP adapter,
Python routing adapter, cancellation protocol, installation helpers, updated
skill/reference files, and additional regressions were still working-tree changes.
Commit order alone therefore does not establish when a fix was deployed.

This commit captures that plugin source and its tests without adding runtime
records, new acceptance artifacts, private conversations, or generated pairing
assets. Previously committed acceptance artifacts are left unchanged.

## Cross-repository boundary

The sibling WebContentAgent repository was at `17a4194` during inspection. Its
current ChatGPT modules, Companion, and many regression files are untracked;
tracked shared files also contain unrelated ongoing work. Those files are not
included by this plugin commit. Tests below use the current sibling working tree,
so this checkpoint is **not a standalone reproducible release** of both projects.
Preserve the sibling work and review its shared extension changes separately
before making a complete cross-repository release.

The blog repository was at `7eccb1d`; its last bridge-specific commit was
`b3b4da7`. The expanded A–Y reference and bridge presentation support were also
working-tree changes. The blog review is a separate local editorial checkpoint.

## What the failures taught

- Identity and authority must be explicit: mounted counts, a visible filename,
  or a successful click cannot stand in for a confirmed message and destination.
- Process lifetime and phase deadlines are part of the protocol. A restarted
  worker may continue a confirmed read, but must never reconstruct another send.
- Failures in inspectors must remain distinguishable from a page still loading.
  The real helper and injection wrapper are necessary regression boundaries.
- Strict guards need valid transition cases as well as rejection cases. The
  canonical-destination repair missed the new-chat provisional route; duplicate
  protection initially blocked proven-zero, later-authorized retries.
- Test environments can hide causes. Native visibility testing and actual
  helper/wrapper fixtures exposed conditions that broader mocks had removed.
- Installation, offline tests, and live acceptance prove different things.
  Preserve failed outcomes and verify the branch a live acceptance actually used.

The blog reference already covers these major families in A–Y. The editorial
change develops their causal relationship in the main article rather than
counting every negative fixture as another production bug. The 72-row coverage
audit mixes defects, safeguards, tooling issues, and evidence limitations.

## Validation for this checkpoint

- Plugin `npm test`: 82 passed, zero failures.
- Plugin `npm run lint`: passed.
- Tests use isolated fixtures and do not submit to the signed-in ChatGPT page.
- No WebContentAgent full-suite rerun, model-loading doctor, reinstall, worker
  reload, or live acceptance was performed for this checkpoint.
- Older-conversation `conversation_mismatch` remains unresolved. The successful
  text-only runs do not replace PDF acceptance or establish general reliability.
