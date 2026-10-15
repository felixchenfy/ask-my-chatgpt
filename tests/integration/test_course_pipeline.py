import json
from pathlib import Path

import pytest

from web_content_agent.config import load_source_config
from web_content_agent.worker import WebContentWorker


class CoursePage:
    def __init__(self) -> None:
        self.url = "https://fixture.test/courses/demo"


class CourseBrowser:
    def __init__(self, config: object, profile: Path, trace: Path, **kwargs: object) -> None:
        self.page = CoursePage()
        self.trace_path = trace
        self.last_status = 200

    async def __aenter__(self) -> "CourseBrowser":
        return self

    async def __aexit__(self, *args: object) -> None:
        self.trace_path.write_bytes(b"trace")

    async def goto(self, url: str) -> CoursePage:
        self.page.url = url
        return self.page

    async def save_current(
        self, html_path: Path, screenshot_path: Path, full_page: bool = True
    ) -> None:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(
            '<main><a href="/courses/demo/module-one">Module One</a></main>',
            encoding="utf-8",
        )
        screenshot_path.write_bytes(b"png")

    async def save_trace(self) -> None:
        self.trace_path.write_bytes(b"trace")


async def fake_extract_raw(
    self: WebContentWorker,
    browser: CourseBrowser,
    article: object,
    article_key: str,
    raw_dir: Path,
) -> dict:
    url = article.url  # type: ignore[attr-defined]
    title = article.title  # type: ignore[attr-defined]
    raw_dir.mkdir(parents=True, exist_ok=True)
    if url.endswith("module-one"):
        html = """
        <article><h1>Module One</h1><p>Module overview.</p>
        <a href="/courses/demo/module-one/lesson-one">Lesson One</a>
        <a href="/courses/demo/module-one/lesson-two">Lesson Two</a></article>
        """
    else:
        html = f"<article><h1>{title}</h1><p>Public lesson body.</p></article>"
    (raw_dir / "page.html").write_text(html, encoding="utf-8")
    return {
        "metadata": {"title": title, "canonical_url": url},
        "manifest": [],
        "report": {},
        "reused": False,
    }


def fake_cleanup(*args: object, **kwargs: object) -> dict:
    return {"counts": {"keep": 0, "drop": 0, "review": 0}}


@pytest.mark.asyncio
async def test_course_strategy_traverses_discovered_lessons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import web_content_agent.worker as worker_module

    source = tmp_path / "collection" / "source.yaml"
    source.parent.mkdir()
    source.write_text(
        "name: course-demo\nsite_name: fixture\n"
        "homepage_url: https://fixture.test/courses/demo\n"
        "allowed_domains: [fixture.test]\nstrategy: course\nmax_items: 10\n",
        encoding="utf-8",
    )
    config = load_source_config(source)
    monkeypatch.setattr(worker_module, "BrowserController", CourseBrowser)
    monkeypatch.setattr(WebContentWorker, "_robots", lambda self, url: True)
    monkeypatch.setattr(WebContentWorker, "_extract_raw", fake_extract_raw)
    monkeypatch.setattr(WebContentWorker, "_cleanup", fake_cleanup)
    monkeypatch.setattr(WebContentWorker, "_validate_article", lambda *args: None)
    monkeypatch.setattr(
        "web_content_agent.lmstudio.LMStudioClient.actual_model_identifier",
        lambda self: "local-agent-vlm",
    )

    run_id = "2026-08-15T130000-0700-course-demo"
    assert await WebContentWorker(config, run_id=run_id).run() == run_id
    run_dir = config.resolved_collection_dir() / "runs" / run_id
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    urls = [item["url"] for item in checkpoint["selected_articles"]]
    assert urls == [
        "https://fixture.test/courses/demo/module-one",
        "https://fixture.test/courses/demo/module-one/lesson-one",
        "https://fixture.test/courses/demo/module-one/lesson-two",
    ]
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "success"
    assert len(manifest["articles"]) == 3


@pytest.mark.asyncio
async def test_required_course_hierarchy_rejects_logged_out_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import web_content_agent.worker as worker_module

    source = tmp_path / "collection" / "source.yaml"
    source.parent.mkdir()
    source.write_text(
        "name: course-demo\nsite_name: fixture\n"
        "homepage_url: https://fixture.test/courses/demo\n"
        "allowed_domains: [fixture.test]\nstrategy: course\n"
        "require_course_hierarchy: true\n",
        encoding="utf-8",
    )
    config = load_source_config(source)
    monkeypatch.setattr(worker_module, "BrowserController", CourseBrowser)
    monkeypatch.setattr(WebContentWorker, "_robots", lambda self, url: True)
    monkeypatch.setattr(worker_module, "runtime_root", lambda: tmp_path / "runtime")
    monkeypatch.setattr(
        "web_content_agent.lmstudio.LMStudioClient.actual_model_identifier",
        lambda self: "local-agent-vlm",
    )

    run_id = "2026-08-16T010000-0700-course-preview"
    with pytest.raises(RuntimeError, match="LOGIN_REQUIRED"):
        await WebContentWorker(config, run_id=run_id).run()
    checkpoint = json.loads(
        (config.resolved_collection_dir() / "runs" / run_id / "checkpoint.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["last_error"]["type"] == "LOGIN_REQUIRED"
