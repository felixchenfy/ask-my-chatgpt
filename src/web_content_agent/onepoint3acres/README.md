# Extra feature: 1point3acres daily assistant

**This is an optional extra. It is not part of the ChatGPT bridge**, and the
bridge never imports or starts it. Nothing here runs unless you start the
desktop app yourself.

It is a small Windows desktop app for the daily check-in quiz on 1point3acres,
using your own signed-in account. A local model proposes an answer with evidence,
a supervisor approves it, the Chrome Companion submits **once**, and the
website's response is recorded. It never bypasses login or CAPTCHA.

## Where its pieces live

All 1point3acres code is in these places. Anything not listed belongs to the
core bridge or the collector.

| Area | Files |
| --- | --- |
| Python package | `src/web_content_agent/onepoint3acres/` (this folder) |
| Desktop app and daily runner | `src/web_content_agent/onepoint3acres_app.py`, `src/web_content_agent/onepoint3acres_daily.py` |
| Command | `wca-onepoint3acres-app` (defined in `pyproject.toml`) |
| Chrome Companion | `checkin-state.js`, `checkin-target.js`, `google-search.js`, `quiz-solve.js`, `quiz-outcome.js`, `trusted-input.js`, `input-diagnostics.js`, generated `quiz-pairing.js` — see [`chrome_extension/README.md`](../../../chrome_extension/README.md) |
| Scripts | the "Extra" group in [`scripts/README.md`](../../../scripts/README.md) |
| Tests | `tests/unit/test_onepoint3acres_daily.py`, `tests/unit/test_quiz_*.py`, `tests/unit/test_chrome_input_diagnostics.py`, `tests/js/onepoint3acres-*.test.mjs`, `tests/fixtures/onepoint3acres/` |

## How it stays separate

- **Own local server:** `127.0.0.1:18731` (the ChatGPT bridge uses `18732`).
  It accepts only requests from the Chrome Companion's `chrome-extension://`
  origin with its own pairing token.
- **Own pairing token:** `runtime/state/quiz-pairing.json`. It is created the
  first time the app runs. Without it, the extension's quiz lane stays inactive.
- **Extension permissions:** the Companion's access to `www.1point3acres.com` and
  `www.google.com` exists only for this extra. The ChatGPT bridge needs only
  `chatgpt.com`.

## Not using it

You don't have to do anything: if you never run `wca-onepoint3acres-app`, it never
starts, never pairs and never touches 1point3acres. Its tests still run with the
offline suite so that changes to shared extension code don't break it.
