from pathlib import Path
from subprocess import CompletedProcess

import pytest

from web_content_agent.config import ModelConfig
from web_content_agent.lmstudio import LMStudioClient, ModelServerUnavailable

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def script_text(name: str) -> str:
    return (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_model_loader_and_worker_default_to_five_minute_idle_ttl() -> None:
    script = script_text("load-model.ps1")
    assert "[int]$IdleTtlSeconds = 300" in script
    assert ModelConfig().idle_ttl_seconds == 300
    assert "--identifier local-agent-vlm" in script
    assert "--ttl $IdleTtlSeconds" in script
    assert "$_.ttlMs -eq ($IdleTtlSeconds * 1000)" in script


def test_all_interactive_launchers_reject_wrong_ttl() -> None:
    for name in (
        "start-ui-agent.ps1",
        "start-opencode-vscode.ps1",
        "start-local-agent-vscode.ps1",
    ):
        assert "$_.ttlMs -eq 300000" in script_text(name)


def test_general_vscode_launcher_is_not_bound_to_web_content_workspace() -> None:
    script = script_text("start-local-agent-vscode.ps1")
    assert "[string]$ProjectPath" in script
    assert "FolderBrowserDialog" in script
    assert "WebContentAgent-OpenCode.code-workspace" not in script
    assert "--new-window $ResolvedProject" in script


def test_general_vscode_launcher_validates_global_coding_policy() -> None:
    script = script_text("start-local-agent-vscode.ps1")
    assert "$Resolved.default_agent -ne 'build'" in script
    assert "$Resolved.permission.edit -ne 'allow'" in script
    assert "$Resolved.permission.external_directory -ne 'ask'" in script


def test_worker_wakes_unloaded_model_with_pinned_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LMStudioClient(ModelConfig())
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["ps", "--json"]:
            return CompletedProcess(command, 0, "[]", "")
        return CompletedProcess(command, 0, "loaded", "")

    monkeypatch.setattr(client, "_lms_path", lambda: "lms.exe")
    monkeypatch.setattr("web_content_agent.lmstudio.subprocess.run", fake_run)

    client._ensure_model_loaded()

    assert calls[0] == ["lms.exe", "ps", "--json"]
    assert "load-model.ps1" in calls[1][calls[1].index("-File") + 1]
    assert calls[1][-2:] == ["-IdleTtlSeconds", "300"]


def test_worker_does_not_unload_an_unrelated_active_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LMStudioClient(ModelConfig())

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 0, '[{"identifier":"my-game-model"}]', "")

    monkeypatch.setattr(client, "_lms_path", lambda: "lms.exe")
    monkeypatch.setattr("web_content_agent.lmstudio.subprocess.run", fake_run)

    with pytest.raises(ModelServerUnavailable, match="Refusing to unload"):
        client._ensure_model_loaded()


def test_identifier_metadata_lookup_wakes_model_before_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LMStudioClient(ModelConfig())
    calls: list[str] = []

    monkeypatch.setattr(client, "_ensure_model_loaded", lambda: calls.append("wake"))
    monkeypatch.setattr(
        client,
        "models",
        lambda: [{"id": "local-agent-vlm"}, {"id": "catalog-model"}],
    )

    assert client.actual_model_identifier() == "local-agent-vlm"
    assert calls == ["wake"]


def test_model_inventory_is_rechecked_after_a_loaded_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LMStudioClient(ModelConfig())
    calls: list[list[str]] = []
    inventories = iter(
        [
            '[{"identifier":"local-agent-vlm"}]',
            "[]",
        ]
    )

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["ps", "--json"]:
            return CompletedProcess(command, 0, next(inventories), "")
        return CompletedProcess(command, 0, "loaded", "")

    monkeypatch.setattr(client, "_lms_path", lambda: "lms.exe")
    monkeypatch.setattr("web_content_agent.lmstudio.subprocess.run", fake_run)

    client._ensure_model_loaded()
    client._ensure_model_loaded()

    assert [command[1:3] for command in calls].count(["ps", "--json"]) == 2
    assert any("load-model.ps1" in " ".join(command) for command in calls)
