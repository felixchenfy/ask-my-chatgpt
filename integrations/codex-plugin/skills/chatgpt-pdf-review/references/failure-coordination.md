# Coordinating AskGPT failures

First account for the user's consent policy for GPT messages: what the user
asked to send in this task, plus any standing consent recorded in an
optional local preferences file (not committed; see the plugin README).
Distinguish operational failures from actual safety/approval denials; the latter
are authorization questions for the user, not maintenance workarounds.

Use this procedure when an AskGPT operation fails, returns an ambiguous outcome,
cannot connect to the bridge, violates an extraction/output expectation, or appears
to expose a bridge defect. A poor but correctly retrieved GPT opinion is ordinary
task feedback; contact maintenance only when the bridge, browser operation,
extraction, validation, installation, or runtime behavior may be at fault.

When the user has authorized it, coordinate between the task that sees the
problem and the designated maintenance task (a separate Codex task the user set
up for bridge maintenance).
This coordination is part of completing the original AskGPT request; do not stop
after merely announcing the failure.

Routine diagnosis, repair and safe continuation may be pre-authorized in the
optional local preferences file (not committed; see the plugin README). When they are, do not ask the
user to reload, restart, paste the request again or reapprove those steps. The
authorization covers diagnostic messages to maintenance and its progress/final
replies to the originating task; it does not authorize unrelated chat messaging.

1. Preserve the exact result, durable task record, conversation URL, session and
   idempotency keys, relevant input/attachment identity, installed plugin path,
   expected output constraints, and whether the request is still current. State
   the narrow submission evidence: attempted, confirmed, or proven zero. Do not
   copy secrets, browser credentials, full private page content, or unrelated tabs.
2. Use Codex task tools to list tasks and find the designated maintenance
   task. Send it a compact diagnostic delegation with
   the source task ID, evidence paths, observed result, requested repair scope,
   and explicit warning against resend when delivery is uncertain or the request
   has become stale. Do not create a duplicate maintenance task. If the originating
   task is itself the maintenance task, perform the maintenance work
   there and skip the self-message.
3. The originating task remains responsible for the user's requested outcome. It
   should complete useful work that cannot conflict with the bridge repair, tell
   maintenance about new evidence, and wait for or read the maintenance result.
   While maintenance owns the incident, it must not run a competing AskGPT call,
   reload the shared page, or edit the same bridge files concurrently.
4. The maintenance task should reproduce the failure at the lowest useful layer,
   preserve the original record, and distinguish a code defect from a valid input
   rejection. For a defect, implement the smallest reusable repair, add behavior
   regression coverage, run the relevant full offline checks, and verify
   installed/runtime state when feasible. It sends concise progress and a final
   report back to the source task ID shown in the delegation. Distinguish an
   observed root cause from a plausible hypothesis.
5. After the maintenance report, the originating task verifies the reported
   artifact/runtime state and resumes automatically when the original action is
   still authorized and the existing retry/recovery rules prove that continuing is
   safe. Reuse the preserved session and idempotency key. Never use a new key or
   `force_send` to bypass an ambiguous outcome. If the request is stale, keep the
   repair and ask the current version only when the user's original workflow still
   authorizes that new request.

Close the incident only after the source task confirms the intended feedback was
retrieved, or records that the original request is stale or has a concrete remaining
dependency. A repair installation alone does not close the user's workflow. The
source task owns continuation; maintenance must not race it with a second send or
read. The standing coordination authorization includes scoped read-only diagnosis
of the failed operation; a missing verified ID calls for the independent identity
procedure in [submission-confirmation.md](submission-confirmation.md), not a new
send or a blanket request for permission to investigate.

If the maintenance task cannot be found or contacted, continue all safe local
diagnosis and preserve a resumable handoff. Tell the user the precise coordination
failure only after that work; do not convert it into permission to resend.

## Page changes that affect several stages

Record the active owner of the code repair, live tab inspection, and any permitted
continuation or recovery read. Ownership can be handed between the two existing
tasks explicitly, with acknowledgment and disjoint file scopes; it is not fixed
by their titles. Never let both tasks act on the same live operation. If either
task is updating a shared blog, coordinate its publication scope separately so a
local-only incident write-up is not included in another task's public deployment.

When a page change breaks one stage, inspect the remaining structural contract
before declaring the workflow repaired: composer, text serialization, Send
control, route transition, original user identity, assistant ownership and
completion controls. Gather only scoped evidence with available authorized tools;
do not use repeated sends to discover the next selector. An offline fixture must
retain the observed combination, rather than pair a new composer with legacy
messages or an old Send button. Test the legitimate path and its closest ambiguous
neighbors through the production helpers.

An unchanged structural snapshot with a visible Stop control does not establish a
rendering stall. Preserve the existing generation, observe bounded status without
reading the answer, and distinguish a hypothesis from a demonstrated rendering
failure. Do not spend the recovery read merely to check progress. A disappeared
Stop control alone also does not establish a complete, correctly owned reply.

## Validation before a task exists

An adapter-level `PermissionError` saying the PDF is outside configured roots can
occur before request reservation and task creation. A generic
`webcontentagent_bridge_failed` result, short duration, or absent task file does
not by itself prove that no send occurred. Maintenance must verify the installed
call path, the precise failing validation, and the shared request/session state and
task records for the original operation. The listener may have started even though
no browser task was queued.

When that evidence proves this invocation stopped before reservation, and there is
no earlier durable request for its key, the source may stage the same approved PDF
as a regular, byte-identical file in an existing allowed root. Check the resolved
path, size and file identity; do not change the allowlist, create a link alias, or
substitute another revision. Preserve the question, session, operation key, model
mode and output constraints; change only `attachment_path`. While the request is
still authorized and current, allow one continuation after maintenance reports
this evidence. The backend's current state still governs acceptance. If the key
already has a record, a path change can conflict with its fingerprint: reconcile
that state instead of applying this no-record exception. Return a repeated failure
to the same maintenance exchange.

## Busy bridge and repeated failures

`bridge_busy` is a pre-queue rejection, so there may be no run ID or task file for
that invocation. Preserve the returned result and original arguments. Maintenance
should identify the active owner or a stale listener without killing a process,
reloading the shared extension, or starting a competing read. If the owner is
unknown, elapsed time alone is not proof that the listener is abandoned.

After the active owner finishes and maintenance verifies availability, the source
task may make one continuation with the original arguments and key while the
request remains current. Existing durable state for that key still governs whether
the backend can queue it; a busy rejection does not erase an earlier ambiguous send.
If it is busy again, return the new evidence to maintenance instead of polling with
repeated tool calls.

Stopping sends does not mean abandoning diagnosis. A second failed retry,
unavailable server, or unresolved version mismatch returns to the same maintenance
exchange. State any remaining external dependency precisely, such as a user-owned
draft or manual login. A repair report and a matching version do not themselves
prove that the user's requested answer has been retrieved.
