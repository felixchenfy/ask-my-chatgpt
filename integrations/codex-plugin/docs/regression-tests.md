# Bridge regression acceptance

The review defects are covered by executable behavior tests, not checks for wording in the skill.

The round-004 JSON defect has a reduced real-DOM fixture in
`WebContentAgent/tests/fixtures/chatgpt-file-citation-json.html` and a production
browser regression in `tests/response-citation-dom.test.mjs`. The fixture was red
before repair: raw `innerText` introduced line breaks inside quoted strings via
inline citation controls. Companion 0.4.45 serializes a detached UI-free tree for
the observed file-citation markup, retaining authored boundaries without parsing
or repairing JSON. Positive and negative cases cover citations, literal filenames,
source-code classes, paragraphs/lists/tables/BR/preformatted text, genuinely invalid
JSON, ordinary reply fallback, completion/generation and submitted-turn identity.

`tests/lint-scope.test.mjs` checks that saved source snapshots under `output/`
are excluded from plugin ESLint while production and test files remain in scope.
This keeps preserved independent-test evidence from being linted as live plugin code.

| Case | Expected behavior | Automated coverage |
| --- | --- | --- |
| Extension claims at the timeout boundary | Never mark a claimed task as proven zero-submission | `WebContentAgent/tests/unit/test_chatgpt_review_regressions.py` |
| Cancel before/after the submit permit | Revoke future permits; preserve ambiguity after permit grant | Same Python suite; `tests/agent-process.test.mjs` exercises a real child process |
| New PDF has no answer yet | Never return the earlier PDF's review | `WebContentAgent/tests/js/chatgpt-regressions.test.mjs` |
| Original user turn unmounted, concurrent question visible | Fail closed rather than select another question | Same JavaScript suite |
| Emoji, CR, Unicode line separators | Python and JavaScript use Unicode codepoints and the same line normalization | Both regression suites |
| Another Companion workflow is waiting | ChatGPT command polling still runs, with one in-flight task per lane | JavaScript scheduler test |
| Foreign Origin / wrong local pairing token | No command disclosure, claim, or forged completion | Python test using a real loopback HTTP server |
| PDF card / collapsed prompt / Show more | Match only the exact prompt body, with one new user turn and stable ID | `tests/companion-dom.test.mjs`, production functions in a real offline browser |
| Prompt prefix/suffix, ambiguous markup, missing ID or concurrent turn | Never confirm a different or unverifiable submission | Same DOM suite |
| Target redirects to home or another chat | Stop before upload/typing; recheck atomically with form submission | Same DOM suite and WebContentAgent destination/workflow tests |

Run `scripts/validate-plugin.ps1` and `npm run lint` here. In WebContentAgent run
`scripts/test.ps1`; set `PYTEST_ADDOPTS` to `-m "not live and not lmstudio"` when
only bridge/offline testing is authorized. Unrelated LM Studio tests are not a
condition for calling the signed-in ChatGPT bridge operational.

## September 3 composer readiness and zero-submission retry

Two live pre-submission failures retained only `composer_loading`. Their actual
page cause remains unknown. Offline fixtures established two separate code bugs:
the readiness loop discarded specific inspector errors, and recent-request
deduplication rejected a later authorized attempt even when durable terminal
state proved zero submission.

New inspector/workflow tests ran red first (10 passed, 7 failed); the initial
persisted-state tests ran red first (13 passed, 5 failed). After the implementation
and expanded guards, targeted Python tests passed 74 results and ChatGPT JS
passed 51. The required offline WCA suite passed 296 Python + 101 JavaScript
results, with six live/LM Studio tests deselected; plugin `npm test` passed 82.
Targeted counts are subsets, not additional full-suite results.

The final documentation rerun first caught an obsolete plugin assertion that
required the old key suffix. That assertion was retired rather than restoring
the workaround. Retry safety is exercised against real persisted Python state;
the cache-repair test additionally verifies that the new retry reference is
copied intact into its isolated fake cache. Final rerun: WCA 296 + 101, plugin 82,
Ruff, ESLint and the canonical skill/plugin validator all passed.

- `WebContentAgent/tests/js/chatgpt-composer-readiness.test.mjs` executes the real
  inspector in a VM fixture: login/verification error codes, absent versus hidden
  composer, ready-input contract and no private page text in its evidence.
- `WebContentAgent/tests/js/chatgpt-submission-workflow.test.mjs` propagates actual
  errors before input, retains sanitized observations, and respects cancellation
  and remaining preparation time without extending the readiness budget.
- `WebContentAgent/tests/unit/test_chatgpt_zero_submission_retry.py` covers strict
  terminal proof, same-key replacement with preserved parent evidence, competing
  reservations, single-use permits, corrupt/missing/contradictory records,
  queued/running/submitted/ambiguous states, cancellation after permit grant,
  callback contradictions, cooldown precedence and a disconnected process restart.

Source skill instructions now preserve the original key; the historical
`-after-confirmed-zero-submit` workaround below is not the current retry policy.
No automatic retry, new key, extra upload or extra submit was added. A deployed
backend that still rejects a same-key proven-zero request must be investigated,
not bypassed.

### Health and rollout boundary

On September 3, plugin ESLint passed. `uv run wca doctor --no-smoke` passed;
this disables inference, **not model auto-loading**: its existing
`actual_model_identifier()` path woke the unloaded pinned `local-agent-vlm`.
The concurrent first MCP doctor failed its model-presence check, while MCP launch,
11-tool discovery and configuration checks passed. A subsequent
`scripts/mcp-doctor.ps1` passed after the wake. No model configuration changed and
no text/JSON/vision inference was requested. Do not call this doctor variant a
purely read-only check; use inventory-only diagnostics if model loading is out of
scope. The default `scripts/doctor.ps1` inference smokes were not run for this
offline bridge repair.

At the earlier 12:57 source-only checkpoint, Companion manifest remained 0.4.45 and plugin manifest remained
`0.1.0+codex.20260903101054`; this source patch has not been installed/reloaded.
The old loaded worker may differ even though its version string matches source.
Live inspection stopped because the control tool could not verify the browser
URL; no alternate control route was used. No new ChatGPT send/reply verified this
patch. The formal report remains with its owning task. Historical PDF live passes
do not establish acceptance for this newer source change.

## September 3 text-only null attachment and live check

The supported Chrome connection subsequently verified the normal ChatGPT home
page, with `#prompt-textarea` present, visible and empty. A text-only smoke on
actual Companion 0.4.46 nevertheless failed before submission with empty
composer/render evidence. It proved zero submission; it was not retried
automatically. The failed record's `attachment_filename` was JSON `null`.

The real attachment helper called `expectedFilename.slice()` even for no-PDF
requests. JavaScript's default parameter handles `undefined`, not `null`.
Replacing the test's whole-helper stub with production code reproduced
`TypeError: Cannot read properties of null (reading 'slice')`. Replacing the
workflow's mocked `execute` with the real wrapper also exposed eager error
throwing that discarded composer diagnostics. The first expanded run had 17
passes and five failures; after repair and further input cases, all 25 passed.

0.4.47 changes are narrow:

- Normalize only a nullish filename to the existing empty/no-attachment value;
  real filenames and staging/confirmation requirements are unchanged.
- Let the readiness caller explicitly retain a structured `result.error` long
  enough to persist it. The shared wrapper's default throwing behavior and
  legitimate void cleanup return values remain unchanged.
- Treat a missing/invalid composer result as `invalid_composer_result` at
  `composer_inspection_failed`, not a 30-second loading condition. Cancellation
  still wins before any input.

Coverage includes omitted/undefined, null and empty filenames; required but
missing PDFs; real scoped PDF/collision fixtures; execution error/default/void
behavior; and the existing zero-submission/concurrency/permit/cooldown tests.
The final full run passed 296 Python (six live/LM Studio deselected), 109
Companion JavaScript and 82 plugin tests. Ruff, ESLint and skill/plugin validators
passed. The shared extension's test version constant was updated with the
manifest; its initial version-equality failure was not a production regression.

The plugin was installed as `0.1.0+codex.20260903203321`; installed skill and retry
reference matched source. A token-protected, no-page-task version probe observed
0.4.46, negotiated its guarded reload, then observed compatible 0.4.47 with a new
worker identity. It queued no question or read operation.

After separate authorization, the exact same four-line question, session and key
were called once, without `force_send`. The actual task handshake was 0.4.47;
`retry_of_run_id` linked the original failure, whose bytes remained unchanged.
One confirmed submission returned 64 codepoints / four lines with the expected
SHA-256, `response_extraction_verified: true` and no read recovery. An independent
text/hash comparison and a read-only check of the same page agreed.

The null exception is a deterministic defect matching the actual text-only
input. The native `InjectionResult` frame array from the failed run was not
captured, so the report does not invent its exact transport error representation.
This small text pass does not prove a 34k-character report, PDF extraction, every
page state or old-conversation routing. Its owning task keeps the formal report.
No loading-type doctor was rerun during this rollout.

## September 3 submission-confirmation repair

Five pre-fix offline reproductions established four defects: the ten-second
cutoff, count-based message identity, whole-page attachment visibility and lost
failed observations. New behavior tests were run red before the implementation
change, then green after it. The historical reproducer asserts broken behavior;
do not count its successful exit as a regression pass after repair.

- `WebContentAgent/tests/js/chatgpt-submission-workflow.test.mjs`: immediate and
  15-second confirmation, 120-second/remaining-budget limits, composer hydration,
  cancellation before/after permit and after confirmation, wrong destination,
  closed tab, one send and cleanup.
- `tests/submission-confirmation-dom.test.mjs`: real offline Chromium and the
  production DOM helper; old-node unmount, older history mount, repeated IDs,
  missing ID then hydration, concurrent question, missing PDF, unmounted
  candidate diagnostics, current-form cards, selected/uploading/error/removal and
  unexpected attachments. File-state variants are synthetic; the group hierarchy
  is derived from the saved observed PDF user turn.
- Python regression tests: bounded observations survive failure/cancellation,
  unverified candidates never become submitted IDs, and observation cannot grant
  another submit permit.
- Existing native Chromium focus-lease, exact prompt-body, destination and WEB
  transition regressions remain part of the suite.

Ordinary tests send no ChatGPT messages. Read-only live acceptance of the original
77-page request is separate from a fresh upload/send test. The old failed run is
preserved, and no later DOM result can reconstruct its discarded ten-second
observations. WebContentAgent's detailed incident journal for this policy is a local, uncommitted document.

## Real PDF acceptance procedure (explicitly opt-in)

The September 3 round-002 failure added `tests/attachment-collision-dom.test.mjs`:
a reduced observed PDF tile renamed to `(1)` reproduces the old failure, then
passes on 0.4.44. Positive and negative cases cover literal stems, numeric suffixes,
consistent group/button/filename/type labels, strict pre-send staging, duplicate,
hidden/busy/error cards, history, prompt mismatch and missing IDs. Filename
diagnostics are bounded and survive failed-task persistence. These are offline
behavior checks, not proof of a successful new live large-PDF submission.

1. Run `scripts/generate-regression-pdf.py` with the bundled PDF Python runtime.
   It creates five pages, 200 distinct lines, 10,000 non-newline Unicode codepoints,
   including three emoji. It re-extracts the PDF and requires an exact match.
2. Render and visually inspect the PDF before sending it.
3. Call `ask_chatgpt` once in the chosen existing conversation with that PDF and
   the three constraints in `output/pdf/bridge-unicode-10000-expectations.json`.
   Ask for all lines verbatim, without explanations or Markdown fences.
4. Independently compare the retrieved text with `bridge-unicode-10000-expected.txt`.
   Require `response_extraction_verified`, all metrics, and the normalized SHA-256.
   Save the private run ID and the actual outcome, including failures; never turn
   a missing result into a pass. Recovery must keep the submitted message ID.
5. Do not resend an ambiguous run. A proved zero-submission disconnect requires
   the skill's manual reload procedure before its one permitted retry.

This test is intentionally not part of ordinary `npm test`: running the default
suite must not upload files or send messages to the user's account.

## Historical verification: 2026-09-01 (local time)

- Plugin: 18 tests, skill/plugin validators, and ESLint passed.
- WebContentAgent: 163 tests passed; six unrelated LM Studio integration tests
  were excluded. All 45 Companion JavaScript tests passed.
- `doctor.ps1` and `mcp-doctor.ps1` passed.
- Generated PDF: all five pages rendered and inspected; local PDF extraction
  matched all 10,000 codepoints / 200 lines exactly, including three emoji.
- First real `ask_chatgpt` attempt: **NOT PASSED**. Companion did not claim the task.
  Returned `extension_not_connected`, `message_submitted: false`, and
  `zero_submission_proven: true`. No upload or message was sent on that attempt.
- The existing MCP process reported `cancellation_supported: false`; it predates
  the installed Node adapter update. Start a fresh Codex task/MCP connection to
  pick up cooperative cancellation. The new adapter's real-child tests passed.

### Post-reload attempt: real page confirms submission, bridge reports failure

The user manually reloaded Companion. The single permitted retry used the original
arguments and idempotency key
`bridge-0433-unicode-pdf-20260902-after-confirmed-zero-submit`.
The task record confirms Companion **0.4.33** connected and a submit permit was granted.
The bridge then returned `failed`: `ChatGPT did not submit the queued question after form submission`.
Its `message_submitted: false` is NOT a zero-submission proof (`zero_submission_proven` is false).

A separate, read-only Chrome DOM check confirmed the PDF and exact prompt were
actually submitted, with one assistant response. The visible answer has **10,000
Unicode codepoints, 200 lines**, and SHA-256
`44ccecb25d4cdc272942c6f87fb867646accbef2f387f5832b53d4aa325605af`, exactly matching
the expected file. This verifies ChatGPT's transcription, **not** the bridge's
automatic retrieval path.

Two failures observed in that run prevented an end-to-end pass (fixed below):

1. `inspectChatGptSubmission` compares the whole user node's `innerText` to the
   prompt. The real PDF user node starts with `bridge-unicode-10000-regression.pdf`,
   a newline, `PDF`, another newline, and then the exact prompt. The attachment UI
   therefore makes the equality check fail even after a real submission.
2. The returned/visible conversation differs from the requested tracked conversation.
   The bridge needs to verify the destination at submission time, not merely when
   acquiring a tab. The reason the page changed destination is not yet established.

No additional message was sent. The retry allowance has been consumed. Preserve
the original tracked conversation URL; do not silently replace it with the unexpected
destination or claim the full live test passed.

### Companion 0.4.34 follow-up

- Fixed the attachment/body comparison using the observed
  `collapsible-user-message-content` container, with a unique older
  `whitespace-pre-wrap` fallback. Unknown or ambiguous markup fails closed;
  filename removal and substring matching are deliberately not used.
- Added destination guards after navigation, before upload/input, inside the
  form-submission expression, and on every response observation. Canonical
  conversations cannot change identity. A new chat or provisional WEB route
  has only its explicit transition allowance, then the resolved URL is pinned.
- If a submit was attempted but not confirmed, report `submission_unconfirmed`
  instead of asserting it was not sent. Never use this result as retry permission.
- Added six real-DOM scenarios (seven Node test results including their parent)
  plus three destination/workflow regressions. Default suites send no messages.
- Live follow-up is read-only, bound to the already observed PDF user-message ID
  and the actual test conversation. It does not retry the failed send or overwrite
  the intended blog-review conversation in the registry.

Verification on 2026-09-01 (local time): **25 plugin + 48 Companion JavaScript +
163 Python tests passed** (236 total; six unrelated LM Studio tests excluded).
ESLint, Ruff, skill and plugin validation passed.

The fresh MCP connection returned `read_completed`,
`response_extraction_verified: true`, and `cancellation_supported: true`.
The private run record confirms Companion **0.4.34** and reuse of the already-open
exact conversation tab. It read the identified original PDF turn without a new
upload or send. The returned text independently matched the entire expected file:
**10,000 codepoints / 200 lines / SHA-256
`44ccecb25d4cdc272942c6f87fb867646accbef2f387f5832b53d4aa325605af`**.
This proves the updated live read path; the repaired submission path passed the
real-DOM fixture tests but was intentionally not exercised by sending again.
