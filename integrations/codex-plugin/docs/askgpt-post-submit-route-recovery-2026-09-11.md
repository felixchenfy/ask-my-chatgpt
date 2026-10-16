# Confirmed submission followed by a conversation mismatch

September 11, 2026. The originating task requested review of nine published blogs
in a designated conversation with its current mode. This report omits private
conversation and message identifiers; they remain in the ignored runtime records.

## Evidence and diagnosis

The original record confirms `message_submitted: true`, a durable
`submitted_message_id`, and `zero_submission_proven: false`. It records an exact
destination tab reuse, followed by one rendering wake. Several minutes later,
`inspectChatGptResponse` returned `conversation_mismatch` with a different
conversation ID. That guard runs before the response inspector queries message
bodies. No navigation actor or causal explanation is established by these events.

The backend's automatic read recovery applies only to `response_unverified` and
`timeout_after_submission`. This run returned `failed`, explaining the returned
`automatic_read_recovery_attempted: false`. False does not establish zero submission
or authorize a new send. The current read tool acquires the exact requested
destination and passes `expected_message_id` through every response inspection.
No relaxation or runtime patch was necessary to recover this confirmed turn.

The skill documented automatic recovery, older results without the automatic
field, and independent identification when a verified ID is missing. It did not
clearly cover this confirmed-send/automatic-false combination. That instruction
gap could leave the source paused even though a bounded read was available.

## Coordinated resolution

Maintenance checked the original record, expected URL, confirmed ID and matching
runtime records; no earlier read for that message was found. The source remained
the sole read owner. Maintenance provided the exact target and ID for one
`read_chatgpt_conversation` call, with the original output constraints and a
600-second timeout. Maintenance did not open ChatGPT, inspect the unrelated page,
send, upload, or perform a competing read.

The source reported successful recovery in
`chatgpt-read-20260911-012933-107b1b`: verified extraction and 9,754 characters.
It checked the feedback against the actual nine article revisions and resumed
its editing workflow. The original failed send record was preserved. Retrieval
is resolved; the cause of the intervening navigation remains unknown.

## Existing skill update

The entrypoint, local workflow reference and submission-confirmation reference
now describe this branch consistently. Maintenance checks the preserved confirmed
identity and unused recovery allowance, then assigns one task the bound read.
Already confirmed IDs do not require reconstructing the prompt from the DOM.
Missing IDs retain the separate independent full-turn verification procedure.
An attempted automatic or diagnostic recovery still excludes another recovery
loop; failed reads return evidence to maintenance. Cancellation and stale requests
remain stop conditions for continuation.

This is a procedure repair. The URL guard and send/idempotency implementation
are unchanged. Skill/package validation and installed-file comparison check the
instruction release; the separate source-owned live read establishes retrieval
of this answer, not new-send acceptance or a fix for navigation.
