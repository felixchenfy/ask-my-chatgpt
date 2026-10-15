from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_stdio_tool_discovery_and_collection_listing(tmp_path: Path) -> None:
    source = tmp_path / "WebContent/sites/example/demo/source.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(
        "name: Demo\nsite_name: example\nhomepage_url: https://example.com\n",
        encoding="utf-8",
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "web_content_agent.mcp.server"],
        env={**os.environ, "WCA_DESKTOP_PATH": str(tmp_path)},
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(
            read_stream, write_stream, read_timeout_seconds=timedelta(seconds=20)
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == {
                "ask_chatgpt",
                "read_chatgpt_conversation",
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
            assert all("shell" not in name for name in names)
            result = await session.call_tool("list_collections", {})
            assert result.isError is False
            payload = json.loads(result.content[0].text)
            assert payload["count"] == 1
            assert payload["collections"][0]["collection"] == "Demo"
