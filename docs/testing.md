# Testing guide

Every public entry point has offline tests: the 11 MCP tools, the `wca` CLI, the
two loopback HTTP servers used by the Chrome Companion, and the extension's
JavaScript. Offline tests need no ChatGPT account, LM Studio model or network.

## Run the checks

```powershell
uv sync --python 3.12 --frozen
$env:PYTHONUTF8 = '1'
$env:PYTEST_ADDOPTS = '-m "not live and not lmstudio"'
.\scripts\test.ps1
```

`test.ps1` runs, in order: `ruff format --check`, `ruff check`, `pytest`, and
`node --test tests/js/*.test.mjs` (Node.js 22+), then `npm test` and `npm run lint`
in `integrations/codex-plugin` when its `node_modules` exists (run `npm ci` there
first; otherwise the plugin step is skipped with a warning). CI installs the plugin
dependencies and runs the same script on `windows-latest` for every push and pull
request.

Useful variations:

```powershell
uv run pytest tests/unit/test_api_contracts.py -q     # one file
uv run pytest -k chatgpt -q                           # by keyword
node --test tests/js/chatgpt-text.test.mjs            # one extension file
```

On macOS/Linux the same `uv run pytest` and `node --test` commands work; four
Windows-only tests are skipped there, and the desktop-app tests need Tk.

## Opt-in live checks

These touch real services and never run in CI.

| Command | What it does |
| --- | --- |
| `uv run python scripts/chatgpt-live-check.py --confirm-send` | Starts `wca-mcp` as a real MCP stdio client would, sends a synthetic question to a **new** ChatGPT chat, sends a follow-up to the **same** chat, then reads the reply back read-only. Stops at the first failure and never resends. |
| `uv run pytest -m lmstudio` | Talks to a loaded LM Studio model on `127.0.0.1:1234`. |
| `uv run pytest -m live` | Fetches a configured public website. |

Conversation URLs are private; the live-check script hides them unless you pass
`--show-url`.

## Test inventory

Counts are test cases (parametrized cases counted individually) at the release
checkpoint in [release-notes.md](release-notes.md).

### Public API contracts

| File | Cases | Covers |
| --- | ---: | --- |
| `tests/unit/test_api_contracts.py` | 48 | Each MCP tool forwards its arguments; documented defaults; `discover_site` URL safety, robots.txt and in-scope candidates; `start_collection`, `resume_run`, `validate_run`, polling cooldown and run-id validation; CLI `--help`, `new-source`, `status`, `validate`; ChatGPT bridge HTTP auth and 404s; quiz bridge per-install token, CORS limited to the extension, control routes; live-check script refuses to send without confirmation |
| `tests/integration/test_mcp_server.py` | 1 | Spawns the real stdio server, checks the exact tool list (no shell tool) and calls `list_collections` |

### ChatGPT bridge (Python)

| File | Cases | Covers |
| --- | ---: | --- |
| `test_chatgpt_chrome_bridge.py` | 31 | Request validation, task lifecycle, URL canonicalization, PDF policy, response verification |
| `test_chatgpt_zero_submission_retry.py` | 39 | When a retry is allowed (proven zero submission) and when it must stay blocked |
| `test_chatgpt_durable_idempotency.py` | 15 | Duplicate protection survives restarts and days; pairing file handling; busy port is reported, not queued |
| `test_chatgpt_queued_attachment.py` | 29 | Same-operation PDF continuation without re-sending text |
| `test_chatgpt_review_regressions.py` | 20 | Pairing, HTTP origin/token rejection, reviewed regressions |
| `test_chatgpt_mode_correction.py` | 14 | Audited thinking-mode correction cannot submit or bypass cooldown |
| `test_chatgpt_bridge_ownership.py` | 11 | Exclusive loopback port ownership and admission |
| `test_chatgpt_project_routes.py` | 10 | ChatGPT project URLs map to one conversation |
| `test_chatgpt_composer_diagnostic.py` | 3 | Read-only composer diagnostics |

### Integrations

| File | Cases | Covers |
| --- | ---: | --- |
| `tests/unit/test_claude_skill_paths.py` | 3 | Claude Code skill `gpt.py` finds the enclosing checkout and the plugin adapter; env overrides win; a copied skill refuses to start the adapter without `WCA_PROJECT_ROOT` |
| `integrations/codex-plugin/tests/*.test.mjs` | 260 | Codex plugin (`npm test`): MCP tool catalog, `.mcp.json`/launcher routing, WebContentAgent root resolution, cooperative cancellation over a real child process, cache repair, real loopback HTTP bridge fixture, and Companion DOM contracts (submission confirmation, attachments, citations, prompt boundaries, render lease) in an offline Chromium |

Counts measured on Windows, 2026-10-15 (484 Python passed with 6 live/model tests
deselected; 182 Companion and 260 plugin Node tests passed).

### Chrome Companion (JavaScript, `tests/js/`)

| Files | Covers |
| --- | --- |
| `chatgpt-submission-workflow`, `chatgpt-execution-boundary`, `chatgpt-regressions` | Submit-once workflow, confirmation of the submitted message, regression fixtures |
| `chatgpt-composer-readiness`, `chatgpt-composer-diagnostic`, `chatgpt-thinking-mode` | Finding a ready composer, diagnostics, model/thinking selection |
| `chatgpt-text`, `chatgpt-render-recovery`, `chatgpt-tabs`, `tab-load` | Reply text extraction and normalization, bounded render recovery, tab selection and loading |
| `onepoint3acres-*` (13 files) | Quiz extraction, solving request, trusted input, review flow, outcome and daily completion detection, Google evidence, version sync |

### Collector

| File | Cases | Covers |
| --- | ---: | --- |
| `test_selection_utils.py` | 11 | Link candidates, slugs, model-guided selection |
| `test_extraction_fallbacks.py` | 3 | Extraction fallbacks when the primary extractor fails |
| `test_image_download.py` | 4 | Image download and cleanup |
| `test_checkpoint_logging_markdown.py` | 6 | Atomic checkpoints, redacted logging, Markdown output |
| `test_failure_bundle.py` | 1 | Redacted failure bundles |
| `test_context_budget.py` | 5 | Model context budget |
| `test_model_lifecycle_scripts.py` | 8 | LM Studio load/unload helpers |
| `test_mcp_tools.py` | 5 | Workspace-scoped collections, status and failure summaries, MCP config merge |
| `test_paths_config.py` | 8 | Desktop/workspace paths, config loading, localhost-only check |
| `test_access_pacing.py`, `test_text_cleanup.py`, `test_monitor_script.py` | 4 | Request pacing, text cleanup, monitor script |
| `tests/integration/test_static_pipeline.py`, `test_course_pipeline.py`, `test_resume_pipeline.py` | 4 | End-to-end pipelines against local fixtures with a fake model |

### 1point3acres assistant (extra feature)

| File | Cases | Covers |
| --- | ---: | --- |
| `test_onepoint3acres_daily.py` | 48 | Home/quiz parsing, workflow, desktop app wiring, scheduler, HTTP status/command/run |
| `test_quiz_supervision.py` | 48 | Supervisor review, one-use permits, restart behaviour, HTTP review authorization |
| `test_quiz_records.py` | 32 | Journal records and daily completion |
| `test_quiz_model_selection.py` | 21 | Installed-model selection and persistence |
| `test_quiz_recovery.py` | 17 | Recovery after interrupted runs |
| `test_quiz_output_format.py` | 14 | Strict model output parsing |
| `test_quiz_knowledge.py` | 13 | Local knowledge notes |
| `test_quiz_web_evidence.py` | 3 | Web evidence filtering |
| `test_chrome_input_diagnostics.py` | 4 | Diagnostic server cannot dispatch real runs |
| `test_codex_daily_review_script.py` | 1 | Review script relaunch behaviour (Windows) |

## Writing a new test

- Put it at the lowest useful layer and use synthetic data only: no real
  conversation URLs, tokens, account pages or private PDFs.
- Use `tmp_path` for state; set `WCA_DESKTOP_PATH` to a temp folder for collector tests.
- HTTP tests bind port `0`. The quiz bridge token is replaced by a fake one in
  `tests/conftest.py`, so tests never read or create a real pairing file.
- Never weaken a send-once, identity or path guard to make a test pass.
