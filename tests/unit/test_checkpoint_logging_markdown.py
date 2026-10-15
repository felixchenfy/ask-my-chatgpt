import json
from pathlib import Path

from web_content_agent.checkpoint import Checkpoint
from web_content_agent.failure_bundle import classify_error
from web_content_agent.logging_setup import redact
from web_content_agent.markdown_output import (
    localize_markdown_images,
    reconcile_downloaded_images,
    validate_local_image_paths,
)


def test_checkpoint_atomic_write_and_resume(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    checkpoint = Checkpoint(path)
    checkpoint.set_article("001-story", "raw_complete", url="https://example.test/story")
    assert not list(tmp_path.glob("*.tmp*"))
    resumed = Checkpoint(path)
    assert resumed.data["articles"]["001-story"]["status"] == "raw_complete"
    resumed.set_article("001-story", "success")
    assert Checkpoint(path).completed("001-story")
    json.loads(path.read_text(encoding="utf-8"))


def test_sensitive_log_redaction() -> None:
    value = redact(
        {
            "Authorization": "Bearer abc.secret",
            "nested": {"cookie": "session=private", "message": "Bearer raw-token"},
        }
    )
    assert value["Authorization"] == "[REDACTED]"
    assert value["nested"]["cookie"] == "[REDACTED]"
    assert "raw-token" not in value["nested"]["message"]


def test_markdown_image_localization_and_validation(tmp_path: Path) -> None:
    markdown = "![Lead](https://img.test/lead.jpg)"
    manifest = [
        {
            "original_url": "https://img.test/lead.jpg",
            "local_filename": "001-lead.jpg",
            "download_success": True,
        }
    ]
    localized = localize_markdown_images(markdown, manifest, "images")
    article = tmp_path / "article.md"
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "001-lead.jpg").write_bytes(b"image")
    article.write_text(localized, encoding="utf-8")
    assert localized == "![Lead](images/001-lead.jpg)"
    assert validate_local_image_paths(article) == []


def test_markdown_image_localization_matches_root_relative_source(tmp_path: Path) -> None:
    source_path = "/static/emoji/google/2753.png"
    markdown = f"![question]({source_path})\n\n[Source link]({source_path})"
    manifest = [
        {
            "original_url": f"https://www.educative.io{source_path}",
            "local_filename": "003-question.png",
            "download_success": True,
        }
    ]

    localized = localize_markdown_images(markdown, manifest, "images/original")

    assert "![question](images/original/003-question.png)" in localized
    assert f"[Source link]({source_path})" in localized
    article = tmp_path / "article.md"
    image_dir = tmp_path / "images" / "original"
    image_dir.mkdir(parents=True)
    (image_dir / "003-question.png").write_bytes(b"image")
    article.write_text(localized, encoding="utf-8")
    assert validate_local_image_paths(article) == []


def test_placeholder_images_are_removed_and_lazy_downloads_retained(tmp_path: Path) -> None:
    markdown = (
        "Body\n\n![](data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///w==)\n\n"
        "![Lead](images/original/002-lead.svg)"
    )
    manifest = [
        {
            "local_filename": "002-lead.svg",
            "download_success": True,
            "alt": "Lead",
        },
        {
            "local_filename": "007-diagram.svg",
            "download_success": True,
            "alt": "Architecture diagram",
        },
    ]
    reconciled = reconcile_downloaded_images(markdown, manifest, "images/original")
    assert "data:image" not in reconciled
    assert reconciled.count("002-lead.svg") == 1
    assert reconciled.count("007-diagram.svg") == 1
    assert "Architecture diagram" in reconciled


def test_failed_remote_image_is_removed_but_numeric_url_is_not_http_503() -> None:
    url = "https://example.test/image/1503123?get_optimised=true"
    reconciled = reconcile_downloaded_images(f"Body\n\n![Broken]({url})", [], "images")
    assert "![Broken]" not in reconciled
    assert classify_error(ValueError(f"Missing image: {url}")) == "CODE_BUG"
    assert classify_error(RuntimeError("HTTP status 503")) == "TRANSIENT_NETWORK"
