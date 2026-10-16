from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from web_content_agent.mcp.configure import (
    SERVER_NAME,
    update_bionic_mcp_config,
    update_mcp_config,
)
from web_content_agent.mcp.process_manager import require_workspace_path
from web_content_agent.mcp.tools import (
    create_collection_data,
    create_context_handoff_data,
    get_failure_data,
    get_run_status_data,
    list_collections_data,
)


@pytest.fixture
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "Desktop"
    path.mkdir()
    monkeypatch.setenv("WCA_DESKTOP_PATH", str(path))
    return path


def test_collection_creation_and_listing_are_workspace_scoped(desktop: Path) -> None:
    result = create_collection_data(
        "https://news.example.test/course", "LLM Serving", "Keep useful diagrams"
    )
    source = Path(result["source_config"])
    assert result["status"] == "created"
    assert source.is_relative_to(desktop / "WebContent")
    source_data = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert source_data["strategy"] == "course"
    assert source_data["max_items"] is None
    assert "Keep useful diagrams" in (source.parent / "README.md").read_text(encoding="utf-8")
    listing = list_collections_data()
    assert listing["count"] == 1
    assert listing["collections"][0]["collection"] == "llm-serving"


def test_paths_outside_workspace_are_rejected(desktop: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.yaml"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        require_workspace_path(outside)


def test_status_and_redacted_failure_summary(desktop: Path) -> None:
    collection = desktop / "WebContent" / "sites" / "example" / "demo"
    run_id = "2026-08-15T120000-0700-demo"
    run = collection / "runs" / run_id
    failure = run / "failures" / "bundle"
    failure.mkdir(parents=True)
    (collection / "source.yaml").write_text(
        "name: demo\nhomepage_url: https://example.test/courses/demo\nmax_articles: 3\n",
        encoding="utf-8",
    )
    (run / "run-manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "failed",
                "started_at": "2026-08-15T12:00:00-07:00",
                "finished_at": "2026-08-15T12:00:05-07:00",
                "source_config": str(collection / "source.yaml"),
            }
        ),
        encoding="utf-8",
    )
    (run / "checkpoint.json").write_text(
        json.dumps(
            {
                "current_stage": "article_download",
                "selected_articles": [{}, {}, {}],
                "articles": {"001-a": {"status": "success"}, "002-b": {"status": "pending"}},
                "retry_counts": {"network": 2},
                "last_error": {"type": "TRANSIENT_NETWORK", "message": "safe message"},
                "can_resume": True,
            }
        ),
        encoding="utf-8",
    )
    (failure / "error.json").write_text(
        json.dumps(
            {
                "category": "TRANSIENT_NETWORK",
                "current_url": "https://example.test/a",
                "message": "safe message",
            }
        ),
        encoding="utf-8",
    )
    status = get_run_status_data(run_id)
    assert status["completed"] == 1
    assert status["total"] == 3
    assert status["can_resume"] is True
    repeated = get_run_status_data(run_id)
    assert repeated["status"] == "poll_too_soon"
    assert repeated["retry_after_seconds"] > 0
    summary = get_failure_data(run_id)
    assert summary["retry_count"] == 2
    assert summary["error_category"] == "TRANSIENT_NETWORK"
    assert "Authorization" not in json.dumps(summary)

    handoff = create_context_handoff_data(run_id)
    snapshot_path = Path(handoff["snapshot"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert handoff["status"] == "handoff_ready"
    assert snapshot["completed_count"] == 1
    assert snapshot["can_resume"] is True
    assert snapshot["workflow_scope"] == "course_collection"
    assert "Do not resume" in snapshot["capability_warning"]
    assert "safe message" in snapshot["last_error"]["message"]
    assert "page.html" not in json.dumps(snapshot)
    assert snapshot_path.is_relative_to(desktop / "WebContent")
    assert len(json.dumps(handoff, ensure_ascii=False)) < 5000
    handoff["new_chat_prompt"].encode("cp1252")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: uses .venv/Scripts/python.exe")
def test_lmstudio_config_merge_preserves_other_servers(tmp_path: Path) -> None:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"existing": {"url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    _, enabled = update_mcp_config(config, enabled=True)
    assert "existing" in enabled["mcpServers"]
    assert SERVER_NAME in enabled["mcpServers"]
    _, disabled = update_mcp_config(config, enabled=False)
    assert "existing" in disabled["mcpServers"]
    assert SERVER_NAME not in disabled["mcpServers"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: uses .venv/Scripts/python.exe")
def test_bionic_config_merge_preserves_other_servers(tmp_path: Path) -> None:
    config = tmp_path / "ng-mcp.json"
    config.write_text(
        json.dumps({"servers": [{"id": "keep", "name": "Existing", "enabled": True}]}),
        encoding="utf-8",
    )
    _, enabled = update_bionic_mcp_config(config, enabled=True)
    assert [server["name"] for server in enabled["servers"]] == [
        "Existing",
        "WebContentAgent",
    ]
    web_content = enabled["servers"][1]
    assert web_content["connection"]["type"] == "stdio"
    assert web_content["connection"]["args"] == ["-m", "web_content_agent.mcp.server"]
    _, disabled = update_bionic_mcp_config(config, enabled=False)
    assert disabled["servers"] == [{"id": "keep", "name": "Existing", "enabled": True}]
