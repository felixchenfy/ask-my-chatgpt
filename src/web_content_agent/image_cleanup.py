from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from PIL import Image

from .article_selection import ImageDecision
from .lmstudio import LMStudioClient
from .markdown_output import localize_markdown_images, remove_markdown_images
from .paths import path_exists, windows_io_path
from .text_cleanup import conservative_text_cleanup

DROP_HINT = re.compile(
    r"\b(logo|icon|avatar|advert|sponsor|tracking|pixel|social|facebook|twitter|instagram|placeholder)\b",
    re.I,
)


def deterministic_image_decision(item: dict) -> ImageDecision | None:
    if item.get("duplicate"):
        return ImageDecision(
            category="decorative",
            decision="drop",
            confidence=1,
            reason=f"Duplicate bytes of {item.get('duplicate_of')}",
        )
    if not item.get("download_success") or not item.get("local_filename"):
        return ImageDecision(
            category="unknown",
            decision="review",
            confidence=0,
            reason=(
                "Image download failed; raw reference retained for review but no broken cleaned "
                "link emitted."
            ),
        )
    width, height = item.get("width"), item.get("height")
    if width and height and width <= 2 and height <= 2:
        return ImageDecision(
            category="tracking",
            decision="drop",
            confidence=1,
            reason="Decoded dimensions are at most 2x2.",
        )
    semantics = " ".join(
        str(item.get(key) or "") for key in ("alt", "caption", "dom_context", "original_url")
    )
    match = DROP_HINT.search(semantics)
    if match:
        category = (
            "advertisement"
            if match.group(0).lower().startswith(("advert", "sponsor"))
            else "decorative"
        )
        return ImageDecision(
            category=category,
            decision="drop",
            confidence=0.98,
            reason=f"Deterministic semantic exclusion matched '{match.group(0)}'.",
        )
    return None


def _resize_for_model(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(windows_io_path(source)) as image:
        image = image.convert("RGB")
        image.thumbnail((1280, 1280))
        image.save(target, format="JPEG", quality=85, optimize=True)


def cleanup_article_images(
    *,
    raw_article_dir: Path,
    cleaned_article_dir: Path,
    article_title: str,
    source_url: str,
    manifest: list[dict],
    llm: LMStudioClient,
    scratch_dir: Path,
) -> dict:
    cleaned_images = cleaned_article_dir / "images"
    cleaned_images.mkdir(parents=True, exist_ok=True)
    decisions: list[dict] = []
    dropped_filenames: set[str] = set()
    counts = {"keep": 0, "drop": 0, "review": 0}
    for item in manifest:
        decision = deterministic_image_decision(item)
        source_name = item.get("local_filename")
        if decision is None and source_name:
            source = raw_article_dir / "images" / "original" / source_name
            preview = scratch_dir / f"preview-{source_name}.jpg"
            try:
                _resize_for_model(source, preview)
                decision = llm.classify_image(
                    preview,
                    article_title=article_title,
                    alt=str(item.get("alt") or ""),
                    caption=str(item.get("caption") or ""),
                    nearby_text=str(item.get("nearby_text") or ""),
                    dom_region=str(item.get("dom_context") or item.get("dom_ancestor") or ""),
                )
            except Exception as exc:
                decision = ImageDecision(
                    category="unknown",
                    decision="review",
                    confidence=0,
                    reason=(
                        "Local image processing/model error; retained safely: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
        if decision is None:
            decision = ImageDecision(
                category="unknown",
                decision="review",
                confidence=0,
                reason="No safe classification available.",
            )
        counts[decision.decision] += 1
        record = {**item, **decision.model_dump()}
        decisions.append(record)
        if decision.decision in {"keep", "review"} and source_name and item.get("download_success"):
            source = raw_article_dir / "images" / "original" / source_name
            if path_exists(source):
                shutil.copy2(windows_io_path(source), windows_io_path(cleaned_images / source_name))
        elif source_name:
            dropped_filenames.add(source_name)
    raw_markdown = (raw_article_dir / "article.md").read_text(encoding="utf-8")
    cleaned_markdown = localize_markdown_images(raw_markdown, manifest, "images")
    cleaned_markdown = cleaned_markdown.replace("images/original/", "images/")
    cleaned_markdown = remove_markdown_images(cleaned_markdown, dropped_filenames)
    cleaned_markdown = conservative_text_cleanup(cleaned_markdown)
    cleaned_markdown = re.sub(
        r"\n---\s*\n+Source:\s*https?://\S+\s*$", "", cleaned_markdown, flags=re.I
    ).rstrip()
    cleaned_markdown = f"{cleaned_markdown}\n\n---\n\nSource: {source_url}\n"
    (cleaned_article_dir / "article.md").write_text(cleaned_markdown, encoding="utf-8")
    with (cleaned_article_dir / "image-decisions.jsonl").open("w", encoding="utf-8") as handle:
        for record in decisions:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    report = {"counts": counts, "decisions": len(decisions), "source_raw_dir": str(raw_article_dir)}
    (cleaned_article_dir / "cleanup-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def repair_cleaned_image_copies(raw_article_dir: Path, cleaned_article_dir: Path) -> int:
    """Restore retained derived image copies without mutating immutable raw evidence."""
    decisions_path = cleaned_article_dir / "image-decisions.jsonl"
    if not decisions_path.exists():
        return 0
    repaired = 0
    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        filename = item.get("local_filename")
        if (
            item.get("decision") not in {"keep", "review"}
            or not item.get("download_success")
            or not filename
        ):
            continue
        source = raw_article_dir / "images" / "original" / str(filename)
        target = cleaned_article_dir / "images" / str(filename)
        if not path_exists(target) and path_exists(source):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(windows_io_path(source), windows_io_path(target))
            repaired += 1
    return repaired
