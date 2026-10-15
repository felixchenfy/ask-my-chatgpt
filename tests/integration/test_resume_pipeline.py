import io
import json
import time
from pathlib import Path

import pytest
from PIL import Image

from web_content_agent.article_selection import ArticleSelection, ImageDecision
from web_content_agent.config import load_source_config
from web_content_agent.worker import ControlledStop, WebContentWorker


def image_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (700, 450), "navy").save(stream, format="PNG")
    return stream.getvalue()


class FakeResponse:
    ok = True
    status = 200
    headers = {"content-type": "image/png"}

    async def body(self) -> bytes:
        return image_bytes()


class FakeRequest:
    async def get(self, *args: object, **kwargs: object) -> FakeResponse:
        return FakeResponse()


class FakePage:
    def __init__(self) -> None:
        self.url = "https://fixture.test/"
        self.request = FakeRequest()


def article_html(number: int, url: str) -> str:
    structured = json.dumps(
        {
            "@type": "NewsArticle",
            "headline": f"Fixture Story {number}",
            "url": url,
        }
    )
    return f"""<!doctype html><html><head><title>Fixture Story {number}</title>
    <link rel="canonical" href="{url}"><meta name="author" content="Reporter">
    <script type="application/ld+json">{structured}</script></head><body><article>
    <h1>Fixture Story {number}</h1><figure>
    <img src="https://fixture.test/image-{number}.png" alt="Editorial photo {number}">
    </figure><p>This is a substantive fixture paragraph for story {number} with enough
    content for extraction.</p></article></body></html>"""


class FakeBrowser:
    def __init__(self, config: object, profile: Path, trace: Path, **kwargs: object) -> None:
        self.page = FakePage()
        self.trace_path = trace
        self.last_status = 200

    async def __aenter__(self) -> "FakeBrowser":
        return self

    async def __aexit__(self, *args: object) -> None:
        self.trace_path.write_bytes(b"trace")

    async def goto(self, url: str) -> FakePage:
        self.page.url = url
        return self.page

    async def save_current(
        self, html_path: Path, screenshot_path: Path, full_page: bool = True
    ) -> None:
        html_path.parent.mkdir(parents=True, exist_ok=True)
        if self.page.url.rstrip("/") == "https://fixture.test":
            html = (
                "<main>"
                + "".join(
                    f'<article><h2><a href="/news/{n}">Fixture Story {n}</a></h2></article>'
                    for n in range(1, 4)
                )
                + "</main>"
            )
        else:
            number = int(self.page.url.rsplit("/", 1)[-1])
            html = article_html(number, self.page.url)
        html_path.write_text(html, encoding="utf-8")
        screenshot_path.write_bytes(b"png")

    async def save_trace(self) -> None:
        self.trace_path.write_bytes(b"trace")


def fake_selection(*args: object, **kwargs: object) -> ArticleSelection:
    return ArticleSelection.model_validate(
        {
            "articles": [
                {
                    "rank": n,
                    "title": f"Fixture Story {n}",
                    "url": f"https://fixture.test/news/{n}",
                    "reason": "Prominent normal editorial fixture article",
                }
                for n in range(1, 4)
            ]
        }
    )


@pytest.mark.asyncio
async def test_resume_skips_completed_article(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import web_content_agent.worker as worker_module

    source = tmp_path / "collection" / "source.yaml"
    source.parent.mkdir()
    source.write_text(
        "name: resume-demo\nsite_name: fixture\nhomepage_url: https://fixture.test/\n"
        "allowed_domains: [fixture.test]\nmax_articles: 3\n",
        encoding="utf-8",
    )
    config = load_source_config(source)
    monkeypatch.setattr(worker_module, "BrowserController", FakeBrowser)
    monkeypatch.setattr(WebContentWorker, "_robots", lambda self, url: True)
    monkeypatch.setattr(
        "web_content_agent.lmstudio.LMStudioClient.actual_model_identifier",
        lambda self: "local-agent-vlm",
    )
    monkeypatch.setattr("web_content_agent.lmstudio.LMStudioClient.select_articles", fake_selection)
    monkeypatch.setattr(
        "web_content_agent.lmstudio.LMStudioClient.classify_image",
        lambda self, *args, **kwargs: ImageDecision(
            category="lead_photo", decision="keep", confidence=0.99, reason="Editorial fixture"
        ),
    )
    run_id = "2026-08-15T120000-0700-resume-demo"
    first = WebContentWorker(config, run_id=run_id, stop_after=1)
    with pytest.raises(ControlledStop):
        await first.run()
    first_html = config.resolved_collection_dir() / "raw" / "001-fixture-story-1" / "page.html"
    assert first_html.exists()
    first_mtime = first_html.stat().st_mtime_ns
    first_content = first_html.read_bytes()
    time.sleep(0.01)
    resumed = WebContentWorker(config, run_id=run_id)
    assert await resumed.run() == run_id
    assert first_html.stat().st_mtime_ns == first_mtime
    assert first_html.read_bytes() == first_content
    manifest = json.loads(
        (config.resolved_collection_dir() / "runs" / run_id / "run-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "success"
    assert len(manifest["articles"]) == 3
    assert all(item["status"] == "success" for item in manifest["articles"])
