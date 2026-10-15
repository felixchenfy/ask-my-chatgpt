from __future__ import annotations

import asyncio
import hashlib
import io
import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image
from playwright.async_api import Page

from .article_selection import safe_filename, safe_slug


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _extension(mime_type: str, url: str) -> str:
    url_suffix = Path(unquote(urlparse(url).path)).suffix.lower()
    if url_suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return url_suffix
    return mimetypes.guess_extension(mime_type.split(";", 1)[0]) or ".bin"


def _image_name(candidate: dict, mime_type: str) -> str:
    source_name = Path(unquote(urlparse(candidate["original_url"]).path)).stem
    descriptive = safe_slug(
        candidate.get("alt") or source_name or f"image-{candidate['position']:03d}", 54
    )
    extension = _extension(mime_type, candidate["original_url"])
    return safe_filename(f"{candidate['position']:03d}-{descriptive}{extension}")


def fit_filename_to_windows_path(filename: str, output_dir: Path, max_path: int = 245) -> str:
    """Keep a readable name while leaving headroom below legacy MAX_PATH."""
    return fit_filename_to_windows_paths(filename, [output_dir], max_path=max_path)


def fit_filename_to_windows_paths(
    filename: str, output_dirs: list[Path], max_path: int = 245
) -> str:
    """Budget a shared filename against every final directory that will contain it."""
    if not output_dirs:
        raise ValueError("At least one output directory is required for filename budgeting")
    available = min(max_path - len(str(directory.resolve())) - 1 for directory in output_dirs)
    if available < 12:
        raise OSError(
            "Image output directory is too long for a safe filename: "
            + ", ".join(str(directory) for directory in output_dirs)
        )
    if len(filename) <= available:
        return filename
    suffix = Path(filename).suffix
    stem_budget = max(4, available - len(suffix))
    shortened = f"{Path(filename).stem[:stem_budget].rstrip('-')}{suffix}"
    return safe_filename(shortened)


async def download_images(
    page: Page,
    candidates: list[dict],
    output_dir: Path,
    *,
    request_delay_seconds: float = 0.0,
    filename_budget_dirs: list[Path] | None = None,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    manifest: list[dict] = []
    for index, candidate in enumerate(candidates):
        if index and request_delay_seconds:
            await asyncio.sleep(request_delay_seconds)
        record = {
            **candidate,
            "local_filename": None,
            "mime_type": None,
            "width": None,
            "height": None,
            "sha256": None,
            "duplicate": False,
            "duplicate_of": None,
            "download_success": False,
            "error": None,
        }
        try:
            response = await page.request.get(
                candidate["original_url"],
                headers={"Referer": page.url, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"},
                timeout=30000,
            )
            if not response.ok:
                raise RuntimeError(f"HTTP {response.status}")
            content = await response.body()
            mime_type = response.headers.get("content-type", "application/octet-stream")
            digest = sha256_bytes(content)
            filename = fit_filename_to_windows_paths(
                _image_name(candidate, mime_type), filename_budget_dirs or [output_dir]
            )
            record.update({"mime_type": mime_type, "sha256": digest})
            if digest in hashes:
                record.update(
                    {
                        "duplicate": True,
                        "duplicate_of": hashes[digest],
                        "download_success": True,
                        "local_filename": hashes[digest],
                    }
                )
            else:
                target = output_dir / filename
                target.write_bytes(content)
                hashes[digest] = filename
                record.update({"download_success": True, "local_filename": filename})
                try:
                    with Image.open(io.BytesIO(content)) as image:
                        record.update({"width": image.width, "height": image.height})
                except Exception as exc:
                    record["error"] = f"Image metadata unavailable: {type(exc).__name__}: {exc}"
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        manifest.append(record)
    return manifest
