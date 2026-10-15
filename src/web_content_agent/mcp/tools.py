from __future__ import annotations

import json
import time
import urllib.robotparser
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from web_content_agent.article_selection import safe_slug
from web_content_agent.browser import BrowserController
from web_content_agent.cli import _validate_manifest
from web_content_agent.config import BrowserConfig, ModelConfig
from web_content_agent.context_handoff import new_chat_prompt, write_context_handoff
from web_content_agent.discovery import collect_link_candidates
from web_content_agent.paths import default_workspace_root, runtime_root

from .process_manager import (
    find_run,
    process_is_alive,
    read_process,
    require_workspace_path,
    spawn_worker,
)
from .task_spec import RunStatus

_LAST_STATUS_POLL: dict[str, float] = {}
STATUS_POLL_COOLDOWN_SECONDS = 30.0


def _safe_http_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are forbidden")
    return url.strip(), parsed.hostname.lower()


def _robots_allowed(url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    try:
        response = httpx.get(robots_url, follow_redirects=True, timeout=15)
        response.raise_for_status()
    except httpx.HTTPError:
        return True
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(response.text.splitlines())
    return parser.can_fetch(user_agent, url)


async def discover_site_data(url: str, instructions: str = "") -> dict:
    """Read one public page and return likely content links; never starts a download."""
    safe_url, host = _safe_http_url(url)
    browser_config = BrowserConfig()
    if not _robots_allowed(safe_url, browser_config.user_agent):
        raise PermissionError(f"robots.txt disallows discovery for {safe_url}")
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    state_dir = runtime_root() / "state" / "mcp-discovery" / stamp
    profile = state_dir / "profile"
    trace = state_dir / "trace.zip"
    async with BrowserController(browser_config, profile, trace) as browser:
        page = await browser.goto(safe_url)
        html = await page.content()
        title = await page.title()
        final_url = page.url
    candidates = collect_link_candidates(html, final_url, [host], limit=20)
    return {
        "status": "discovered",
        "page_title": title,
        "url": final_url,
        "instructions": instructions[:1000],
        "candidate_count": len(candidates),
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "next_tool": "Use create_collection only after the user identifies the desired collection.",
    }


def create_collection_data(
    url: str,
    name: str,
    instructions: str = "",
    strategy: Literal["auto", "news", "course"] = "auto",
    max_items: int | None = None,
) -> dict:
    """Create workspace and source.yaml only; does not start downloading."""
    safe_url, host = _safe_http_url(url)
    signal = f"{safe_url} {name} {instructions}".lower()
    resolved_strategy = strategy
    if strategy == "auto":
        hints = ("/course", "lesson", "chapter", "curriculum", "课程", "章节", "课时")
        resolved_strategy = "course" if any(hint in signal for hint in hints) else "news"
    if max_items is None:
        max_items = None if resolved_strategy == "course" else 3
    if max_items is not None and max_items < 1:
        raise ValueError("max_items must be at least 1 when supplied")
    site_name = safe_slug(host.removeprefix("www."))
    collection_name = safe_slug(name)
    workspace = default_workspace_root().resolve()
    site_dir = workspace / "sites" / site_name
    collection = site_dir / collection_name
    require_workspace_path(collection, must_exist=False)
    source_path = collection / "source.yaml"
    if source_path.exists():
        return {
            "status": "exists",
            "site": site_name,
            "collection": collection_name,
            "source_config": str(source_path),
            "next_tool": "Call start_collection with source_config when the user asks to begin.",
        }
    for relative in ("runs", "raw", "cleaned", "rewritten"):
        (collection / relative).mkdir(parents=True, exist_ok=True)
    site_config = site_dir / "site.yaml"
    if not site_config.exists():
        site_config.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "site_name": site_name,
                    "base_url": f"{urlparse(safe_url).scheme}://{urlparse(safe_url).netloc}",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    source_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "name": collection_name,
                "site_name": site_name,
                "homepage_url": safe_url,
                "allowed_domains": [host.removeprefix("www.")],
                "strategy": resolved_strategy,
                "max_items": max_items,
                "instructions": instructions[:4000],
                "model": ModelConfig().model_dump(mode="json"),
                "browser": BrowserConfig().model_dump(mode="json"),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (collection / "README.md").write_text(
        f"# {name}\n\nSource: {safe_url}\n\nRequested behavior: {instructions}\n\n"
        "Raw output is immutable after finalization.\n",
        encoding="utf-8",
    )
    (collection / "rewritten" / "README.md").write_text(
        "Reserved for later high-quality rewrites; extraction and cleanup do not write here.\n",
        encoding="utf-8",
    )
    return {
        "status": "created",
        "site": site_name,
        "collection": collection_name,
        "strategy": resolved_strategy,
        "max_items": max_items,
        "source_config": str(source_path),
        "next_tool": "Call start_collection with source_config when the user asks to begin.",
    }


def start_collection_data(source_config: str) -> dict:
    record = spawn_worker(source_config)
    return {
        "run_id": record.run_id,
        "status": "running",
        "pid": record.pid,
        "source_config": record.source_config,
        "next_tool": (
            "Do not poll repeatedly in this response. Tell the user the run_id; "
            "use get_run_status after at least 30 seconds or in a later message."
        ),
    }


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def get_run_status_data(run_id: str) -> dict:
    now = time.monotonic()
    last_poll = _LAST_STATUS_POLL.get(run_id)
    if last_poll is not None and now - last_poll < STATUS_POLL_COOLDOWN_SECONDS:
        return {
            "run_id": run_id,
            "status": "poll_too_soon",
            "retry_after_seconds": round(STATUS_POLL_COOLDOWN_SECONDS - (now - last_poll), 1),
            "instruction": "Stop polling in this response; the worker continues independently.",
        }
    _LAST_STATUS_POLL[run_id] = now
    record = read_process(run_id)
    try:
        run_dir = find_run(run_id)
    except FileNotFoundError:
        if record:
            alive = process_is_alive(record.pid)
            return RunStatus(
                run_id=run_id,
                status="running" if alive else "unknown",
                stage="starting" if alive else "worker_exited_before_checkpoint",
                source_config=record.source_config,
            ).model_dump(mode="json")
        raise
    checkpoint_path = run_dir / "checkpoint.json"
    manifest = _load_json(run_dir / "run-manifest.json")
    checkpoint = _load_json(checkpoint_path)
    articles = checkpoint.get("articles", {})
    completed = sum(1 for item in articles.values() if item.get("status") == "success")
    failed = sum(1 for item in articles.values() if item.get("status") == "failed")
    selected = checkpoint.get("selected_articles", [])
    current = next((key for key, item in articles.items() if item.get("status") != "success"), None)
    status = manifest.get("status", "unknown")
    if record and process_is_alive(record.pid) and status not in {"success", "failed"}:
        status = "running"
    if status not in {"starting", "running", "success", "failed", "interrupted"}:
        status = "unknown"
    started_at = manifest.get("started_at") or (record.started_at if record else None)
    elapsed = None
    if started_at:
        started = datetime.fromisoformat(started_at)
        finished_at = manifest.get("finished_at")
        end = datetime.fromisoformat(finished_at) if finished_at else datetime.now().astimezone()
        elapsed = round((end - started).total_seconds(), 3)
    return RunStatus(
        run_id=run_id,
        status=status,
        stage=checkpoint.get("current_stage", "starting"),
        completed=completed,
        total=len(selected),
        failed=failed or (1 if manifest.get("status") == "failed" else 0),
        current_item=current,
        last_checkpoint=(
            datetime.fromtimestamp(checkpoint_path.stat().st_mtime).astimezone().isoformat()
            if checkpoint_path.exists()
            else None
        ),
        elapsed_seconds=elapsed,
        can_resume=bool(checkpoint.get("can_resume")),
        source_config=manifest.get("source_config") or (record.source_config if record else None),
    ).model_dump(mode="json")


def resume_run_data(run_id: str) -> dict:
    status = get_run_status_data(run_id)
    if status["status"] == "running":
        return {"run_id": run_id, "status": "already_running"}
    if not status["can_resume"]:
        raise RuntimeError(f"Run {run_id} is not resumable")
    run_dir = find_run(run_id)
    manifest = _load_json(run_dir / "run-manifest.json")
    source = manifest.get("source_config")
    if not source or not Path(source).exists():
        source = str(run_dir.parents[1] / "source.yaml")
    record = spawn_worker(source, run_id=run_id, resume=True)
    return {"run_id": run_id, "status": "running", "pid": record.pid}


def validate_run_data(run_id: str) -> dict:
    run_dir = find_run(run_id)
    errors = _validate_manifest(run_dir)
    return {
        "run_id": run_id,
        "status": "passed" if not errors else "failed",
        "error_count": len(errors),
        "errors": errors[:20],
    }


def list_collections_data() -> dict:
    sites_root = default_workspace_root() / "sites"
    collections = []
    if not sites_root.exists():
        return {"collections": [], "count": 0}
    for source in sorted(sites_root.glob("*/*/source.yaml")):
        require_workspace_path(source)
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        collection_dir = source.parent
        manifests = sorted(
            collection_dir.glob("runs/*/run-manifest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        latest = _load_json(manifests[0]) if manifests else {}
        collections.append(
            {
                "site": data.get("site_name", source.parents[1].name),
                "collection": data.get("name", collection_dir.name),
                "source_url": data.get("homepage_url"),
                "source_config": str(source),
                "last_run": latest.get("run_id"),
                "last_status": latest.get("status"),
                "raw_content_count": len(
                    [p for p in (collection_dir / "raw").glob("*") if p.is_dir()]
                ),
                "cleaned_content_count": len(
                    [p for p in (collection_dir / "cleaned").glob("*") if p.is_dir()]
                ),
            }
        )
    return {"collections": collections, "count": len(collections)}


def get_failure_data(run_id: str) -> dict:
    run_dir = find_run(run_id)
    manifest = _load_json(run_dir / "run-manifest.json")
    checkpoint = _load_json(run_dir / "checkpoint.json")
    bundles = sorted(
        [path for path in (run_dir / "failures").glob("*") if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    bundle = bundles[0] if bundles else None
    error = _load_json(bundle / "error.json") if bundle else {}
    last_error = checkpoint.get("last_error") or {}
    return {
        "run_id": run_id,
        "status": manifest.get("status"),
        "error_category": error.get("category") or last_error.get("type"),
        "failed_url": error.get("current_url"),
        "failed_stage": checkpoint.get("current_stage"),
        "retry_count": sum(checkpoint.get("retry_counts", {}).values()),
        "failure_bundle": str(bundle) if bundle else None,
        "can_resume": bool(checkpoint.get("can_resume")),
        "message": (error.get("message") or last_error.get("message") or "")[:1000],
    }


def create_context_handoff_data(run_id: str) -> dict:
    """Persist a bounded recovery snapshot and return a ready-to-paste new-chat prompt."""
    run_dir = find_run(run_id)
    require_workspace_path(run_dir)
    json_path, markdown_path, handoff = write_context_handoff(run_dir)
    return {
        "run_id": run_id,
        "status": "handoff_ready",
        "snapshot": str(json_path),
        "human_summary": str(markdown_path),
        "compressed_state": {
            "run_status": handoff["status"],
            "stage": handoff["stage"],
            "completed": handoff["completed_count"],
            "failed": handoff["failed_count"],
            "can_resume": handoff["can_resume"],
            "capability_warning": handoff["capability_warning"],
            "next_action": handoff["next_action"],
        },
        "new_chat_prompt": new_chat_prompt(handoff, json_path),
        "instruction": "Open a new Chat and paste new_chat_prompt. Do not keep polling here.",
    }
