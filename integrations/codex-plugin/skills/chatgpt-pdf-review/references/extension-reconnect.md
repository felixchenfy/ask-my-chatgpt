# Chrome Companion reconnect

Use this procedure only when `ask_chatgpt` returns `extension_not_connected` or explicitly says Chrome Companion did not connect. It covers a pre-submission infrastructure pause, not response recovery.

## Prove that retrying is safe

Locate the failed run's private task record under the configured WebContentAgent root's `runtime/state/chatgpt-bridge`. Prefer a returned `task_file` or `run_id`; otherwise select the newest record that matches the current session and attempted call. Resolve the root through `WCA_PROJECT_ROOT` or the default resolution (the repository containing `integrations/codex-plugin`, then a sibling checkout), as in [local-agent-workflow.md](local-agent-workflow.md). Do not expose the task file or prompt body to the user.

Continue only when the record explicitly shows all three:

```text
status: extension_not_connected
message_submitted: false
zero_submission_proven: true
```

A generic timeout, a missing record, an ambiguous record, or `message_submitted: true` is not proof of zero submission. Older records without `zero_submission_proven: true` are not sufficient either: a claim could race the old timeout handler. In those cases, stop sending and coordinate diagnosis with the designated maintenance task; do not abandon the task or ask the user to reload. If submission was recorded and a stable canonical URL and `submitted_message_id` exist, follow the normal one-shot read-only recovery policy; never resend.

## Repair without losing the request

Keep the original question, session key, canonical conversation URL, attachment path, thinking mode, response expectations, and timeout in the active Codex task. Do not make the user restate or paste them again. Do not put prompt or response bodies in the conversation registry.

When the user has pre-authorized autonomous repair (for example in an optional local preferences file (not committed; see the plugin README)), that replaces the old manual-Reload procedure. Follow [failure-coordination.md](failure-coordination.md): contact the designated maintenance task with the exact failure, private evidence locations, source task identity and verified submission state. Do not ask the user to open `chrome://extensions/`, click Reload, restart Chrome, restate the request or approve routine diagnosis. Do not ask the user to copy a diagnostic block into another Codex task.

The former skill-level blanket prohibition on automating `chrome://extensions/` is retired. When extension maintenance is actually necessary, perform it through available supported, authorized tooling, with one owner and no active conflicting operation. This removes the manual-only requirement; it does not require a reload before routine sends or change the submission/recovery checks below.

Identify the actual failure before choosing a repair: listener availability and ownership, installed entrypoints, Companion connection/version, pairing and task-claim evidence. `extension_not_connected` alone does not prove that the extension is disabled or incorrectly installed. Use the existing supported health, version-handshake and repair mechanisms; coordinate a necessary idle bridge/extension refresh automatically. Do not interrupt a shared active request, erase records, expose pairing secrets, or switch to a second sender. Record a concise diagnosis/repair state in an existing conversation registry entry.

If the available bridge cannot perform the necessary repair, maintenance owns implementing and verifying that capability. A version bump or local test pass alone is not proof that the live incident is fixed. Continue useful independent work while maintenance investigates; do not substitute a manual-Reload question.

## Resume after verified repair

Continue the already authorized call when all of these are true:

- it is in the same active Codex task;
- the exact original call arguments are still available;
- the failed task record proved `message_submitted: false` and `zero_submission_proven: true`;
- maintenance or the originating task verified a concrete repair or availability change and no conflicting live operation;
- the operation was not canceled or replaced by a newer request.

Retry once after that verified change with the preserved arguments and the same `idempotency_key` (or the same automatic fingerprint if no explicit key was supplied). No new user confirmation is required. Do not set `force_send: true` or generate a replacement key. The backend must validate the terminal zero-submission record and absence of conflicting permit/send evidence; see [composer-readiness-and-retry.md](composer-readiness-and-retry.md). If it still returns `duplicate_blocked`, stop sending and return the original run plus the version/state discrepancy to maintenance; do not work around it with a fresh key. A historical `done`, `ready`, or `reloaded` reply may continue an uncanceled request under the same evidence checks, but manual action is no longer a prerequisite.

If the retry again returns `extension_not_connected`, check its new task record, update the registry status, and stop repeated sends. Return the evidence to the designated maintenance task for further diagnosis under [failure-coordination.md](failure-coordination.md). A later continuation needs a newly verified repair or availability change plus the same submission checks; elapsed time alone is insufficient. Preserve ambiguous results as ambiguous. Continue diagnosis without a send loop, new idempotency keys, or asking the user to reload or paste the original request elsewhere.
