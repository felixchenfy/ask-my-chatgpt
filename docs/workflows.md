# Project reports, fresh ideas and progress summaries

The most useful pattern is one ChatGPT conversation per project. Your coding
agent sends concise evidence-based updates; ChatGPT offers a second perspective;
you return to that conversation when you want the bigger picture.

These are workflow recipes, not features that install themselves. WebContentAgent
provides communication. Your coding client decides what to send, a separately
configured scheduler decides when to run, and you define the permitted scope.

## Start a project conversation

Give Codex or Claude Code this instruction after setup:

```text
Use WebContentAgent to start a ChatGPT review conversation for this project.
Prepare a concise report from the current repository and verified test results:
- the goal and intended users;
- what is implemented and what changed;
- tests actually run, their results and untested areas;
- current blockers and decisions;
- the proposed next step.
Send the approved report with ask_chatgpt, using thinking_mode="current".
Ask ChatGPT for alternative approaches, weak assumptions and one useful experiment.
Save the returned conversation URL and a brief update checkpoint in ignored local
project state. Bring back the reply, then explain what you accept or reject and why.
Do not include credentials, private customer data or unrelated source files.
```

Ask the agent to show the proposed report first when you want per-send review.
If you prefer recurring authorized reporting, define the scope once in your
client/scheduler instructions. This repository does not grant that consent for you.

## Continue the same project

```text
Read the saved project-review conversation URL and the last report checkpoint.
Summarize meaningful changes since that report. Continue that exact conversation
with ask_chatgpt rather than starting another chat. Ask whether the new evidence
changes the previous recommendations. Return the feedback with links or local
references to the tests and changes that support your conclusions.
```

Store the URL and checkpoint outside version control. A report should say when
its evidence was collected. Do not mark it delivered until the bridge confirms
the intended exchange; record uncertain delivery separately.

## Configure periodic reports

After a manual exchange succeeds, configure a recurring task in your coding client
or an external scheduler. Choose a cadence that fits the project, such as once
per workday. The scheduled task must run on the machine with this MCP server and
signed-in Chrome session, with permission to inspect the intended project.

Use this as the task body, after replacing the project and private state locations:

```text
Review this project's current state and the last saved report checkpoint.
This recurring task is authorized to send concise project progress reports to
the saved ChatGPT project conversation using WebContentAgent.

If there is a meaningful implementation change, test result, blocker or decision:
1. Prepare a dated report of goals, changes, evidence, unknowns and next steps.
2. Continue the saved conversation with ask_chatgpt, thinking_mode="current".
3. Ask for new ideas, missing assumptions and the highest-value next experiment.
4. Save the confirmed report checkpoint and returned feedback privately.
5. Summarize the actionable feedback for me. Treat suggestions as proposals;
   test them before adopting changes that affect correctness or permissions.

If nothing meaningful changed, skip sending and stay quiet. Do not overlap another
reporting run. If a previous send is uncertain, diagnose its existing state before
any new send; do not create a fresh request to evade duplicate protection.
Never include secrets or unrelated files. Notify me if an account or permission
problem requires my action.
```

This recipe is not a built-in timer, unattended-service guarantee or installed
automation. The computer and client must remain available; authentication,
account limits, approval requirements and website changes can interrupt a run.
The optional forum assistant's schedule is a separate workflow.

## Get a progress summary in ChatGPT

Open the saved conversation yourself and ask:

```text
Based on the project reports in this conversation, summarize:
1. the project goal;
2. what is completed and supported by tests;
3. what is still in progress or unverified;
4. important decisions and their reasons;
5. blockers and the next three priorities.
State the date of the latest report. Separate reported facts from your suggestions,
and point out missing information instead of assuming the repository has changed.
```

You can also have your coding agent send that question through `ask_chatgpt` in
the same conversation. Reading an existing reply is different:
`read_chatgpt_conversation` does not generate a fresh summary or inspect the repository.

The conversation is convenient shared context, not a synchronized project database.
For long-running projects, ask for a dated recap and retain authoritative decisions
and tests in the project itself. A saved URL does not guarantee perfect recall.

## Review a PDF or a design decision

After configuring the PDF allowlist:

```text
Send the approved architecture PDF to our existing ChatGPT project conversation.
Ask for contradictions, missing requirements and two alternative designs with
tradeoffs. Return the review and distinguish actionable findings from speculation.
```

Keep reports focused. Sending the entire codebase is not required for useful
feedback, and a confident response is not a substitute for verification.
