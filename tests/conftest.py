from __future__ import annotations

import pytest

# The optional 1point3acres bridge reads a per-install token from runtime/state.
# Tests use a fixed fake token so they never create or read a real pairing file.
TEST_QUIZ_BRIDGE_TOKEN = "ab" * 32


@pytest.fixture(autouse=True)
def _fake_quiz_bridge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from web_content_agent.onepoint3acres import chrome_bridge

    monkeypatch.setattr(chrome_bridge, "bridge_token", lambda: TEST_QUIZ_BRIDGE_TOKEN)
