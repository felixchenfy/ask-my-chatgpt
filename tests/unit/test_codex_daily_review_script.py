from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).parents[2] / "scripts/codex-daily-review.py"
SPEC = importlib.util.spec_from_file_location("codex_daily_review_script", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: Windows launcher flags")
def test_ensure_app_restarts_after_connection_reset(monkeypatch, tmp_path):
    launcher = tmp_path / ".venv/Scripts/wca-onepoint3acres-app.exe"
    launcher.parent.mkdir(parents=True)
    launcher.touch()
    calls = iter(
        [
            httpx.ReadError("server closed during shutdown"),
            httpx.ConnectError("not listening yet"),
            {
                "mode": "codex_review",
                "internal_schedule_enabled": False,
            },
        ]
    )

    def request(*_args, **_kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    launched = []
    monkeypatch.setattr(MODULE, "request", request)
    monkeypatch.setattr(MODULE, "project_root", lambda: tmp_path)
    monkeypatch.setattr(MODULE.subprocess, "Popen", lambda *args, **kwargs: launched.append(args))
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    assert MODULE.ensure_app()["mode"] == "codex_review"
    assert len(launched) == 1
