# Submission confirmation and unknown outcomes

Companion 0.4.43 observes one submit attempt for up to 120 seconds, capped by the
remaining pre-submission budget. Composer readiness (30 seconds) and attachment
staging (60 seconds) share that budget. Once the original user turn is confirmed,
the requested response-wait budget starts. A stalled hidden renderer may receive
the same one-time target-scoped rendering wake used by the response wait.

Confirmation checks known IDs and witnessed DOM ordering, exact prompt-body
equality, an empty composer, the destination, and the required PDF on the same
user turn. Mounted node counts and filenames elsewhere on the page are not proof.
Unknown/changed DOM markup fails closed; it is not permission to use looser text
matching or to resend. Existing draft attachments are preserved and block a new
send until the user resolves them.

Since Companion 0.4.44, confirmation can recognize an appended numeric filename
collision suffix, such as `report.pdf` displayed as `report(1).pdf`, only on the
witnessed new user turn with consistent attachment labels. Staging still requires
the exact original filename. Inspect `last_observation.attachment.expectedFilename`,
`observedFilename`, `filenameMatch` and `state` together; a match label alone is not
submission proof. Do not strip suffixes yourself, relax expectations, or promote
a candidate ID. This handles a demonstrated display-name variant without
changing the single-send and verified-ID recovery rules.

Companion 0.4.81 also recognizes the observed submitted PDF
`span.group/resource-card`: one `button.peer/resource-card` named for the file,
one matching `.truncate.font-semibold` filename and one `PDF` type label. It
must be visible, ready and inside that exact user message, outside its prompt
body. This differs from the composer card and does not broaden staging. Extra,
wrong, hidden, busy or malformed cards still fail confirmation. A failed older
confirmation record stays unchanged after repair; establish a missing original
user ID independently under the procedure below before assigning a bound read.

Since Companion 0.4.67, prompt extraction restores single-backtick delimiters
around the observed `code.user-message-inline-code` representation. Three such
spans previously produced a six-character deficit. This is a scoped DOM adapter,
not permission to strip Markdown from the expected question, accept an approximate
match, or infer delimiter widths for other code markup. Companion 0.4.82 adds the
October 5 observed raw search-unit representation: an attributeless, text-only
`CODE` directly under its `.whitespace-pre-wrap` user body, with inline display.
Twelve such nodes caused a verified 24-character deficit. Nested/PRE, classed,
attributed or other-role code nodes and multiple-backtick delimiters remain
unsupported by this adapter. A similar length difference alone does not establish
the cause; compare the complete original prompt with the scoped DOM and reproduce
the difference before choosing a repair. Never strip backticks from the expected
question to make confirmation pass.

On failure, read the returned structural diagnostics:

- `submission_attempted`: the worker reached the one submit attempt.
- `message_submitted`: the bridge recorded confirmation, not merely permission.
- `zero_submission_proven`: authoritative absence of a submit permit/attempt.
  False or absent means do not assume nothing was sent.
- `last_observation`: phase, confirmation elapsed/budget, mounted counts, prompt
  match and lengths, attachment state, destination, visibility and candidate ID.
- `candidateMessageId` / `candidateVerified`: an observation, not automatically
  the durable `submitted_message_id`. Never promote it to unlock recovery.

When confirmation is missing, report the narrow fact: “The submission could not be confirmed; it may have
reached ChatGPT.” Preserve the original request, task record, URL and idempotency
key. Do not resend, re-upload, reload the conversation, or ask GPT to repeat its
answer. Do not claim that the PDF was absent merely because confirmation failed.
Routine recovery uses the existing skill's verified-ID rule. A missing verified ID
does not end the maintenance exchange.

### A confirmed send followed by a different conversation

If `message_submitted: true` and a durable `submitted_message_id` are present,
report that submission succeeded and reading failed. A later `conversation_mismatch`
does not undo that confirmation. Preserve the expected URL and mismatch evidence;
do not inspect the unrelated conversation's body or infer who caused navigation.
The current backend returns this case as `failed`, so its automatic recovery does
not run. `automatic_read_recovery_attempted: false` alone grants no retry.

Maintenance verifies the original task record, canonical destination and confirmed
message ID, and checks for any previous automatic or diagnostic recovery. If none
has consumed the allowance and the original request remains current, assign one
task a single `read_chatgpt_conversation` call with the original URL, the confirmed
ID as `expected_message_id`, and the original output constraints. The read tool
selects the exact destination and checks URL and message identity before accepting
an answer. Do not manually navigate the unrelated page, resend, or remove identity
checks. Preserve the separate read result and have the source verify the review's
revision. If this read fails, return its evidence to maintenance without another
read loop. An already confirmed ID does not require reconstructing the prompt from
the DOM; the independent verification below applies when that ID is missing.

### When the original message ID is missing

Under the user's standing failure-coordination authorization, maintenance may
inspect only the intended conversation read-only and independently establish the
original user turn. Locate it without selecting from the failed candidate ID,
compare its complete normalized prompt against the preserved original, verify the
required attachment evidence when applicable, and establish unique message identity
and ordering. A length difference, nearby answer, filename alone, or plausible
text is insufficient. If the page cannot establish the full identity, report that
specific gap and do not read an arbitrary latest answer.

After independent verification, coordinate one diagnostic
`read_chatgpt_conversation` call bound to that actual message ID, preserving output
constraints and checking that no prior automatic or diagnostic recovery has already
consumed the read allowance. Either maintenance or the source owns that read, never
both. Save its separate result, leave the original failed record unchanged, and have
the source verify the intended revision before using the feedback. This does not
promote a candidate ID or manufacture a successful send. No resend, re-upload or
page reload is part of this diagnosis. Respect a later cancellation or narrower
user instruction. State whether live verification covered reading only or also a
new upload/send; fixture tests do not prove fresh live submission behavior.

### A new route and new message markup

The September 24 page used `/c/local-chatgpt%3A<uuid>` temporarily before a stable
canonical URL. Companion 0.4.75 recognizes this bounded provisional transition;
it does not save that route as session identity or permit an existing canonical
conversation to drift. After an attempted send fails confirmation at this stage,
wait for the original tab's stable destination without navigating or resending.

The same page lacked legacy `data-message-author-role` nodes. User and assistant
search units had their own role suffixes and message-ID attributes, while their
outer `data-turn-key` remained the user's ID. Thus the outer turn key alone cannot
identify an assistant. Independent user verification still compares the entire
original prompt and unique identity; expected length alone is not a match.

Before authorizing a recovery read, establish that the installed reader supports
the actual assistant unit and its completion controls. Intermediate `primary` or
`tertiary` text without assistant ownership, the user's `Copy message`, and an
assistant's `Copy table` are not evidence of a completed answer. A Copy control
outside the assistant unit needs verified association with that same exchange.
Coordinate an offline adapter repair if those relationships are unsupported;
do not substitute arbitrary page text or consume the sole read to probe support.
Keep failed submission evidence separate from any subsequent verified read.
