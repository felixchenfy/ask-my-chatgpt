import io
import json
from pathlib import Path

import pytest
from PIL import Image

from web_content_agent.article_selection import ImageDecision
from web_content_agent.extraction import extract_article_from_html
from web_content_agent.image_cleanup import cleanup_article_images
from web_content_agent.image_download import download_images
from web_content_agent.markdown_output import localize_markdown_images, validate_local_image_paths


def make_image(color: str, size: tuple[int, int] = (640, 420)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


class FixtureResponse:
    ok = True
    status = 200
    headers = {"content-type": "image/png"}

    def __init__(self, content: bytes) -> None:
        self.content = content

    async def body(self) -> bytes:
        return self.content


class FixtureRequest:
    COLORS = ["blue", "green", "red", "purple", "orange", "black"]

    async def get(self, url: str, **kwargs: object) -> FixtureResponse:
        index = sum(url.encode("utf-8")) % len(self.COLORS)
        size = (1, 1) if "tracking" in url else (640 + index, 420)
        return FixtureResponse(make_image(self.COLORS[index], size))


class FixturePage:
    url = "https://fixture.test/news/fixture-story"
    request = FixtureRequest()


class ConservativeFixtureLLM:
    def classify_image(self, image_path: Path, **context: str) -> ImageDecision:
        alt = context.get("alt", "")
        return ImageDecision(
            category="chart" if "chart" in alt.lower() else "lead_photo",
            decision="keep",
            confidence=0.98,
            reason="Fixture model keeps substantive editorial imagery.",
        )


@pytest.mark.asyncio
async def test_full_fixture_extraction_and_cleanup(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "article.html"
    extracted = extract_article_from_html(
        fixture.read_text(encoding="utf-8"), "https://fixture.test/news/fixture-story"
    )
    assert extracted.title == "Fixture News Story"
    assert extracted.author == "Test Reporter"
    assert extracted.published_at == "2026-08-15T10:30:00-07:00"
    assert "first paragraph" in extracted.markdown
    assert "Header" not in extracted.markdown
    assert "Footer" not in extracted.markdown
    assert "Related stories" not in extracted.markdown
    urls = {item["original_url"] for item in extracted.image_candidates}
    assert "https://fixture.test/inline-lazy.png" in urls
    raw = tmp_path / "raw" / "001-fixture-news-story"
    originals = raw / "images" / "original"
    manifest = await download_images(FixturePage(), extracted.image_candidates, originals)
    (raw / "images").mkdir(parents=True, exist_ok=True)
    (raw / "images" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    markdown = localize_markdown_images(extracted.markdown, manifest, "images/original")
    (raw / "article.md").write_text(markdown, encoding="utf-8")
    assert not validate_local_image_paths(raw / "article.md")
    cleaned = tmp_path / "cleaned" / "001-fixture-news-story"
    report = cleanup_article_images(
        raw_article_dir=raw,
        cleaned_article_dir=cleaned,
        article_title=extracted.title,
        source_url=extracted.canonical_url,
        manifest=manifest,
        llm=ConservativeFixtureLLM(),  # type: ignore[arg-type]
        scratch_dir=tmp_path / "scratch",
    )
    assert report["counts"]["keep"] == 2
    assert report["counts"]["drop"] >= 4
    cleaned_markdown = (cleaned / "article.md").read_text(encoding="utf-8")
    assert "lead" in cleaned_markdown
    assert "inline" in cleaned_markdown
    assert "logo" not in cleaned_markdown.lower()
    assert "advert" not in cleaned_markdown.lower()
    assert "tracking" not in cleaned_markdown.lower()
    assert not validate_local_image_paths(cleaned / "article.md")
    assert (raw / "images" / "original").is_dir()
