from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.robotparser
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .article_selection import SelectedArticle, safe_directory_name, safe_slug
from .browser import BrowserController
from .checkpoint import Checkpoint, atomic_write_json
from .config import SourceConfig
from .discovery import (
    collect_course_candidates,
    collect_link_candidates,
    make_dom_summary,
    validate_selection_against_candidates,
)
from .extraction import extract_article_from_html
from .failure_bundle import classify_error, create_failure_bundle
from .image_cleanup import cleanup_article_images, repair_cleaned_image_copies
from .image_download import download_images
from .lmstudio import LMStudioClient
from .logging_setup import EventLogger
from .markdown_output import (
    localize_markdown_images,
    reconcile_downloaded_images,
    validate_local_image_paths,
)
from .paths import default_workspace_root, runtime_root
from .run_manifest import RunManifest, make_run_id


class ControlledStop(RuntimeError):
    """Debug-only checkpoint interruption used by resume tests."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "uncommitted"


def _lmstudio_version() -> str:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        package = Path(local) / "Programs" / "Bionic" / "resources" / "app" / "package.json"
        if package.exists():
            try:
                return str(json.loads(package.read_text(encoding="utf-8"))["version"])
            except (json.JSONDecodeError, KeyError):
                pass
    return "unknown"


def _article_output_key(article: SelectedArticle, force_suffix: str | None = None) -> str:
    if force_suffix:
        return f"{article.rank:03d}-{safe_slug(article.title, 48)}-{safe_slug(force_suffix, 18)}"
    return f"{article.rank:03d}-{safe_slug(article.title)}"


def _article_output_path(article: SelectedArticle, force_suffix: str | None = None) -> Path:
    """Return the user-facing path while keeping the checkpoint key flat and stable."""
    if (
        article.section_number is not None
        and article.section_title
        and article.lesson_number is not None
    ):
        section = (
            f"{article.section_number}_{safe_directory_name(article.section_title, 'Section')}"
        )
        lesson = (
            f"{article.section_number}.{article.lesson_number}_"
            f"{safe_directory_name(article.title, 'Lesson')}"
        )
        if force_suffix:
            lesson += f"_{safe_directory_name(force_suffix, 'forced', 18)}"
        return Path(section) / lesson
    return Path(_article_output_key(article, force_suffix))


def _staging_key(article_key: str) -> str:
    """Use only the numeric rank inside the already-long run directory."""
    rank = article_key.split("-", 1)[0]
    if not rank.isdigit():
        raise ValueError(f"Invalid article output key: {article_key}")
    return rank


def _status_at_least(status: str | None, expected: str) -> bool:
    order = {"pending": 0, "raw_complete": 1, "cleaned": 2, "validated": 3, "success": 4}
    return order.get(status or "pending", 0) >= order[expected]


class WebContentWorker:
    def __init__(
        self,
        config: SourceConfig,
        run_id: str | None = None,
        stop_after: int | None = None,
        force: bool = False,
    ) -> None:
        self.config = config
        self.collection_dir = config.resolved_collection_dir()
        self.run_id = run_id or make_run_id(config.name)
        self.run_dir = self.collection_dir / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events = EventLogger(self.run_dir / "events.jsonl")
        self.checkpoint = Checkpoint(self.run_dir / "checkpoint.json")
        assert config.source_path is not None
        self.manifest = RunManifest(
            self.run_dir / "run-manifest.json", self.run_id, config.source_path
        )
        self.llm = LMStudioClient(
            config.model,
            audit_path=self.run_dir / "llm-calls.jsonl",
            event_logger=self.events,
        )
        self.stop_after = stop_after
        self.force = force or bool(self.checkpoint.data.get("force_output"))
        if force and not self.checkpoint.data.get("force_output"):
            self.checkpoint.data["force_output"] = True
            self.checkpoint.save()
        self.trace_path = self.run_dir / "playwright-trace.zip"
        self.failure_html = self.run_dir / "failure-page.html"
        self.failure_screenshot = self.run_dir / "failure-screenshot.png"
        self.current_url = ""
        self.started = time.perf_counter()

    def _prepare(self) -> None:
        for relative in ("runs", "raw", "cleaned", "rewritten"):
            (self.collection_dir / relative).mkdir(parents=True, exist_ok=True)
        source_snapshot = self.run_dir / "source-snapshot.yaml"
        if not source_snapshot.exists():
            assert self.config.source_path is not None
            shutil.copy2(self.config.source_path, source_snapshot)

    def _robots(self, url: str) -> bool:
        parsed = urlparse(url)
        robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
        target = self.run_dir / "robots.txt"
        try:
            if target.exists():
                content = target.read_text(encoding="utf-8")
            else:
                response = httpx.get(robots_url, timeout=15, follow_redirects=True)
                response.raise_for_status()
                content = response.text
                target.write_text(content, encoding="utf-8")
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(content.splitlines())
            allowed = parser.can_fetch(self.config.browser.user_agent, url)
            self.events.emit("robots_check", url=url, robots_url=robots_url, allowed=allowed)
            return allowed
        except Exception as exc:
            self.events.emit("robots_check_unavailable", url=url, error=str(exc))
            return True

    async def _extract_raw(
        self,
        browser: BrowserController,
        article: SelectedArticle,
        article_key: str,
        raw_dir: Path,
    ) -> dict[str, Any]:
        if not self._robots(article.url):
            raise PermissionError(f"robots.txt disallows this worker for {article.url}")
        stage = self.run_dir / "staging" / _staging_key(article_key) / "raw"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        self.current_url = article.url
        self.events.emit("article_open", article=article_key, url=article.url)
        page = await browser.goto(article.url)
        await browser.save_current(stage / "page.html", stage / "page-screenshot.png")
        html = (stage / "page.html").read_text(encoding="utf-8")
        extracted = extract_article_from_html(html, page.url)
        if self.config.require_course_hierarchy and extracted.method != "site_lesson_content":
            raise PermissionError(
                "Login required or full lesson content unavailable; expected "
                ".ed-lesson-content and will not save a partial lesson"
            )
        manifest = await download_images(
            page,
            extracted.image_candidates,
            stage / "images" / "original",
            request_delay_seconds=self.config.browser.image_request_delay_seconds,
            filename_budget_dirs=[
                raw_dir / "images" / "original",
                self.collection_dir
                / "cleaned"
                / raw_dir.relative_to(self.collection_dir / "raw")
                / "images",
            ],
        )
        (stage / "images" / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        markdown = f"# {extracted.title}\n\n{extracted.markdown}\n"
        markdown = localize_markdown_images(markdown, manifest, "images/original")
        markdown = reconcile_downloaded_images(markdown, manifest, "images/original")
        markdown += f"\n---\n\nSource: {extracted.canonical_url}\n"
        (stage / "article.md").write_text(markdown, encoding="utf-8")
        metadata = {
            "original_url": article.url,
            "canonical_url": extracted.canonical_url,
            "homepage_url": self.config.homepage_url,
            "title": extracted.title,
            "author": extracted.author,
            "published_at": extracted.published_at,
            "fetched_at": datetime.now().astimezone().isoformat(),
            "selection_reason": article.reason,
            "page_status": browser.last_status,
            "raw_html_sha256": sha256_file(stage / "page.html"),
            "agent_version": _git_commit(),
            "lmstudio_version": _lmstudio_version(),
            "model_identifier": self.llm.actual_model_identifier(),
            "model_key": self.config.model.model_key,
            "model_quant": self.config.model.quant,
            "source_config_path": str(self.config.source_path),
            "course_hierarchy": {
                "section_number": article.section_number,
                "section_title": article.section_title,
                "lesson_number": article.lesson_number,
            }
            if article.section_number is not None
            else None,
        }
        (stage / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        report = {
            "method": extracted.method,
            "character_count": extracted.character_count,
            "candidate_images": len(manifest),
            "downloaded_images": sum(1 for item in manifest if item["download_success"]),
            "failed_images": sum(1 for item in manifest if not item["download_success"]),
        }
        (stage / "extraction-report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        missing = validate_local_image_paths(stage / "article.md")
        if missing:
            raise ValueError(f"Raw markdown contains missing/non-local image targets: {missing}")
        if raw_dir.exists():
            existing_metadata = raw_dir / "metadata.json"
            if existing_metadata.exists():
                existing_url = json.loads(existing_metadata.read_text(encoding="utf-8")).get(
                    "original_url"
                )
                if existing_url == article.url:
                    shutil.rmtree(stage.parent)
                    return {
                        "metadata": json.loads(existing_metadata.read_text(encoding="utf-8")),
                        "manifest": json.loads(
                            (raw_dir / "images" / "manifest.json").read_text(encoding="utf-8")
                        ),
                        "report": json.loads(
                            (raw_dir / "extraction-report.json").read_text(encoding="utf-8")
                        ),
                        "reused": True,
                    }
            raise FileExistsError(f"Refusing to overwrite immutable raw directory: {raw_dir}")
        raw_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, raw_dir)
        return {"metadata": metadata, "manifest": manifest, "report": report, "reused": False}

    def _load_raw(self, raw_dir: Path) -> dict[str, Any]:
        return {
            "metadata": json.loads((raw_dir / "metadata.json").read_text(encoding="utf-8")),
            "manifest": json.loads(
                (raw_dir / "images" / "manifest.json").read_text(encoding="utf-8")
            ),
            "report": json.loads((raw_dir / "extraction-report.json").read_text(encoding="utf-8")),
            "reused": True,
        }

    def _cleanup(self, article_key: str, raw_dir: Path, cleaned_dir: Path, raw: dict) -> dict:
        if cleaned_dir.exists() and (cleaned_dir / "cleanup-report.json").exists():
            return json.loads((cleaned_dir / "cleanup-report.json").read_text(encoding="utf-8"))
        stage = self.run_dir / "staging" / _staging_key(article_key) / "cleaned"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        report = cleanup_article_images(
            raw_article_dir=raw_dir,
            cleaned_article_dir=stage,
            article_title=raw["metadata"]["title"],
            source_url=raw["metadata"]["canonical_url"],
            manifest=raw["manifest"],
            llm=self.llm,
            scratch_dir=runtime_root() / "state" / self.run_id / article_key,
        )
        if cleaned_dir.exists():
            raise FileExistsError(f"Refusing to overwrite cleaned directory: {cleaned_dir}")
        cleaned_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, cleaned_dir)
        return report

    @staticmethod
    def _validate_article(raw_dir: Path, cleaned_dir: Path) -> None:
        raw_required = [
            "page.html",
            "page-screenshot.png",
            "article.md",
            "metadata.json",
            "extraction-report.json",
            "images/manifest.json",
        ]
        cleaned_required = ["article.md", "cleanup-report.json", "image-decisions.jsonl", "images"]
        missing = [str(raw_dir / item) for item in raw_required if not (raw_dir / item).exists()]
        missing += [
            str(cleaned_dir / item)
            for item in cleaned_required
            if not (cleaned_dir / item).exists()
        ]
        missing += validate_local_image_paths(raw_dir / "article.md")
        missing += validate_local_image_paths(cleaned_dir / "article.md")
        metadata = json.loads((raw_dir / "metadata.json").read_text(encoding="utf-8"))
        required_metadata = {
            "original_url",
            "canonical_url",
            "homepage_url",
            "title",
            "fetched_at",
            "selection_reason",
            "page_status",
            "raw_html_sha256",
            "agent_version",
            "lmstudio_version",
            "model_identifier",
            "source_config_path",
        }
        missing_keys = required_metadata - metadata.keys()
        if missing or missing_keys:
            raise ValueError(
                f"Validation failed; missing={missing}, metadata_keys={sorted(missing_keys)}"
            )
        if sha256_file(raw_dir / "page.html") != metadata["raw_html_sha256"]:
            raise ValueError(
                "Raw HTML hash no longer matches metadata; immutable raw data was modified"
            )

    async def run(self) -> str:
        self._prepare()
        if self.checkpoint.data.get("last_error"):
            self.checkpoint.update("resuming", last_error=None, can_resume=True)
        self.events.emit("run_started", run_id=self.run_id, source=str(self.config.source_path))
        profile = (
            default_workspace_root() / "browser-profiles" / f"{self.config.site_name}-isolated"
        )
        selected: list[SelectedArticle] = []
        article_results: list[dict] = []
        image_counts = {"candidate": 0, "keep": 0, "drop": 0, "review": 0}
        strategy = self.config.resolved_strategy()
        max_items = self.config.max_items
        news_count = max_items or 3
        limit_reached = False
        try:
            print("[1/7] Checking LM Studio...")
            actual_identifier = self.llm.actual_model_identifier()
            print(f"[2/7] Local model ready: {actual_identifier}")
            async with BrowserController(
                self.config.browser,
                profile,
                self.trace_path,
                access_state_path=self.collection_dir / "runs" / "access-policy.json",
                event_logger=self.events,
            ) as browser:
                try:
                    saved_selection = self.checkpoint.data.get("selected_articles") or []
                    if saved_selection:
                        selected = [
                            SelectedArticle.model_validate(item) for item in saved_selection
                        ]
                        print(f"[3/7] Resuming saved selection for {self.config.homepage_url}")
                    else:
                        if not self._robots(self.config.homepage_url):
                            raise PermissionError("robots.txt disallows homepage access")
                        entry_url = self.config.discovery_url or self.config.homepage_url
                        if entry_url != self.config.homepage_url and not self._robots(entry_url):
                            raise PermissionError("robots.txt disallows configured discovery URL")
                        print(f"[3/7] Opening {strategy} entry page: {entry_url}")
                        self.current_url = entry_url
                        await browser.goto(entry_url)
                        homepage_html = self.run_dir / "homepage.html"
                        homepage_shot = self.run_dir / "homepage-screenshot.png"
                        await browser.save_current(homepage_html, homepage_shot)
                        homepage_text = homepage_html.read_text(encoding="utf-8")
                        if strategy == "news":
                            candidates = collect_link_candidates(
                                homepage_text,
                                self.config.homepage_url,
                                self.config.allowed_domains,
                                limit=max(40, news_count * 4),
                            )
                            if len(candidates) < news_count:
                                raise ValueError(
                                    f"Only {len(candidates)} valid homepage candidates found; "
                                    f"requested {news_count}"
                                )
                            selection = self.llm.select_articles(
                                self.config.homepage_url,
                                make_dom_summary(candidates),
                                count=news_count,
                            )
                            selection = validate_selection_against_candidates(
                                selection, candidates, self.config.allowed_domains
                            )
                            selected = selection.articles
                        else:
                            candidates = collect_course_candidates(
                                homepage_text,
                                self.config.homepage_url,
                                self.config.allowed_domains,
                                limit=max_items + 1 if max_items is not None else 500,
                            )
                            if self.config.require_course_hierarchy and not any(
                                item.section_number is not None for item in candidates
                            ):
                                raise PermissionError(
                                    "Login required or numbered course hierarchy unavailable in "
                                    "the isolated browser profile; refusing a partial course run"
                                )
                            if not candidates:
                                raise ValueError(
                                    "No public same-course chapter links were found on the entry "
                                    "page; login or a site adapter may be required"
                                )
                            limit_reached = max_items is not None and len(candidates) > max_items
                            initial_candidates = (
                                candidates[:max_items] if max_items is not None else candidates
                            )
                            selected = [
                                SelectedArticle(
                                    rank=index,
                                    title=item.title,
                                    url=item.url,
                                    reason="Ordered public link discovered in the course scope",
                                    section_number=item.section_number,
                                    section_title=item.section_title,
                                    lesson_number=item.lesson_number,
                                )
                                for index, item in enumerate(initial_candidates, start=1)
                            ]
                        atomic_write_json(
                            self.run_dir / "selected-articles.json",
                            {
                                "strategy": strategy,
                                "items": [item.model_dump() for item in selected],
                            },
                        )
                        self.checkpoint.update(
                            "content_selected",
                            strategy=strategy,
                            selected_articles=[item.model_dump(mode="json") for item in selected],
                        )
                    print(f"[4/7] Queued {len(selected)} {strategy} content items.")
                    index = 0
                    while index < len(selected):
                        article = selected[index]
                        index += 1
                        total = len(selected)
                        article_key = _article_output_key(
                            article, self.run_id if self.force else None
                        )
                        output_path = _article_output_path(
                            article, self.run_id if self.force else None
                        )
                        raw_dir = self.collection_dir / "raw" / output_path
                        cleaned_dir = self.collection_dir / "cleaned" / output_path
                        saved_status = (
                            self.checkpoint.data.get("articles", {})
                            .get(article_key, {})
                            .get("status", "pending")
                        )
                        if self.checkpoint.completed(article_key):
                            repaired = repair_cleaned_image_copies(raw_dir, cleaned_dir)
                            if repaired:
                                self.events.emit(
                                    "cleaned_images_repaired", article=article_key, count=repaired
                                )
                            self._validate_article(raw_dir, cleaned_dir)
                            raw = self._load_raw(raw_dir)
                            cleanup = json.loads(
                                (cleaned_dir / "cleanup-report.json").read_text(encoding="utf-8")
                            )
                            print(f"[5/7] Item {index}/{total} already valid; skipped")
                            print(f"      {article.title}")
                        else:
                            print(f"[5/7] Downloading item {index}/{total}: {article.title}")
                            if _status_at_least(saved_status, "raw_complete"):
                                raw = self._load_raw(raw_dir)
                            else:
                                raw = await self._extract_raw(
                                    browser, article, article_key, raw_dir
                                )
                                completed = self.checkpoint.data.setdefault(
                                    "completed_downloads", []
                                )
                                if article_key not in completed:
                                    completed.append(article_key)
                                self.checkpoint.set_article(
                                    article_key,
                                    "raw_complete",
                                    url=article.url,
                                    raw_dir=str(raw_dir),
                                    reused=raw["reused"],
                                )
                            if strategy == "course":
                                discovered = collect_course_candidates(
                                    (raw_dir / "page.html").read_text(encoding="utf-8"),
                                    self.config.homepage_url,
                                    self.config.allowed_domains,
                                    limit=max_items + 1 if max_items is not None else 500,
                                )
                                known_urls = {item.url for item in selected}
                                for candidate in discovered:
                                    if candidate.url in known_urls:
                                        continue
                                    if max_items is not None and len(selected) >= max_items:
                                        limit_reached = True
                                        break
                                    selected.append(
                                        SelectedArticle(
                                            rank=len(selected) + 1,
                                            title=candidate.title,
                                            url=candidate.url,
                                            reason=(
                                                "Ordered public link discovered while traversing "
                                                "the course"
                                            ),
                                            section_number=candidate.section_number,
                                            section_title=candidate.section_title,
                                            lesson_number=candidate.lesson_number,
                                        )
                                    )
                                    known_urls.add(candidate.url)
                                self.checkpoint.data["selected_articles"] = [
                                    item.model_dump(mode="json") for item in selected
                                ]
                                self.checkpoint.save()
                                atomic_write_json(
                                    self.run_dir / "selected-articles.json",
                                    {
                                        "strategy": strategy,
                                        "items": [item.model_dump() for item in selected],
                                    },
                                )
                            print(
                                f"[6/7] Running image cleanup for item {index}/{len(selected)}..."
                            )
                            saved_status = self.checkpoint.data["articles"][article_key]["status"]
                            if _status_at_least(saved_status, "cleaned"):
                                cleanup = json.loads(
                                    (cleaned_dir / "cleanup-report.json").read_text(
                                        encoding="utf-8"
                                    )
                                )
                                repaired = repair_cleaned_image_copies(raw_dir, cleaned_dir)
                                if repaired:
                                    self.events.emit(
                                        "cleaned_images_repaired",
                                        article=article_key,
                                        count=repaired,
                                    )
                            else:
                                cleanup = self._cleanup(article_key, raw_dir, cleaned_dir, raw)
                                completed_cleanup = self.checkpoint.data.setdefault(
                                    "completed_cleanup", []
                                )
                                if article_key not in completed_cleanup:
                                    completed_cleanup.append(article_key)
                                self.checkpoint.set_article(
                                    article_key,
                                    "cleaned",
                                    cleaned_dir=str(cleaned_dir),
                                    cleanup_counts=cleanup["counts"],
                                )
                            self._validate_article(raw_dir, cleaned_dir)
                            self.checkpoint.set_article(article_key, "success")
                        counts = cleanup["counts"]
                        image_counts["candidate"] += len(raw["manifest"])
                        for key in ("keep", "drop", "review"):
                            image_counts[key] += int(counts[key])
                        article_results.append(
                            {
                                "rank": article.rank,
                                "title": raw["metadata"]["title"],
                                "url": raw["metadata"]["canonical_url"],
                                "status": "success",
                                "raw_dir": str(raw_dir),
                                "cleaned_dir": str(cleaned_dir),
                                "candidate_images": len(raw["manifest"]),
                                "cleanup_counts": counts,
                                "section_number": article.section_number,
                                "section_title": article.section_title,
                                "lesson_number": article.lesson_number,
                            }
                        )
                        self.events.emit(
                            "article_complete",
                            article=article_key,
                            index=index,
                            image_counts=counts,
                            elapsed_seconds=round(time.perf_counter() - self.started, 3),
                        )
                        if self.stop_after == index:
                            self.checkpoint.record_error(
                                "CONTROLLED_STOP",
                                f"Stopped after article {index} by debug option",
                                True,
                            )
                            raise ControlledStop(
                                f"Debug stop after article {index}; resume run {self.run_id}"
                            )
                    if limit_reached and max_items is not None:
                        raise ValueError(
                            f"Course traversal reached configured max_items={max_items} before "
                            "the discovery queue was exhausted; increase max_items explicitly"
                        )
                except Exception:
                    try:
                        await browser.save_current(self.failure_html, self.failure_screenshot)
                    except Exception:
                        pass
                    try:
                        await browser.save_trace()
                    except Exception:
                        pass
                    raise
            elapsed = time.perf_counter() - self.started
            self.manifest.finish(article_results, image_counts, elapsed)
            self.checkpoint.update("complete", can_resume=False, last_error=None)
            summary = [
                f"# Run {self.run_id}",
                "",
                "Status: success",
                f"Elapsed seconds: {elapsed:.3f}",
                f"Images: {json.dumps(image_counts)}",
                "",
            ]
            summary.extend(f"- {item['title']} — {item['url']}" for item in article_results)
            (self.run_dir / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
            print(f"[7/7] Validating output... success. Checkpoint: {self.checkpoint.path}")
            return self.run_id
        except ControlledStop:
            self.manifest.data["status"] = "interrupted"
            self.manifest.save()
            raise
        except Exception as exc:
            error_type = classify_error(exc)
            self.checkpoint.record_error(error_type, str(exc), True)
            self.manifest.fail(error_type, str(exc))
            bundle = create_failure_bundle(
                failure_root=runtime_root() / "failures",
                exc=exc,
                checkpoint_path=self.checkpoint.path,
                events_path=self.run_dir / "events.jsonl",
                current_url=self.current_url,
                page_html_path=self.failure_html,
                screenshot_path=self.failure_screenshot,
                trace_path=self.trace_path,
                last_llm_request=self.llm.last_request,
                last_llm_response=self.llm.last_response,
            )
            run_failure = self.run_dir / "failures" / bundle.name
            run_failure.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(bundle, run_failure)
            self.events.emit("run_failed", error_type=error_type, failure_bundle=str(bundle))
            raise RuntimeError(
                f"{error_type}: {exc}. Failure bundle: {bundle}. Resume: wca resume {self.run_id}"
            ) from exc


def run_worker(
    config: SourceConfig,
    run_id: str | None = None,
    stop_after: int | None = None,
    force: bool = False,
) -> str:
    return asyncio.run(
        WebContentWorker(config, run_id=run_id, stop_after=stop_after, force=force).run()
    )
