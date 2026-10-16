# Architecture and module communication

Python owns scope, traversal, file validation, retries, task state and persistence.
The local model owns semantic choices only in optional collection workflows. The ChatGPT route is a separate browser
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

## Follow one bridge request

This is the direct Python MCP route documented in the quickstart. The MCP server
and bridge run in the same Python process; the extension initiates HTTP polling.
The optional Node adapter described in the historical blog is a different entry point.

```mermaid
sequenceDiagram
    participant C as Codex / Claude Code
    participant M as Python MCP server
    participant B as ChatGPT bridge
    participant R as Private task records
    participant X as Chrome Companion
    participant G as ChatGPT page
    C->>M: ask_chatgpt(question, optional PDF, conversation URL)
    M->>B: Call bridge function
    B->>B: Validate destination and optional PDF policy
    B->>R: Reserve request and persist task
    X->>B: Poll paired loopback endpoint
    B-->>X: Eligible task
    X->>G: Prepare input and submit once
    X->>B: Report matching submitted message identity
    B->>R: Persist submission evidence
    X->>G: Observe rendered reply for the original message
    X->>X: Check identity, completion and extraction
    X->>B: Return reply and evidence
    B->>R: Persist outcome
    B-->>M: Result, reply and conversation URL
    M-->>C: MCP tool result
```

This diagram shows a successful exchange, not a guarantee of delivery. An
uncertain send remains protected against replay. Eligible extraction failures can
use a bounded read-only recovery tied to the original message; a new send is not
recovery. The reply's factual correctness still needs evaluation.

## Modules and responsibilities

| Module | Responsibility | Communication |
| --- | --- | --- |
| `src/web_content_agent/mcp/server.py` | Expose high-level tools | MCP stdio with the coding client; Python calls internally |
| `src/web_content_agent/chatgpt_chrome_bridge.py` | Validate requests, coordinate tasks and record outcomes | Paired loopback HTTP with Companion; private local state |
| `chrome_extension/` | Operate the signed-in page and collect matching evidence | Poll the bridge, interact with the page, post observations/results |
| Collection controller and Worker | Discover, extract, resume and validate optional collections | Playwright, local files and the LM Studio loopback endpoint |
| External scheduler, if configured | Decide when an authorized agent run starts | Client-specific; not a server-owned timer |

The ChatGPT route does not use LM Studio. The optional collection route is separate:

```mermaid
flowchart LR
    C["MCP client"] --> M["Collection tools"]
    M --> W["Controller and Worker"]
    W <-->|"Semantic choices"| L["LM Studio on loopback"]
    W <-->|"In-scope browsing"| P["Playwright"]
    W --> R["Raw evidence and checkpoints"]
    W --> O["Cleaned Markdown and validation"]
```

## Optional 1point3acres desktop app

`wca-onepoint3acres-app` is a Tk window that owns a second loopback server on
`127.0.0.1:18731`. The same Chrome Companion polls it for commands, reads the
quiz page, and asks the app to solve. A local LM Studio model proposes an answer
with confidence and evidence; `supervision.py` holds it for review and issues a
one-use submission permit; the extension revalidates the page, submits once and
reports the website's verdict, which is journaled.

```mermaid
sequenceDiagram
    participant A as Desktop app (Python)
    participant X as Chrome Companion
    participant S as 1point3acres page
    participant L as LM Studio (local)
    participant R as Supervisor
    A->>A: Queue daily run
    X->>A: Poll /command
    A-->>X: Run with run_id
    X->>S: Read quiz and options
    X->>A: /solve (question, options, evidence)
    A->>L: Ask for answer and confidence
    A->>R: Hold for review
    R->>A: Approve (separate review token)
    X->>A: /review/permit (one use)
    X->>S: Revalidate page and submit once
    X->>A: /complete with website verdict
```

## Local authentication

Both loopback servers bind `127.0.0.1` only. Each requires an `X-WCA-Token`
header whose value is a random per-machine token created by
`chatgpt_pairing.py` and mirrored into an ignored extension file, and each rejects
requests whose `Origin` is not `chrome-extension://…`; CORS responses are only
issued to extension origins. Review decisions on the quiz server need a second,
separate review token. Tokens are never logged or returned by any endpoint.

## Tests

[testing.md](testing.md) lists the offline tests per module and the opt-in live
checks, including `scripts/chatgpt-live-check.py`, which drives the real MCP stdio
server through a new chat, a follow-up and a read-only read.

For the development story, see the
[bridge architecture and failure analysis](https://feiyuchen-blog.pages.dev/blog/codex-talks-to-chatgpt-through-a-local-agent/)
and [local extraction article](https://feiyuchen-blog.pages.dev/blog/local-llm-web-agent/).
The bridge overview adapts the blog's logical component diagram to the current
direct MCP entry point. Dated incidents in the article describe historical versions.
