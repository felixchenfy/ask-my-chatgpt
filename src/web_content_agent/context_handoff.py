from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .checkpoint import atomic_write_json

MAX_SELECTED_ITEMS = 10
MAX_ERROR_CHARS = 800


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_context_handoff(run_dir: Path) -> dict[str, Any]:
    """Build a small, deterministic handoff without copying logs or page content."""
    manifest = _read_json(run_dir / "run-manifest.json")
    checkpoint = _read_json(run_dir / "checkpoint.json")
    source = {}
    source_snapshot = run_dir / "source-snapshot.yaml"
    if not source_snapshot.exists():
        source_snapshot = run_dir.parents[1] / "source.yaml"
    if source_snapshot.exists():
        source = yaml.safe_load(source_snapshot.read_text(encoding="utf-8")) or {}

    articles = checkpoint.get("articles", {})
    selected = checkpoint.get("selected_articles", [])
    completed = [key for key, value in articles.items() if value.get("status") == "success"]
    failed = [key for key, value in articles.items() if value.get("status") == "failed"]
    pending = [key for key, value in articles.items() if value.get("status") != "success"]
    last_error = checkpoint.get("last_error") or {}

    selected_summary = []
    for item in selected[:MAX_SELECTED_ITEMS]:
        if not isinstance(item, dict):
            continue
        selected_summary.append(
            {
                "rank": item.get("rank"),
                "title": str(item.get("title") or "")[:200],
                "url": str(item.get("url") or "")[:500],
            }
        )

    status = manifest.get("status", "unknown")
    can_resume = bool(checkpoint.get("can_resume"))
    source_url = str(source.get("homepage_url") or "")
    configured_strategy = source.get("strategy", "auto")
    inferred_course = "/course" in source_url.lower()
    resolved_strategy = (
        "course"
        if configured_strategy == "course" or (configured_strategy == "auto" and inferred_course)
        else "news"
    )
    course_shape_mismatch = (
        inferred_course and "strategy" not in source and source.get("max_articles") == 3
    )
    capability_warning = None
    if course_shape_mismatch:
        capability_warning = (
            "This source points to a course, but the current worker strategy handles exactly three "
            "editorial articles. Do not resume or describe it as a complete-course download."
        )
        next_action = "Create/implement a course strategy before attempting the full-course task."
    elif status == "success":
        next_action = f"Validate run {run_dir.name}."
    elif can_resume:
        next_action = (
            f"Inspect status/failure once, then resume run {run_dir.name} from its checkpoint."
        )
    else:
        next_action = f"Inspect the failure for run {run_dir.name}; do not restart blindly."

    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "run_id": run_dir.name,
        "collection": run_dir.parent.parent.name,
        "site": run_dir.parent.parent.parent.name,
        "source_name": source.get("name"),
        "source_url": source_url or None,
        "source_config": manifest.get("source_config"),
        "workflow_scope": f"{resolved_strategy}_collection",
        "capability_warning": capability_warning,
        "status": status,
        "stage": checkpoint.get("current_stage", "unknown"),
        "completed_count": len(completed),
        "failed_count": len(failed),
        "selected_count": len(selected),
        "completed_items": completed[:MAX_SELECTED_ITEMS],
        "pending_items": pending[:MAX_SELECTED_ITEMS],
        "selected_items": selected_summary,
        "retry_counts": checkpoint.get("retry_counts", {}),
        "last_success_at": checkpoint.get("last_success_at"),
        "last_error": {
            "type": last_error.get("type"),
            "message": str(last_error.get("message") or "")[:MAX_ERROR_CHARS],
            "timestamp": last_error.get("timestamp"),
        },
        "can_resume": can_resume,
        "next_action": next_action,
        "omitted": "Full chat, HTML, article text, screenshots, logs, and model reasoning.",
    }


def write_context_handoff(run_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    handoff = build_context_handoff(run_dir)
    json_path = run_dir / "context-handoff.json"
    markdown_path = run_dir / "context-handoff.md"
    atomic_write_json(json_path, handoff)
    markdown = (
        "# WebContentAgent context handoff\n\n"
        f"Run: `{handoff['run_id']}`  \n"
        f"Status/stage: `{handoff['status']}` / `{handoff['stage']}`  \n"
        f"Progress: {handoff['completed_count']} completed, "
        f"{handoff['failed_count']} failed, {handoff['selected_count']} selected  \n"
        f"Can resume: `{str(handoff['can_resume']).lower()}`\n\n"
        f"Next action: {handoff['next_action']}\n\n"
        "This is a compact recovery record. Authoritative state remains in the checkpoint and "
        "manifest; page bodies and logs are intentionally omitted.\n"
    )
    _atomic_write_text(markdown_path, markdown)
    return json_path, markdown_path, handoff


def new_chat_prompt(handoff: dict[str, Any], json_path: Path) -> str:
    prompt = (
        "This is a fresh LM Studio Chat. Continue WebContentAgent run "
        f"{handoff['run_id']}. The compact snapshot is at {json_path}. "
        f"Current state: {handoff['status']}/{handoff['stage']}; "
        f"can_resume={str(handoff['can_resume']).lower()}. "
        "Call get_run_status once; call get_failure only if the run failed. "
        "Do not poll repeatedly, recreate the collection, or redownload completed work."
    )
    if handoff.get("capability_warning"):
        prompt += f" IMPORTANT: {handoff['capability_warning']}"
    return prompt
