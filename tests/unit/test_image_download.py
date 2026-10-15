import io
import json
from pathlib import Path

import pytest
from PIL import Image

from web_content_agent.image_cleanup import repair_cleaned_image_copies
from web_content_agent.image_download import (
    download_images,
    fit_filename_to_windows_path,
    fit_filename_to_windows_paths,
)


def png_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (100, 80), "blue").save(stream, format="PNG")
    return stream.getvalue()


class FakeResponse:
    ok = True
    status = 200
    headers = {"content-type": "image/png"}

    async def body(self) -> bytes:
        return png_bytes()


class FakeRequest:
    async def get(self, *args: object, **kwargs: object) -> FakeResponse:
        return FakeResponse()


class FakePage:
    url = "https://fixture.test/story"
    request = FakeRequest()


@pytest.mark.asyncio
async def test_duplicate_image_hash_deduplication(tmp_path: Path) -> None:
    candidates = [
        {"position": 1, "original_url": "https://fixture.test/a.png", "alt": "First"},
        {"position": 2, "original_url": "https://fixture.test/b.png", "alt": "Second"},
    ]
    manifest = await download_images(FakePage(), candidates, tmp_path)
    assert manifest[0]["download_success"]
    assert not manifest[0]["duplicate"]
    assert manifest[1]["duplicate"]
    assert manifest[1]["duplicate_of"] == manifest[0]["local_filename"]
    assert len(list(tmp_path.iterdir())) == 1


def test_image_filename_leaves_windows_path_headroom(tmp_path: Path) -> None:
    output = tmp_path / ("long-article-name-" * 5) / "images" / "original"
    filename = fit_filename_to_windows_path(
        "001-a-very-descriptive-editorial-image-name.jpg", output
    )
    assert len(str(output.resolve() / filename)) <= 245
    assert filename.startswith("001-")
    assert filename.endswith(".jpg")


def test_image_filename_budgets_against_final_raw_and_cleaned_paths(tmp_path: Path) -> None:
    staging = tmp_path / "runs" / "short" / "staging" / "019" / "raw" / "images"
    raw = tmp_path / "raw" / ("long-section-" * 3) / ("long-lesson-" * 3) / "images"
    cleaned = tmp_path / "cleaned" / ("long-section-" * 3) / ("long-lesson-" * 3) / "images"
    filename = fit_filename_to_windows_paths(
        "019-an-extremely-descriptive-diagram-name-that-used-to-fit-only-staging.svg",
        [staging, raw, cleaned],
    )
    assert all(len(str(path.resolve() / filename)) <= 245 for path in (staging, raw, cleaned))
    assert filename.startswith("019-")
    assert filename.endswith(".svg")


def test_repair_cleaned_image_copies_preserves_raw(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "lesson"
    cleaned = tmp_path / "cleaned" / "lesson"
    source = raw / "images" / "original" / "019-diagram.svg"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"<svg>immutable raw</svg>")
    cleaned.mkdir(parents=True)
    decision = {
        "local_filename": source.name,
        "download_success": True,
        "decision": "review",
    }
    (cleaned / "image-decisions.jsonl").write_text(json.dumps(decision) + "\n", encoding="utf-8")

    assert repair_cleaned_image_copies(raw, cleaned) == 1
    assert (cleaned / "images" / source.name).read_bytes() == source.read_bytes()
    assert source.read_bytes() == b"<svg>immutable raw</svg>"
