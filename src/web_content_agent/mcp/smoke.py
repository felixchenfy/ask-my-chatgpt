from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import warnings
from datetime import timedelta

from web_content_agent.paths import project_root

with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=r"Field 'lifespan' has an incomplete definition.*",
    )
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client


def _payload(result: object) -> dict:
    content = result.content
    return json.loads(content[0].text)


async def _poll_run(session: ClientSession, run_id: str) -> dict:
    status = {}
    for _ in range(180):
        await asyncio.sleep(5)
        status = _payload(await session.call_tool("get_run_status", {"run_id": run_id}))
        print(
            f"Smoke progress: {status['status']} {status['stage']} "
            f"{status['completed']}/{status['total']}",
            flush=True,
        )
        if status["status"] in {"success", "failed", "interrupted", "unknown"}:
            break
    return status


async def run_smoke(start: bool, run_id: str | None, resume: bool, validate: bool) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "web_content_agent.mcp.server"],
        cwd=str(project_root()),
        env=dict(os.environ),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(
            read_stream, write_stream, read_timeout_seconds=timedelta(seconds=60)
        ) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"MCP tools: {', '.join(tool.name for tool in tools.tools)}")
            collections = _payload(await session.call_tool("list_collections", {}))
            print(f"Collections: {collections['count']}")
            mit = next(
                (item for item in collections["collections"] if item["site"] == "mit-news"),
                None,
            )
            if not mit:
                raise RuntimeError("MIT News collection is missing")
            print(f"MIT News collection: {mit['collection']} ({mit['last_status']})")
            if mit.get("last_run"):
                status = _payload(
                    await session.call_tool("get_run_status", {"run_id": mit["last_run"]})
                )
                print(
                    f"Latest MIT run: {status['run_id']} status={status['status']} "
                    f"completed={status['completed']}/{status['total']}"
                )
            if run_id:
                if resume:
                    resumed = _payload(await session.call_tool("resume_run", {"run_id": run_id}))
                    print(f"Resume: {json.dumps(resumed, ensure_ascii=False)}")
                    await _poll_run(session, run_id)
                status = _payload(await session.call_tool("get_run_status", {"run_id": run_id}))
                print(f"Requested status: {json.dumps(status, ensure_ascii=False)}")
                if validate:
                    checked = _payload(await session.call_tool("validate_run", {"run_id": run_id}))
                    print(f"Validation: {json.dumps(checked, ensure_ascii=False)}")
            if not start:
                return
            created = _payload(
                await session.call_tool(
                    "create_collection",
                    {
                        "url": "https://news.mit.edu/",
                        "name": "mcp-ui-smoke",
                        "instructions": "MCP smoke test; preserve raw and cleaned outputs.",
                    },
                )
            )
            started = _payload(
                await session.call_tool(
                    "start_collection", {"source_config": created["source_config"]}
                )
            )
            print(
                f"Started independent worker: {started['run_id']} pid={started['pid']} "
                f"status={started['status']}"
            )
            await _poll_run(session, started["run_id"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Exercise WebContentAgent through MCP")
    parser.add_argument("--start", action="store_true", help="Start the isolated MIT smoke run")
    parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_smoke(args.start, args.run_id, args.resume, args.validate))


if __name__ == "__main__":
    main()
