# Composer readiness and a later safe retry

Use this reference for a composer failure or a later attempt after a pre-submission failure. It permits one continuation after a verified repair under the user's standing coordination instruction when the original request is still current and the conditions below hold. It does not authorize blind retries or a new request after cancellation.

## Interpret the actual evidence

Read the returned error and the failed run's private `last_observation`. Current source records `composerErrorCode`, `composerEvidence.present/visible`, `renderEvidence`, `composerElapsedMs` and `composerBudgetMs`. These are bounded structural diagnostics, not the page body or proof of submission.

- `not_signed_in` / `manual_verification_required`: preserve and verify the inspector's specific finding with maintenance; do not mistake a bridge defect for a login requirement. Never automate login, MFA or verification. If a genuine account dependency remains, record it accurately, continue independent work and avoid repeated user-action questions under the standing preference.
- `draft_conflict`: preserve the existing text and attachments. Maintenance can investigate a false detection, but must not clear a real draft to make the request pass. If user action is required to free the composer, keep the request resumable and explain that concrete dependency.
- A readiness-budget timeout with no specific error: report an unavailable composer, not an invented login or selector diagnosis. Missing versus hidden markup and rendering state narrow an investigation but do not establish its cause.
- Older records containing only `phase: composer_loading`: evidence is insufficient to distinguish those causes. More time or a different tab is not automatically a fix.

Preserve the exact question, destination/session, attachment, mode, expectations, timeout and idempotency key privately so the user need not paste them again. Do not start a parallel read, send or reload while the original operation is active. A denied browser inspection is a stop condition; do not change control channels to bypass it.

## An empty composer reported as containing attachments

The native file input can retain a previous submitted file even after the composer
has no text or attachment cards. A browser inspection that exposes an empty
`files` array may not expose the same native state as the production inspector.
Neither that view nor a nonempty `FileList` establishes the current draft.

Maintenance can run `scripts/inspect-chatgpt-composer.py --failed-run-id <run-id>`
from the configured WebContentAgent repository after the original call ends.
The command requires the current matching failed request with authoritative zero
submission and an existing canonical conversation/session. It refuses a busy
listener and uses the production tab-selection and composer inspector in read-only
mode. It does not create or navigate a tab, clear or type input, upload, grant a
send permit, read a reply, or change the failed record. The report is private and
contains bounded structural evidence, including the actual tab, composer form,
card counts and native selection. A worker version negotiation may reload the
extension; coordinate ownership before running it. Never run it alongside an
active send/read or use an AskGPT call just to inspect the composer.

Current scoped attachment cards, their names and readiness, existing text, and
in-progress uploads govern draft protection. Raw file selection is diagnostic;
it cannot prove an attachment is ready, invalidate a verified current card, or
justify clearing a real draft. With no Send button, inspect the composer's form
directly. If a required field is missing from an older record, report that gap
rather than inferring a zero value. After a verified repair, the originating task
owns the bounded continuation below and reports whether the intended feedback
was retrieved. A second failure returns to joint diagnosis, not a send loop.

## A PDF remains after an attachment-staging failure

A visible filename and Remove control may belong to a current composer card
whose markup the old inspector missed. Diagnose it with the production helper;
do not clear it or upload a second copy to test the repair.

Companion `0.4.83` also waits for one unnamed card immediately after its own
native upload when upload progress is still active. The original 60-second
staging budget and exact ready-card checks remain in force. This transient card
does not establish filename identity or readiness; wrong names, multiple cards,
failed/removed cards, and a card without active progress remain refusals.
Pre-existing attachments or progress still block a new upload.

After repair and runtime verification, run that read-only diagnostic again for
the original failed run. For the exact attachment-staging timeout, or the observed
premature `ChatGPT attachment staging is unexpected` failure whose preserved
record has one not-ready unnamed card and active upload progress, the backend
can derive `preserved_queued_attachment` from a report no more than five minutes
old: the unchanged proven-unsent parent, original PDF bytes, one unique matching
existing or managed tab, one ready exact-filename card, empty prompt and no ongoing upload must all
match. The originating task then owns one continuation with the original
arguments and key. The worker rechecks the same tab and current draft, skips
re-upload, inserts the original question, and retains normal destination,
full-question, attachment and send-permit checks. A changed draft is preserved.

If the user restores the exact original conversation in a new tab, its browser
ID can differ from the earlier managed tab. Diagnose the restored tab read-only;
do not edit the old record or browser routing state to make the old ID appear
current. A unique exact canonical destination can be inspected as `existing`.
The backend binds the continuation to that freshly inspected ID; duplicate
matching tabs, changed text, wrong files and missing proof still fail closed.

Never supply this internal field or edit records to enable it. A stale diagnostic
calls for another read-only inspection, not another send. Installation and a
ready card do not prove PDF submission or retrieval; the originating task must
verify the intended feedback before closing the incident.

## Text is present after an input-verification failure

A page may expose a visible form-contained editable textbox labeled `Ask ChatGPT`
without the legacy composer ID or testid. The current shared resolver recognizes
that observed shape only when it is unique and outside history/dialog controls.
An unavailable legacy selector does not establish an unavailable input. Preserve
the actual markup evidence; do not broaden to the first contenteditable element
or assume every locale uses the same label. All stages must use the same resolver.

An old `composer_ready` observation is from before input; its zero character count
does not prove insertion failed. Current code records a separate
`composer_input_verification` observation. An editable DIV's `value` can be empty
while its DOM contains the question. Its paragraph and inline URL wrappers also
make raw `innerText` and `textContent` unsuitable for exact comparison; production
code serializes the paragraph boundaries and inline text. Do not globally remove
spaces or accept an approximate match to pass verification.

Preserve any remaining text and return a repeated failure to maintenance. After a
verified repair, the same-key continuation can retain an already queued text-only
draft without clearing or inserting it again. The backend derives this allowance
only from the matching explicit key/session and proven-unsent parent's exact
input-verification failure, or a Send-not-ready failure whose post-input record
explicitly verified the entire question; it records `preserved_queued_draft` with the parent
run and observed tab. The worker requires that exact tab, the entire original
question and normal attachment/destination checks. A changed draft, another tab,
an unrelated failure, missing proof or a new operation receives no exception.
Do not fabricate this internal field, edit records, or manually submit as a
workaround. The source still owns the one authorized continuation and final
feedback verification; the failed record stays unchanged.

For Send-not-ready failures, inspect the saved button presence, type and disabled
state rather than assuming input failed. The production resolver requires one
visible submit button in the composer's form and recognizes the observed exact
`Send` accessible label as well as legacy labels. Multiple candidates, another
form, a disabled button or a non-submit control must not be clicked as a fallback.

## Preconditions for a later caller-authorized attempt

The original call must have ended. Require its matching durable record to have an allowed terminal status (`failed`, `extension_not_connected`, or `cancelled_before_submission`), `message_submitted: false`, and `zero_submission_proven: true`. Any granted/attempted/submitted evidence or inconsistent state overrides a claimed zero flag. Missing or older incomplete proof is insufficient. Cancellation requires new explicit authorization; a casual `done` does not revive a canceled request.

After an explicit retry request, or a verified maintenance repair while the original request remains authorized and current under the standing failure-coordination instruction, make one call with the preserved arguments and the **same key**. An `extension_not_connected` failure follows [extension-reconnect.md](extension-reconnect.md), including its automatic continuation after verified repair; no manual Reload or new user approval is required. Do not use `force_send` or mint a new key to get past a block.

The source backend validates both private and shared state, checks cooldown, and atomically reserves one replacement run. It retains the parent failure and records `retry_of_run_id`; it does not re-enable the parent's submit permit. In-flight, submitted, ambiguous and throttled requests remain protected. This exception depends on authoritative zero-submission evidence, not elapsed time. Explicit operation keys and unresolved sends stay protected across days; only completed automatic fingerprints expire after their convenience window. It is not a general exactly-once guarantee.

If the backend returns `duplicate_blocked` or `throttled_cooldown`, stop sending and preserve that result. A source fix does not prove the installed worker/backend has changed. Report a version/state mismatch when supported; do not repeatedly call, switch keys or request the user to restate the content. A repeated failed retry ends the send attempt; send the new evidence back to maintenance and continue diagnosis without an automatic send loop.

## Correcting an accidentally strict thinking preference

If the original request asked for max thinking but did not prohibit fallback, and
the caller incorrectly imposed strict `max`, maintenance may correct only that
configuration to `prefer_max`. This is not permission to relax a user-required
strict mode. A direct argument change conflicts with the durable fingerprint;
do not change keys, use `force_send`, or edit the ledger by hand.

Use `scripts/correct-unsent-chatgpt-mode.py` in the configured WebContentAgent
repository with `--idempotency-key`, `--expected-run-id`, `--source-task` and a
concise `--reason` explaining the original authorization and caller mistake.
Review its default dry-run result, then run with `--apply` when that correction is
authorized. It requires the current matching failed run, authoritative zero
submission with no conflicting evidence, the original strict mode and a matching
fingerprint. It changes no question, attachment or output constraint, grants no
permit, starts no listener and queues no browser work. The original failed record
remains unchanged; the ledger keeps a dated correction and the subsequent run
inherits that audit plus `retry_of_run_id`.

The source task then owns one still-current continuation with the same key and
preserved arguments except `thinking_mode="prefer_max"`. Cooldown, duplicate
protection and new input validation still apply. A changed document/review revision
must be reconciled separately; this correction is not authority to substitute it.
If the original request actually prohibited fallback, keep strict mode and resolve
that concrete requirement instead. Report PDF validation, upload evidence and mode
verification separately: a mode failure before staging proves nothing about live
upload success.
