from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import warnings
from datetime import timedelta
from pathlib import Path

import httpx

from web_content_agent.paths import default_workspace_root, project_root

from .configure import SERVER_NAME, default_bionic_mcp_config, default_mcp_config

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"Field 'lifespan' has an incomplete definition.*",
    )
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client


def _pass(label: str, detail: str = "") -> None:
    suffix = f": {detail}" if detail else ""
    print(f"[PASS] {label}{suffix}")


async def check_mcp() -> list[str]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "web_content_agent.mcp.server"],
        cwd=str(project_root()),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(
            read_stream, write_stream, read_timeout_seconds=timedelta(seconds=20)
        ) as session:
            await session.initialize()
            result = await session.list_tools()
            return [tool.name for tool in result.tools]


def _lms_executable() -> str:
    found = shutil.which("lms")
    fallback = Path.home() / ".lmstudio" / "bin" / "lms.exe"
    if found:
        return found
    if fallback.exists():
        return str(fallback)
    raise FileNotFoundError("lms CLI is unavailable")


def main() -> None:
    failures: list[str] = []
    try:
        lms = _lms_executable()
        version = subprocess.run(
            [lms, "--version"], capture_output=True, text=True, timeout=20, check=True
        )
        _pass("LM Studio CLI available", version.stdout.strip())
    except Exception as exc:
        failures.append(f"LM Studio CLI: {exc}")
    try:
        response = httpx.get("http://127.0.0.1:1234/v1/models", timeout=5)
        response.raise_for_status()
        models = response.json().get("data", [])
        _pass("LM Studio server available", "127.0.0.1:1234")
        identifiers = [model.get("id") for model in models]
        if "local-agent-vlm" not in identifiers:
            raise RuntimeError(f"local-agent-vlm is not loaded; models={identifiers}")
        _pass("local-agent-vlm loaded")
    except Exception as exc:
        failures.append(f"LM Studio server/model: {exc}")
    config_path = default_mcp_config()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if SERVER_NAME not in config.get("mcpServers", {}):
            raise RuntimeError(f"{SERVER_NAME} entry is absent")
        _pass("LM Studio MCP configuration exists", str(config_path))
    except Exception as exc:
        failures.append(f"MCP configuration: {exc}")
    bionic_config_path = default_bionic_mcp_config()
    try:
        bionic_config = json.loads(bionic_config_path.read_text(encoding="utf-8"))
        connected = next(
            (
                server
                for server in bionic_config.get("servers", [])
                if server.get("name") == "WebContentAgent" and server.get("enabled")
            ),
            None,
        )
        if not connected:
            raise RuntimeError("enabled WebContentAgent entry is absent")
        _pass("Bionic Chat UI MCP configuration exists", str(bionic_config_path))
    except Exception as exc:
        failures.append(f"Bionic MCP configuration: {exc}")
    try:
        tools = asyncio.run(check_mcp())
        expected = {
            "discover_site",
            "create_collection",
            "start_collection",
            "get_run_status",
            "resume_run",
            "validate_run",
            "list_collections",
            "get_failure",
            "create_context_handoff",
        }
        missing = expected.difference(tools)
        if missing:
            raise RuntimeError(f"missing tools: {sorted(missing)}")
        _pass("MCP server launches")
        _pass("MCP tool discovery works", f"{len(tools)} safe tools")
    except Exception as exc:
        failures.append(f"MCP launch/discovery: {exc}")
    workspace = default_workspace_root()
    if workspace.is_dir():
        _pass("WebContent workspace accessible", str(workspace))
    else:
        failures.append(f"WebContent workspace is missing: {workspace}")
    result = subprocess.run(
        [sys.executable, "-m", "web_content_agent.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode == 0:
        _pass("Existing WCA CLI works")
    else:
        failures.append("Existing WCA CLI help returned nonzero")
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
