from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from .paths import path_exists


def localize_markdown_images(markdown: str, manifest: list[dict], prefix: str) -> str:
    result = markdown
    for item in manifest:
        source = item.get("original_url")
        filename = item.get("local_filename")
        if source and filename and item.get("download_success"):
            source_text = str(source)
            variants = {source_text}
            parsed = urlparse(source_text)
            if parsed.scheme and parsed.netloc:
                path_and_query = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                variants.add(path_and_query)
                variants.add(f"//{parsed.netloc}{path_and_query}")
            local_target = f"{prefix.rstrip('/')}/{filename}"
            for variant in variants:
                pattern = re.compile(rf"(!\[[^\]]*\]\(){re.escape(variant)}(?=(?:\s+[^\)]*)?\))")
                result = pattern.sub(
                    lambda match, replacement=local_target: f"{match.group(1)}{replacement}",
                    result,
                )
    return result


def reconcile_downloaded_images(markdown: str, manifest: list[dict], prefix: str) -> str:
    """Remove browser placeholders and retain every downloaded editorial candidate.

    Some client-rendered sites keep an empty data-URI ``img`` in the visible DOM
    while placing the real lazy image in a sibling or ``noscript`` node. The
    downloader sees the real URL, but markdownify sees only the placeholder. We
    never keep an invalid embedded URI: downloaded images that were not already
    localized are added in DOM order with their available alt/caption text.
    """
    result = re.sub(r"!\[[^\]]*\]\(data:[^\)]*\)", "", markdown)
    # Successful remote URLs were localized above. Any remote image left here
    # failed to download (or was not an approved candidate), so keep its audit
    # record in the manifest but do not publish a broken Markdown image.
    result = re.sub(r"!\[[^\]]*\]\(https?://[^\)]*\)", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    targets = set(markdown_image_targets(result))
    additions: list[str] = []
    seen_files: set[str] = set()
    for item in manifest:
        filename = item.get("local_filename")
        if not filename or not item.get("download_success") or filename in seen_files:
            continue
        seen_files.add(str(filename))
        target = f"{prefix.rstrip('/')}/{filename}"
        if target in targets:
            continue
        label = str(item.get("alt") or item.get("caption") or "Article image").strip()
        label = label.replace("[", "").replace("]", "")
        additions.append(f"![{label}]({target})")
    if additions:
        result = f"{result}\n\n## Images\n\n" + "\n\n".join(additions)
    return result


def remove_markdown_images(markdown: str, filenames: set[str]) -> str:
    result = markdown
    for filename in filenames:
        escaped = re.escape(filename)
        result = re.sub(rf"!?\[[^\]]*\]\([^\)]*{escaped}(?:\s+[^\)]*)?\)", "", result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def markdown_image_targets(markdown: str) -> list[str]:
    return re.findall(r"!\[[^\]]*\]\(([^\s\)]+)(?:\s+[^\)]*)?\)", markdown)


def validate_local_image_paths(markdown_path: Path) -> list[str]:
    missing: list[str] = []
    for target in markdown_image_targets(markdown_path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "data:")) or not path_exists(
            markdown_path.parent / target
        ):
            missing.append(target)
    return missing
