from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from web_content_agent.checkpoint import atomic_write_json
from web_content_agent.config import load_source_config
from web_content_agent.paths import default_workspace_root, project_root, runtime_root
from web_content_agent.run_manifest import make_run_id
from web_content_agent.worker import run_worker

from .task_spec import ProcessRecord

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,159}$")


def _is_within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def require_workspace_path(path: str | Path, *, must_exist: bool = True) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not _is_within(resolved, default_workspace_root()):
        raise ValueError(f"Path is outside the WebContent workspace: {resolved}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(resolved)
    return resolved


def require_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid run_id")
    return run_id


def find_run(run_id: str) -> Path:
    require_run_id(run_id)
    matches = list(default_workspace_root().glob(f"sites/*/*/runs/{run_id}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one run named {run_id}; found {len(matches)}")
    return require_workspace_path(matches[0])


def _registry_dir() -> Path:
    path = runtime_root() / "state" / "mcp-processes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def registry_path(run_id: str) -> Path:
    return _registry_dir() / f"{require_run_id(run_id)}.json"


def read_process(run_id: str) -> ProcessRecord | None:
    path = registry_path(run_id)
    if not path.exists():
        return None
    return ProcessRecord.model_validate_json(path.read_text(encoding="utf-8"))


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0 and f'"{pid}"' in result.stdout


def _unique_run_id(name: str, collection_dir: Path) -> str:
    base = make_run_id(name)
    candidate = base
    counter = 2
    while (collection_dir / "runs" / candidate).exists() or registry_path(candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def spawn_worker(
    source_config: str | Path, *, run_id: str | None = None, resume: bool = False
) -> ProcessRecord:
    source = require_workspace_path(source_config)
    if source.name.lower() != "source.yaml":
        raise ValueError("source_config must point to a collection source.yaml")
    config = load_source_config(source)
    collection_dir = require_workspace_path(config.resolved_collection_dir())
    selected_run_id = (
        require_run_id(run_id) if run_id else _unique_run_id(config.name, collection_dir)
    )
    existing = read_process(selected_run_id)
    if existing and process_is_alive(existing.pid):
        raise RuntimeError(f"Run {selected_run_id} already has a live worker")

    logs = runtime_root() / "logs" / "mcp-workers"
    logs.mkdir(parents=True, exist_ok=True)
    stdout_path = logs / f"{selected_run_id}.stdout.log"
    stderr_path = logs / f"{selected_run_id}.stderr.log"
    args = [
        sys.executable,
        "-m",
        "web_content_agent.mcp.process_manager",
        "worker",
        "--source",
        str(source),
        "--run-id",
        selected_run_id,
    ]
    if resume:
        args.append("--resume")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    with stdout_path.open("ab") as stdout_handle, stderr_path.open("ab") as stderr_handle:
        process = subprocess.Popen(
            args,
            cwd=project_root(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            close_fds=True,
            creationflags=creationflags,
        )
    record = ProcessRecord(
        run_id=selected_run_id,
        pid=process.pid,
        mode="resume" if resume else "run",
        source_config=str(source),
        started_at=datetime.now().astimezone().isoformat(),
        stdout_log=str(stdout_path),
        stderr_log=str(stderr_path),
    )
    atomic_write_json(registry_path(selected_run_id), record.model_dump(mode="json"))
    return record


def _worker_main(source: str, run_id: str, resume: bool) -> int:
    source_path = require_workspace_path(source)
    config = load_source_config(source_path)
    if resume:
        find_run(run_id)
    stop_after_value = os.getenv("WCA_MCP_TEST_STOP_AFTER")
    stop_after = int(stop_after_value) if stop_after_value else None
    if stop_after is not None and stop_after not in {1, 2, 3}:
        raise ValueError("WCA_MCP_TEST_STOP_AFTER must be 1, 2, or 3")
    run_worker(config, run_id=require_run_id(run_id), stop_after=stop_after)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Internal detached WebContentAgent worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker = subparsers.add_parser("worker")
    worker.add_argument("--source", required=True)
    worker.add_argument("--run-id", required=True)
    worker.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "worker":
        raise SystemExit(_worker_main(args.source, args.run_id, args.resume))


if __name__ == "__main__":
    main()
