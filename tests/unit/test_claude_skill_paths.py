"""Path resolution of the Claude Code skill CLI (integrations/claude-skill/chatgpt-bridge).

These tests only import gpt.py; they never start the adapter or contact ChatGPT.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_SCRIPT = REPO / "integrations" / "claude-skill" / "chatgpt-bridge" / "scripts" / "gpt.py"
ADAPTER = REPO / "integrations" / "codex-plugin" / "scripts" / "ask-chatgpt-adapter.py"
OVERRIDES = ("WCA_PROJECT_ROOT", "CHATGPT_BRIDGE_SOURCE", "WCA_PYTHON_EXECUTABLE")


def _load(script: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"gpt_under_test_{id(script)}", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clear_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OVERRIDES:
        monkeypatch.delenv(name, raising=False)


def test_in_repo_skill_resolves_enclosing_checkout_and_plugin_adapter() -> None:
    gpt = _load(SKILL_SCRIPT)
    assert gpt.WCA_ROOT == REPO
    assert gpt.ADAPTER == ADAPTER
    assert ADAPTER.is_file()
    venv_python = ".venv/Scripts/python.exe" if gpt.os.name == "nt" else ".venv/bin/python"
    assert gpt.WCA_PYTHON == REPO / venv_python


def test_environment_overrides_take_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WCA_PROJECT_ROOT", str(tmp_path / "wca"))
    monkeypatch.setenv("CHATGPT_BRIDGE_SOURCE", str(tmp_path / "plugin"))
    monkeypatch.setenv("WCA_PYTHON_EXECUTABLE", str(tmp_path / "python.exe"))
    gpt = _load(SKILL_SCRIPT)
    assert gpt.WCA_ROOT == (tmp_path / "wca").resolve()
    assert gpt.ADAPTER == tmp_path / "plugin" / "scripts" / "ask-chatgpt-adapter.py"
    assert gpt.WCA_PYTHON == tmp_path / "python.exe"


def test_copied_skill_needs_configuration_and_never_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copied = tmp_path / "skills" / "chatgpt-bridge" / "scripts" / "gpt.py"
    copied.parent.mkdir(parents=True)
    shutil.copyfile(SKILL_SCRIPT, copied)
    gpt = _load(copied)
    assert gpt.WCA_ROOT is None
    assert gpt.ADAPTER is None

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the adapter must not start without a checkout")

    monkeypatch.setattr(gpt.subprocess, "Popen", forbidden)
    with pytest.raises(SystemExit, match="WCA_PROJECT_ROOT"):
        gpt._run_adapter({"question": "never sent"})

    monkeypatch.setenv("WCA_PROJECT_ROOT", str(REPO))
    configured = _load(copied)
    assert configured.WCA_ROOT == REPO
    assert configured.ADAPTER == ADAPTER
