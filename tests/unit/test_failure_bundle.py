import json
from pathlib import Path

from web_content_agent.failure_bundle import create_failure_bundle


def test_complete_failure_bundle_and_redaction(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint-source.json"
    checkpoint.write_text('{"can_resume":true}', encoding="utf-8")
    events = tmp_path / "events-source.jsonl"
    events.write_text('{"event":"start"}\n', encoding="utf-8")
    try:
        raise RuntimeError("test failure")
    except RuntimeError as exc:
        bundle = create_failure_bundle(
            failure_root=tmp_path / "failures",
            exc=exc,
            checkpoint_path=checkpoint,
            events_path=events,
            current_url="https://fixture.test/story",
            page_html_path=None,
            screenshot_path=None,
            trace_path=None,
            last_llm_request={"Authorization": "Bearer private"},
            last_llm_response='{"decision":"review"}',
        )
    required = {
        "error.json",
        "traceback.txt",
        "recent-events.jsonl",
        "current-url.txt",
        "page.html",
        "screenshot.png",
        "playwright-trace.zip",
        "last-llm-request.json",
        "last-llm-response.txt",
        "checkpoint.json",
    }
    assert required <= {item.name for item in bundle.iterdir()}
    request = json.loads((bundle / "last-llm-request.json").read_text(encoding="utf-8"))
    assert request["Authorization"] == "[REDACTED]"
