"""App-only model choice. Inventory is cheap; loading happens only during a quiz.

The optional override temporarily replaces the idle pinned model, never changes
WebContentAgent's default, and restores the pinned runtime after inference.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from web_content_agent.config import ModelConfig
from web_content_agent.paths import project_root

APP_MODEL_ID = "onepoint3acres-quiz"
MODEL_LOAD_TIMEOUT_SECONDS = 600


def settings_path() -> Path:
    return project_root() / ".local/state/onepoint3acres-model.json"


def selected_model(path: Path | None = None) -> str | None:
    target = path or settings_path()
    if not target.exists():
        return None
    value = json.loads(target.read_text(encoding="utf-8"))["model_key"]
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError("Invalid app model selection; reselect a downloaded model")
    return value


def lms(*args: str, timeout: float = 180) -> str:
    executable = Path.home() / ".lmstudio/bin/lms.exe"
    result = subprocess.run(
        [str(executable), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError(
            f"LM Studio {args[0]} failed: {(result.stderr or result.stdout)[-1500:]}"
        )
    return result.stdout


def installed_models() -> list[dict[str, str]]:
    models = []
    for item in json.loads(lms("ls", "--json", timeout=20)):
        if item.get("type") != "llm":
            continue
        # lms load accepts modelKey, NOT indexedModelIdentifier or @variant keys.
        # Quantization is the inventory's selected variant; verify it after loading.
        quant = item.get("quantization", {}).get(
            "name", "Q2_K_P" if "Q2_K_P" in item.get("path", "") else "file quant"
        )
        models.append(
            {
                "key": item["modelKey"],
                "label": f"{item['displayName']} ({quant})",
                "indexed": item["indexedModelIdentifier"],
                "quant": quant,
            }
        )
    return models


def save_selection(key: str | None, path: Path | None = None) -> None:
    # Accept only concrete, already-downloaded choices, never arbitrary CLI arguments.
    if key is not None and key not in {item["key"] for item in installed_models()}:
        raise ValueError("Select a model already downloaded in LM Studio")
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"model_key": key}, indent=2), encoding="utf-8")
    temporary.replace(target)


@contextmanager
def model_lease(path: Path | None = None) -> Iterator[None]:
    """Cross-process app/experiment lock, released by the OS even after a crash."""
    target = path or project_root() / ".local/state/onepoint3acres-model.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Quiz model is in use by another app run/experiment") from exc
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def loaded_models() -> list[dict[str, Any]]:
    return json.loads(lms("ps", "--json", timeout=20))


def _idle(item: dict[str, Any]) -> bool:
    return item.get("status") == "idle" and item.get("queued") == 0


def _pinned(item: dict[str, Any]) -> bool:
    return (
        item.get("identifier") == "local-agent-vlm"
        and item.get("indexedModelIdentifier") == ModelConfig().model_key
        and item.get("contextLength") == 32768
        and item.get("parallel") == 1
    )


def _restore_pinned() -> None:
    if loaded_models():
        raise RuntimeError("Another model appeared; refusing to overwrite it during restoration")
    # Descendant lms processes can retain inherited PIPE handles after the
    # PowerShell parent times out. A file keeps timeout cleanup bounded while
    # retaining public loader diagnostics, without interrupting model runtimes.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as log:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(project_root() / "scripts/load-model.ps1"),
            ],
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=MODEL_LOAD_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log.seek(0)
        diagnostic = log.read()
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, result.args, output=diagnostic)


@contextmanager
def quiz_model(key: str, output: Callable[[str], None] = lambda _: None) -> Iterator[ModelConfig]:
    """Caller holds model_lease across loading, both inference calls, and restoration.

    Other LM Studio clients do not honor our lock: refuse any busy, multiple, or
    unfamiliar runtime. This is not a global LM Studio reservation mechanism.
    """
    expected = next((item for item in installed_models() if item["key"] == key), None)
    if expected is None:
        raise ValueError("The selected quiz model is no longer installed")
    idle_ttl = (
        ModelConfig().idle_ttl_seconds
        if expected.get("indexed") == ModelConfig().model_key
        else 1200
    )
    before = loaded_models()
    if len(before) > 1 or any(not _idle(item) or not _pinned(item) for item in before):
        raise RuntimeError("LM Studio is busy or has another model; quiz model switch deferred")
    if before and expected.get("indexed") == ModelConfig().model_key:
        lms("server", "start", "--port", "1234", "--bind", "127.0.0.1")
        output("[6/8] 使用答题模型：" + key)
        yield ModelConfig(timeout_seconds=900, max_retries=1)
        return
    restore = bool(before)
    if restore:
        output("[6/8] 暂时卸载空闲的默认模型；答题完成后恢复。")
        lms("unload", "local-agent-vlm")
    try:
        if loaded_models():
            raise RuntimeError("Another model appeared before loading the quiz model")
        lms("server", "start", "--port", "1234", "--bind", "127.0.0.1")
        output("[6/8] 正在加载答题专用模型：" + key)
        lms(
            "load",
            key,
            "--identifier",
            APP_MODEL_ID,
            "--gpu",
            "max",
            "--context-length",
            "32768",
            "--parallel",
            "1",
            "--ttl",
            str(idle_ttl),
            "--no-speculative-draft-mtp",
            "--yes",
            timeout=MODEL_LOAD_TIMEOUT_SECONDS,
        )
        actual = loaded_models()
        if (
            len(actual) != 1
            or actual[0].get("identifier") != APP_MODEL_ID
            or actual[0].get("modelKey") != key
            or actual[0].get("contextLength") != 32768
            or actual[0].get("parallel") != 1
        ):
            raise RuntimeError("LM Studio did not report the sole requested quiz runtime")
        actual_quant = actual[0].get("quantization", {}).get("name", expected.get("quant", ""))
        if expected.get("quant") not in (None, "file quant") and actual_quant != expected["quant"]:
            raise RuntimeError("Loaded quantization differs from LM Studio's selected variant")
        output(
            "[6/8] 已加载："
            + str(actual[0].get("displayName", key))
            + "；量化："
            + str(actual[0].get("quantization", {}).get("name", expected.get("quant")))
        )
        yield ModelConfig(
            identifier=APP_MODEL_ID,
            model_key=str(actual[0].get("indexedModelIdentifier", key)),
            quant=str(actual_quant),
            idle_ttl_seconds=idle_ttl,
            timeout_seconds=900,
            max_retries=1,
        )
    finally:
        current = loaded_models()
        owned = [item for item in current if item.get("identifier") == APP_MODEL_ID]
        if owned:
            if not all(_idle(item) for item in owned):
                raise RuntimeError(
                    "Quiz runtime still busy; left loaded instead of interrupting it"
                )
            lms("unload", APP_MODEL_ID)
        if restore:
            output("[6/8] 正在恢复 WebContentAgent 默认模型……")
            _restore_pinned()
            output("[6/8] 默认模型已恢复。")
