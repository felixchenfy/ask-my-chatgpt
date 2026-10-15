from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from web_content_agent.config import ModelConfig
from web_content_agent.onepoint3acres import model_selection as selection


def test_inventory_uses_cli_model_keys_not_indexed_paths_or_variant_keys(monkeypatch):
    monkeypatch.setattr(
        selection,
        "lms",
        lambda *a, **kw: json.dumps(
            [
                {"type": "embedding"},
                {
                    "type": "llm",
                    "displayName": "Qwen",
                    "variants": ["qwen@q4", "qwen@q6"],
                    "modelKey": "qwen",
                    "indexedModelIdentifier": "qwen/file.gguf",
                },
                {
                    "type": "llm",
                    "displayName": "Other",
                    "modelKey": "other",
                    "indexedModelIdentifier": "other/file.gguf",
                    "quantization": {"name": "Q4"},
                },
            ]
        ),
    )
    assert [item["key"] for item in selection.installed_models()] == [
        "qwen",
        "other",
    ]


def test_selection_roundtrip_and_invalid_choice_preserves_previous(tmp_path, monkeypatch):
    path = tmp_path / "model.json"
    monkeypatch.setattr(selection, "installed_models", lambda: [{"key": "model@q4"}])
    assert selection.selected_model(path) is None
    selection.save_selection("model@q4", path)
    assert selection.selected_model(path) == "model@q4"
    with pytest.raises(ValueError):
        selection.save_selection("--download-anything", path)
    assert selection.selected_model(path) == "model@q4"
    selection.save_selection(None, path)
    assert selection.selected_model(path) is None


def test_corrupt_selection_does_not_silently_fall_back(tmp_path):
    path = tmp_path / "model.json"
    path.write_text('{"model_key":42}')
    with pytest.raises(ValueError):
        selection.selected_model(path)


def test_lease_rejects_concurrent_experiment_and_releases_after_error(tmp_path):
    path = tmp_path / "model.lock"
    with pytest.raises(ValueError), selection.model_lease(path):
        with pytest.raises((RuntimeError, BlockingIOError)), selection.model_lease(path):
            pytest.fail("concurrent lease was allowed")
        raise ValueError("test failure")
    with selection.model_lease(path):
        pass


def pinned():
    return {
        "identifier": "local-agent-vlm",
        "indexedModelIdentifier": ModelConfig().model_key,
        "contextLength": 32768,
        "parallel": 1,
        "status": "idle",
        "queued": 0,
    }


def fake_runtime(monkeypatch, initial):
    state = list(initial)
    calls = []
    monkeypatch.setattr(selection, "installed_models", lambda: [{"key": "test-model"}])
    monkeypatch.setattr(selection, "loaded_models", lambda: list(state))

    def command(*args, **kwargs):
        calls.append(args)
        if args[0] == "unload":
            state[:] = [item for item in state if item["identifier"] != args[1]]
        elif args[0] == "load":
            assert not state, "must never load two models"
            state.append(
                {
                    "identifier": selection.APP_MODEL_ID,
                    "status": "idle",
                    "queued": 0,
                    "modelKey": "test-model",
                    "contextLength": 32768,
                    "parallel": 1,
                }
            )
        return ""

    def restore():
        assert not state
        calls.append(("restore",))
        state.append(pinned())

    monkeypatch.setattr(selection, "lms", command)
    monkeypatch.setattr(selection, "_restore_pinned", restore)
    return state, calls


@pytest.mark.parametrize("fail", [False, True])
def test_quiz_model_restores_idle_default_even_after_solver_error(monkeypatch, fail):
    state, calls = fake_runtime(monkeypatch, [pinned()])
    try:
        with selection.quiz_model("test-model") as config:
            assert config.identifier == selection.APP_MODEL_ID
            assert config.base_url == "http://127.0.0.1:1234/v1"
            assert config.context_length == 32768
            assert state[0]["identifier"] == selection.APP_MODEL_ID
            if fail:
                raise ValueError("model generated invalid JSON")
    except ValueError:
        assert fail
    assert state == [pinned()]
    assert calls[0] == ("unload", "local-agent-vlm")
    assert calls[-2:] == [("unload", selection.APP_MODEL_ID), ("restore",)]


def test_no_default_loaded_before_means_no_default_loaded_after(monkeypatch):
    state, calls = fake_runtime(monkeypatch, [])
    with selection.quiz_model("test-model"):
        pass
    assert not state
    assert ("restore",) not in calls


@pytest.mark.parametrize("is_pinned, expected_ttl", [(True, 300), (False, 1200)])
def test_app_cold_load_uses_new_qwen_ttl_but_keeps_other_models_unchanged(
    monkeypatch, is_pinned, expected_ttl
):
    _, calls = fake_runtime(monkeypatch, [])
    monkeypatch.setattr(
        selection,
        "installed_models",
        lambda: [
            {"key": "test-model", "indexed": ModelConfig().model_key if is_pinned else "other"}
        ],
    )
    with selection.quiz_model("test-model") as config:
        assert config.idle_ttl_seconds == expected_ttl
    load = next(call for call in calls if call[0] == "load")
    assert load[load.index("--ttl") + 1] == str(expected_ttl)


@pytest.mark.parametrize(
    "initial",
    [
        [dict(pinned(), status="generating")],
        [dict(pinned(), queued=1)],
        [dict(pinned(), identifier="someone-else")],
        [pinned(), pinned()],
    ],
)
def test_never_unloads_busy_or_unfamiliar_models(monkeypatch, initial):
    _, calls = fake_runtime(monkeypatch, initial)
    with pytest.raises(RuntimeError, match="busy or has another model"):
        with selection.quiz_model("test-model"):
            pytest.fail("unsafe switch")
    assert calls == []


def test_load_failure_restores_default(monkeypatch):
    state, calls = fake_runtime(monkeypatch, [pinned()])
    real_command = selection.lms

    def broken(*args, **kwargs):
        if args[0] == "load":
            raise RuntimeError("out of memory")
        return real_command(*args, **kwargs)

    monkeypatch.setattr(selection, "lms", broken)
    with pytest.raises(RuntimeError, match="out of memory"):
        with selection.quiz_model("test-model"):
            pass
    assert state == [pinned()]
    assert calls[-1] == ("restore",)


def test_quiz_model_allows_slow_lm_studio_load(monkeypatch):
    state = []
    calls = []
    monkeypatch.setattr(selection, "installed_models", lambda: [{"key": "test-model"}])
    monkeypatch.setattr(selection, "loaded_models", lambda: list(state))

    def command(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "load":
            state.append(
                {
                    "identifier": selection.APP_MODEL_ID,
                    "status": "idle",
                    "queued": 0,
                    "modelKey": "test-model",
                    "contextLength": 32768,
                    "parallel": 1,
                }
            )
        elif args[0] == "unload":
            state.clear()
        return ""

    monkeypatch.setattr(selection, "lms", command)
    with selection.quiz_model("test-model"):
        pass

    load = next((args, kwargs) for args, kwargs in calls if args[0] == "load")
    assert load[1]["timeout"] == selection.MODEL_LOAD_TIMEOUT_SECONDS == 600


def test_busy_runtime_after_timeout_is_not_force_unloaded(monkeypatch):
    state, calls = fake_runtime(monkeypatch, [pinned()])
    with pytest.raises(RuntimeError, match="still busy"):
        with selection.quiz_model("test-model"):
            state[0]["status"] = "generating"
    assert state[0]["identifier"] == selection.APP_MODEL_ID
    assert ("restore",) not in calls


def test_restore_uses_bounded_file_output_and_preserves_failure(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(selection, "loaded_models", lambda: [])

    def failed_loader(args, **kwargs):
        assert kwargs["timeout"] == selection.MODEL_LOAD_TIMEOUT_SECONDS
        assert "capture_output" not in kwargs
        assert kwargs["stderr"] == subprocess.STDOUT
        kwargs["stdout"].write("public loader diagnostic")
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=7, args=args)

    monkeypatch.setattr(selection.subprocess, "run", failed_loader)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        selection._restore_pinned()
    assert exc.value.output == "public loader diagnostic"


def test_restore_timeout_does_not_wait_for_descendant_output_pipe(monkeypatch):
    import subprocess

    monkeypatch.setattr(selection, "loaded_models", lambda: [])

    def timed_out(args, **kwargs):
        assert kwargs["stdout"] != subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(selection.subprocess, "run", timed_out)
    with pytest.raises(subprocess.TimeoutExpired):
        selection._restore_pinned()


def test_app_override_uses_its_model_without_inheriting_global_env(monkeypatch):
    from web_content_agent.onepoint3acres import runtime
    from web_content_agent.onepoint3acres.models import DailyQuiz, QuizAnswer

    monkeypatch.setenv("LM_STUDIO_MODEL", "global-model")
    monkeypatch.setattr(runtime, "selected_model", lambda: "test-model")
    stages = []

    @contextmanager
    def lease():
        stages.append("lease")
        yield

    @contextmanager
    def model(key, output):
        assert key == "test-model"
        stages.append("load")
        yield ModelConfig(identifier=selection.APP_MODEL_ID)
        stages.append("restore")

    monkeypatch.setattr(runtime, "model_lease", lease)
    monkeypatch.setattr(runtime, "quiz_model", model)

    class Solver:
        def __init__(self, client):
            assert client.config.identifier == selection.APP_MODEL_ID

        def solve(self, quiz):
            stages.append("solve")
            return QuizAnswer(choice_index=1, choice_text="A", confidence=0.5, reason="test")

    monkeypatch.setattr(runtime, "QuizSolver", Solver)
    solver = runtime.create_solver()
    assert not stages  # constructing the solver must not load anything
    solver.solve(
        DailyQuiz(
            question="test?",
            choices=[{"index": 1, "text": "A"}, {"index": 2, "text": "B"}],
            source_url="",
        )
    )
    assert stages == ["lease", "load", "solve", "restore"]


def test_experiment_label_is_separate_from_quiz_and_evidence():
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/onepoint3acres/points-quiz-model-comparison.json"
        ).read_text(encoding="utf-8")
    )
    assert "expected_choice_index" not in fixture["quiz"]
    assert "以上都对" not in fixture["evidence"]
    assert fixture["expected_choice_index"] == 1


def test_model_selector_populates_saves_and_disables_during_run(monkeypatch, tmp_path):
    import tkinter as tk

    from web_content_agent import onepoint3acres_app as gui

    class Server:
        def __init__(self, *args, **kwargs):
            pass

        def serve_forever(self):
            pass

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    saved = []
    monkeypatch.setattr(gui, "BridgeServer", Server)
    monkeypatch.setattr(gui, "project_root", lambda: tmp_path)
    monkeypatch.setattr(gui, "installed_models", lambda: [{"key": "model", "label": "Test Q4"}])
    monkeypatch.setattr(gui, "selected_model", lambda: "model")
    monkeypatch.setattr(gui, "save_selection", saved.append)
    monkeypatch.setattr(gui.threading, "Thread", ImmediateThread)
    root = tk.Tk()
    root.withdraw()
    try:
        app = gui.OnePointThreeAcresApp(root)
        app._drain_events()
        assert app.model_text.get() == "Test Q4"
        assert str(app.model_combo.cget("state")) == "readonly"
        app.model_text.set("WebContentAgent 默认模型")
        app._select_model()
        assert saved == [None]
        app.running = True
        app._select_model()
        assert saved == [None]
        app.events.put(("models", ([{"key": "model", "label": "Test Q4"}], "model")))
        app._drain_events()
        assert str(app.model_combo.cget("state")) == "disabled"
    finally:
        root.destroy()
