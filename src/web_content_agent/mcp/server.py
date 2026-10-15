from __future__ import annotations

import warnings
from typing import Literal

from mcp.server.fastmcp import FastMCP

from web_content_agent.chatgpt_chrome_bridge import (
    ask_chatgpt_data,
    read_chatgpt_conversation_data,
)

from .tools import (
    create_collection_data,
    create_context_handoff_data,
    discover_site_data,
    get_failure_data,
    get_run_status_data,
    list_collections_data,
    resume_run_data,
    start_collection_data,
    validate_run_data,
)

warnings.filterwarnings(
    "ignore",
    message=r"Field 'lifespan' has an incomplete definition.*",
)

mcp = FastMCP(
    "WebContentAgent",
    instructions=(
        "Use these tools to control the existing local WebContentAgent. Start with discover_site "
        "for an unfamiliar URL, create_collection only after choosing a collection, then "
        "start_collection. Long jobs continue independently; use get_run_status later. Never "
        "poll get_run_status repeatedly in one response; wait at least 30 seconds or until the "
        "user asks again. Use strategy='course' for requests to download all lessons/chapters, and "
        "strategy='news' for editorial selections. Course traversal follows public same-course "
        "links until the discovery queue is exhausted; max_items is an optional user-requested "
        "cap, not a fixed product limit. Never imply that protected lessons can be bypassed. When "
        "the conversation is long, keep bulk work in the background worker and return only concise "
        "status. A context handoff is optional recovery, not a requirement to switch Chat. "
        "Use ask_chatgpt only when the user explicitly asks ChatGPT for an answer or second "
        "opinion; it saves a private local task record, uses the signed-in Chrome companion, "
        "sends once, waits, and returns the answer with the canonical conversation URL. "
        "Never claim that login, CAPTCHA, paywall, DRM, robots rules, or access controls can be "
        "bypassed."
    ),
)


@mcp.tool()
def ask_chatgpt(
    question: str,
    conversation_url: str | None = None,
    attachment_path: str | None = None,
    timeout_seconds: int = 900,
    thinking_mode: Literal["current", "prefer_max", "max"] = "prefer_max",
    minimum_response_characters: int | None = None,
) -> dict:
    """Send one question and optional PDF through signed-in Chrome and return ChatGPT's answer.

    The question and result are persisted in an ignored local task file. Supply a canonical
    https://chatgpt.com/c/... URL to continue an existing conversation; omit it for a new chat.
    PDF paths must be absolute and inside the existing ChatGPT bridge's allowed roots.
    """
    return ask_chatgpt_data(
        question=question,
        conversation_url=conversation_url,
        attachment_path=attachment_path,
        timeout_seconds=timeout_seconds,
        thinking_mode=thinking_mode,
        minimum_response_characters=minimum_response_characters,
    )


@mcp.tool()
def read_chatgpt_conversation(
    conversation_url: str,
    timeout_seconds: int = 120,
    minimum_response_characters: int | None = None,
    expected_message_id: str | None = None,
) -> dict:
    """Read the latest assistant response from an existing ChatGPT conversation.

    This is recovery-only and never types, submits, or uploads anything. Supply the canonical
    https://chatgpt.com/c/... URL returned by a previous ask_chatgpt call.
    """
    return read_chatgpt_conversation_data(
        conversation_url=conversation_url,
        timeout_seconds=timeout_seconds,
        minimum_response_characters=minimum_response_characters,
        expected_message_id=expected_message_id,
    )


@mcp.tool()
async def discover_site(url: str, instructions: str = "Find likely content collections.") -> dict:
    """Inspect one public page and list likely content links.

    This never downloads a collection. Use it first for an unfamiliar site, then ask the
    user which collection they want.
    """
    return await discover_site_data(url, instructions)


@mcp.tool()
def create_collection(
    url: str,
    name: str,
    instructions: str = "",
    strategy: Literal["auto", "news", "course"] = "auto",
    max_items: int | None = None,
) -> dict:
    """Create a safe WebContent collection and source.yaml without starting a job.

    After this, call start_collection only when the user asks to begin.
    """
    return create_collection_data(url, name, instructions, strategy, max_items)


@mcp.tool()
def start_collection(source_config: str) -> dict:
    """Start the existing worker as an independent background process.

    Returns a run_id immediately. The path must be a source.yaml inside WebContent.
    """
    return start_collection_data(source_config)


@mcp.tool()
def get_run_status(run_id: str) -> dict:
    """Get concise progress for a run without returning full logs.

    Use this when the user asks how a download is going. Never call it repeatedly in one response.
    """
    return get_run_status_data(run_id)


@mcp.tool()
def resume_run(run_id: str) -> dict:
    """Resume a failed or interrupted run without repeating completed work.

    Use only after get_run_status or get_failure says the run is resumable.
    """
    return resume_run_data(run_id)


@mcp.tool()
def validate_run(run_id: str) -> dict:
    """Validate final outputs, hashes, and local image paths.

    Use after status reports success.
    """
    return validate_run_data(run_id)


@mcp.tool()
def list_collections() -> dict:
    """List collections with source URL, latest status, and content counts.

    Use for questions about existing collections or the latest run.
    """
    return list_collections_data()


@mcp.tool()
def get_failure(run_id: str) -> dict:
    """Return a redacted failure summary and bundle path for a failed run.

    Never returns cookies, authorization headers, credentials, or full logs.
    """
    return get_failure_data(run_id)


@mcp.tool()
def create_context_handoff(run_id: str) -> dict:
    """Compress durable run state into a small snapshot for continuing in a new Chat.

    Use once when a Chat becomes long or before a long background wait. It intentionally omits
    page bodies, logs, screenshots, repeated tool results, and hidden reasoning.
    """
    return create_context_handoff_data(run_id)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
