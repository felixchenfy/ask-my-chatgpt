# Design and boundaries

Python owns scope, traversal, file validation, retries, task state and persistence.
The local model owns semantic choices. The ChatGPT route is a separate browser
workflow: it does not call LM Studio and does transmit authorized content remotely.

Collection discovers in-scope links, extracts source evidence, downloads assets,
and writes derived Markdown. Final raw content is immutable; cleanup writes to
cleaned output. Checkpoints and manifests let resumed runs validate completed
items before skipping them. Model requests have an explicit context budget.

MCP is a thin control interface. Tools do not provide arbitrary shell execution,
credential export or unrestricted file writes. PDF uploads enforce real paths,
allowed roots, file type and size. The Chrome bridge binds loopback and pairs
with the extension using a locally generated token.

Submission is a state transition with durable evidence. Ambiguous sends remain
protected against replay. Recovery reads are bound to the intended conversation
and message. Reply extraction and factual correctness are separate obligations.

The optional daily assistant records the exact question and ordered options,
awaits supervisor review, revalidates the page, and consumes a one-use permit.
An attempt or consumed permit does not establish daily completion. Verify the
website result before considering another run; never resubmit a graded question.

Do not bypass login, CAPTCHA, paywalls, DRM or site access restrictions.
