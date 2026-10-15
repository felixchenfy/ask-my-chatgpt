from __future__ import annotations

import argparse
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from web_content_agent.checkpoint import atomic_write_json
from web_content_agent.paths import project_root

SERVER_NAME = "web-content-agent"


def default_mcp_config() -> Path:
    return Path.home() / ".lmstudio" / "mcp.json"


def default_bionic_mcp_config() -> Path:
    return Path.home() / ".lmstudio" / "apps" / "bionic" / ".internal" / "ng-mcp.json"


def server_entry() -> dict:
    python = project_root() / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        raise FileNotFoundError(f"Project Python is missing: {python}")
    return {
        "command": str(python),
        "args": ["-m", "web_content_agent.mcp.server"],
        "env": {"PYTHONUNBUFFERED": "1"},
    }


def bionic_server_entry(*, server_id: str | None = None) -> dict:
    entry = server_entry()
    return {
        "id": server_id or str(uuid.uuid4()),
        "name": "WebContentAgent",
        "enabled": True,
        "connection": {
            "type": "stdio",
            "command": entry["command"],
            "args": entry["args"],
            "env": {},
            "cwd": str(project_root()),
        },
    }


def update_mcp_config(path: Path, *, enabled: bool) -> tuple[Path | None, dict]:
    path = path.expanduser().resolve()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("LM Studio mcp.json must contain a JSON object")
    else:
        data = {}
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be a JSON object")
    before = json.dumps(data, sort_keys=True)
    if enabled:
        servers[SERVER_NAME] = server_entry()
    else:
        servers.pop(SERVER_NAME, None)
    after = json.dumps(data, sort_keys=True)
    backup = None
    if before != after:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            backup = path.with_name(f"mcp.json.backup-{stamp}")
            shutil.copy2(path, backup)
        atomic_write_json(path, data)
    return backup, data


def update_bionic_mcp_config(path: Path, *, enabled: bool) -> tuple[Path | None, dict]:
    """Merge the Bionic/LM Studio 1.x Connected Apps registry without touching other MCPs."""
    path = path.expanduser().resolve()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Bionic ng-mcp.json must contain a JSON object")
    else:
        data = {"servers": []}
    servers = data.setdefault("servers", [])
    if not isinstance(servers, list):
        raise ValueError("Bionic servers must be a JSON array")
    before = json.dumps(data, sort_keys=True)
    existing = next(
        (
            item
            for item in servers
            if isinstance(item, dict) and item.get("name") == "WebContentAgent"
        ),
        None,
    )
    if enabled:
        replacement = bionic_server_entry(server_id=existing.get("id") if existing else None)
        if existing:
            servers[servers.index(existing)] = replacement
        else:
            servers.append(replacement)
    elif existing:
        servers.remove(existing)
    after = json.dumps(data, sort_keys=True)
    backup = None
    if before != after:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            backup = path.with_name(f"ng-mcp.json.backup-{stamp}")
            shutil.copy2(path, backup)
        atomic_write_json(path, data)
    return backup, data


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure LM Studio WebContentAgent MCP")
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--path", type=Path, default=default_mcp_config())
    parser.add_argument("--bionic-path", type=Path, default=default_bionic_mcp_config())
    args = parser.parse_args()
    backup, _ = update_mcp_config(args.path, enabled=not args.disable)
    bionic_backup, _ = update_bionic_mcp_config(args.bionic_path, enabled=not args.disable)
    action = "disabled" if args.disable else "enabled"
    print(f"WebContentAgent MCP {action} (classic): {args.path.resolve()}")
    print(f"WebContentAgent MCP {action} (Bionic UI): {args.bionic_path.resolve()}")
    if backup:
        print(f"Previous configuration backed up: {backup}")
    if bionic_backup:
        print(f"Previous Bionic configuration backed up: {bionic_backup}")


if __name__ == "__main__":
    main()
