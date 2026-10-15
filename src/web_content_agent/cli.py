from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
import typer
import yaml
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright
from pydantic import BaseModel

from .article_selection import safe_slug
from .checkpoint import Checkpoint
from .config import ModelConfig, load_source_config
from .context_handoff import new_chat_prompt, write_context_handoff
from .lmstudio import LMStudioClient, ModelServerUnavailable
from .logging_setup import configure_logging
from .markdown_output import validate_local_image_paths
from .paths import (
    default_workspace_root,
    ensure_runtime_dirs,
    get_desktop_path,
    project_root,
    runtime_root,
)
from .worker import ControlledStop, run_worker, sha256_file

app = typer.Typer(no_args_is_help=True, help="Local Web Content Agent worker and diagnostics")


class SmokeJSON(BaseModel):
    status: Literal["ok"]
    number: Literal[7]


class VisionSmoke(BaseModel):
    description: str
    shapes: list[str]
    text_seen: list[str]


def _find_executable(name: str, fallback: Path) -> str:
    found = shutil.which(name)
    if found:
        return found
    if fallback.exists():
        return str(fallback)
    raise FileNotFoundError(f"{name} was not found in PATH or expected location {fallback}")


def _lms_executable() -> str:
    return _find_executable("lms", Path.home() / ".lmstudio" / "bin" / "lms.exe")


def _run_lms(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_lms_executable(), *args], capture_output=True, text=True, timeout=60, check=False
    )


def _localhost_listener(port: int) -> tuple[bool, list[str]]:
    result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
    listeners = [
        line.strip()
        for line in result.stdout.splitlines()
        if f":{port}" in line and "LISTENING" in line.upper()
    ]
    unsafe = []
    for line in listeners:
        parts = line.split()
        local_address = parts[1] if len(parts) >= 2 else ""
        if local_address.startswith("0.0.0.0:") or local_address.startswith("[::]:"):
            unsafe.append(line)
    return bool(listeners) and not unsafe, listeners


def _find_run(run_id: str) -> Path:
    matches = list(default_workspace_root().glob(f"sites/*/*/runs/{run_id}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one run named {run_id}; found {len(matches)}")
    return matches[0]


async def _interactive_browser_login(source: Path) -> None:
    config = load_source_config(source)
    profile = default_workspace_root() / "browser-profiles" / f"{config.site_name}-isolated"
    target = config.discovery_url or config.homepage_url
    profile.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=profile,
            headless=False,
            viewport={"width": 1440, "height": 1200},
            locale="en-US",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(target, wait_until="domcontentloaded")
        print("Sign in normally in the isolated Chromium window.")
        print("Do not paste credentials into this terminal; they stay in the browser profile.")
        await asyncio.to_thread(
            input, "After the full lesson and sidebar are visible, press Enter: "
        )
        await page.wait_for_timeout(1000)
        lesson_count = await page.locator(".ed-lesson-content").count()
        section_count = await page.locator("nav h5").count()
        await context.close()
    if not lesson_count or not section_count:
        raise RuntimeError(
            "Login verification failed: the isolated profile does not expose both the full "
            "lesson and numbered curriculum"
        )
    print(f"Isolated profile ready: lesson content detected, {section_count} sections visible.")


def _validate_manifest(run_dir: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = run_dir / "run-manifest.json"
    if not manifest_path.exists():
        return [f"Missing {manifest_path}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for article in manifest.get("articles", []):
        raw_dir = Path(article["raw_dir"])
        cleaned_dir = Path(article["cleaned_dir"])
        for item in (
            raw_dir / "page.html",
            raw_dir / "page-screenshot.png",
            raw_dir / "article.md",
            raw_dir / "metadata.json",
            raw_dir / "extraction-report.json",
            raw_dir / "images" / "manifest.json",
            cleaned_dir / "article.md",
            cleaned_dir / "cleanup-report.json",
            cleaned_dir / "image-decisions.jsonl",
        ):
            if not item.exists():
                errors.append(f"Missing {item}")
        if (raw_dir / "article.md").exists():
            errors.extend(
                f"Invalid raw image target: {item}"
                for item in validate_local_image_paths(raw_dir / "article.md")
            )
        if (cleaned_dir / "article.md").exists():
            errors.extend(
                f"Invalid cleaned image target: {item}"
                for item in validate_local_image_paths(cleaned_dir / "article.md")
            )
        if (raw_dir / "metadata.json").exists() and (raw_dir / "page.html").exists():
            metadata = json.loads((raw_dir / "metadata.json").read_text(encoding="utf-8"))
            if metadata.get("raw_html_sha256") != sha256_file(raw_dir / "page.html"):
                errors.append(f"Raw HTML immutability check failed: {raw_dir}")
    if manifest.get("status") != "success":
        errors.append(f"Manifest status is {manifest.get('status')!r}, not 'success'")
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    selected_count = len(checkpoint.get("selected_articles", []))
    if selected_count < 1 or len(manifest.get("articles", [])) != selected_count:
        errors.append(
            "Manifest item count does not match the non-empty checkpoint selection "
            f"({len(manifest.get('articles', []))} != {selected_count})"
        )
    return errors


@app.command()
def doctor(
    smoke: bool = typer.Option(True, "--smoke/--no-smoke", help="Run local text/JSON/vision calls"),
) -> None:
    """Verify Desktop, tools, localhost binding, loaded model, and optional model smoke tests."""
    configure_logging()
    ensure_runtime_dirs()
    print(f"Desktop: {get_desktop_path()}")
    print(f"Project: {project_root()}")
    lms = _lms_executable()
    print(f"lms: {lms}")
    version = _run_lms("--version")
    if version.returncode:
        raise typer.Exit(version.returncode)
    print(version.stdout.strip())
    status = _run_lms("server", "status")
    status_output = status.stdout + status.stderr
    if status.returncode or "running" not in status_output.lower():
        typer.echo(status.stdout + status.stderr, err=True)
        raise typer.Exit(2)
    local_only, listeners = _localhost_listener(1234)
    if not local_only:
        typer.echo(f"Unsafe or missing listener for port 1234: {listeners}", err=True)
        raise typer.Exit(3)
    print(f"LM Studio listener: localhost only ({listeners[0]})")
    config = ModelConfig(
        base_url=os.getenv("WCA_LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"),
        identifier=os.getenv("WCA_MODEL_IDENTIFIER", "local-agent-vlm"),
    )
    client = LMStudioClient(config, runtime_root() / "logs" / "doctor-llm-calls.jsonl")
    models = client.models()
    actual = client.actual_model_identifier()
    print(f"Loaded model identifier: {actual}")
    print(f"Models endpoint: {len(models)} model(s)")
    runtime_model = client.loaded_model_details()
    quant = runtime_model.get("quantization")
    context_length = runtime_model.get("loaded_context_length")
    vision = runtime_model.get("type") == "vlm"
    if quant != config.quant:
        raise RuntimeError(f"Expected quant {config.quant}, LM Studio reports {quant!r}")
    if context_length != config.context_length:
        raise RuntimeError(
            f"Expected context {config.context_length}, LM Studio reports {context_length!r}"
        )
    if vision is not True:
        raise RuntimeError("Loaded model does not report vision capability")
    print(f"Loaded model: {runtime_model.get('id')} ({quant}, context {context_length}, vision)")
    unavailable = LMStudioClient(config.model_copy(update={"base_url": "http://127.0.0.1:1/v1"}))
    try:
        unavailable.models(timeout=0.2)
        raise RuntimeError("Unavailable-server diagnostic unexpectedly connected")
    except ModelServerUnavailable as exc:
        print(f"Unavailable-server diagnostic: clear error ({type(exc).__name__})")
    if smoke:
        text = client.text_smoke()
        if "LOCAL_OK" not in text:
            raise RuntimeError(f"Text smoke test returned unexpected content: {text!r}")
        print("Text smoke: passed")
        structured = client.structured(
            SmokeJSON,
            [
                {
                    "role": "user",
                    "content": 'Return JSON with status exactly "ok" and number exactly 7.',
                }
            ],
            "smoke_json",
            max_tokens=100,
        )
        print(f"JSON smoke: passed ({structured.model_dump_json()})")
        image_path = runtime_root() / "state" / "vision-smoke.png"
        image = Image.new("RGB", (640, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((60, 90, 280, 270), fill="royalblue", outline="black", width=5)
        draw.text((60, 25), "LOCAL VISION TEST", fill="black")
        draw.text((330, 130), "Three short lines\nfrom WebContentAgent\nnumber 7", fill="black")
        image.save(image_path)
        import base64

        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        vision = client.structured(
            VisionSmoke,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this locally generated test image."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }
            ],
            "smoke_vision",
            max_tokens=300,
        )
        if not vision.description:
            raise RuntimeError("Vision smoke returned an empty description")
        print(f"Vision smoke: passed ({vision.description[:100]})")
        timeout_client = LMStudioClient(config.model_copy(update={"timeout_seconds": 0.001}))
        started = time.perf_counter()
        try:
            timeout_client.text_smoke()
            print(
                "Timeout smoke: server returned before 1 ms threshold (finite timeout configured)"
            )
        except httpx.TimeoutException:
            print(
                f"Timeout smoke: passed ({time.perf_counter() - started:.3f}s, no permanent wait)"
            )
    print("Doctor: all checks passed")


@app.command("run")
def run_command(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True),
    force: bool = typer.Option(
        False, help="Reserved explicit override; raw files remain immutable"
    ),
    stop_after: int | None = typer.Option(None, min=1, hidden=True),
) -> None:
    """Run a source configuration as a new checkpointed job."""
    if force:
        typer.echo(
            "--force creates a fresh run but never overwrites finalized raw article directories."
        )
    config = load_source_config(source)
    try:
        run_id = run_worker(config, stop_after=stop_after, force=force)
        print(f"Run complete: {run_id}")
    except ControlledStop as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(75) from exc


@app.command("login")
def login_command(
    source: Path = typer.Argument(..., exists=True, dir_okay=False, resolve_path=True),
) -> None:
    """Open an isolated headed browser so the user can authorize a course session."""
    asyncio.run(_interactive_browser_login(source))


@app.command()
def resume(
    run_id: str,
    reclean: bool = typer.Option(
        False, "--reclean", help="Rebuild cleaned output from immutable raw data"
    ),
) -> None:
    """Continue a failed/interrupted run from its atomic checkpoint."""
    run_dir = _find_run(run_id)
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    source_path = Path(manifest["source_config"])
    if not source_path.exists():
        source_path = run_dir.parents[1] / "source.yaml"
    config = load_source_config(source_path)
    if reclean:
        checkpoint = Checkpoint(run_dir / "checkpoint.json")
        archive = run_dir / "replaced-cleaned" / datetime.now().strftime("%Y%m%dT%H%M%S")
        for key, article in checkpoint.data.get("articles", {}).items():
            cleaned_value = article.get("cleaned_dir")
            if not cleaned_value:
                continue
            cleaned_dir = Path(cleaned_value).resolve()
            if not cleaned_dir.is_relative_to(default_workspace_root().resolve()):
                raise ValueError(
                    f"Refusing to archive cleaned directory outside workspace: {cleaned_dir}"
                )
            if cleaned_dir.exists():
                archive.mkdir(parents=True, exist_ok=True)
                shutil.move(str(cleaned_dir), str(archive / key))
            article["status"] = "raw_complete"
            article.pop("cleanup_counts", None)
        checkpoint.data["completed_cleanup"] = []
        checkpoint.update("reclean_requested", can_resume=True, last_error=None)
        print(f"Previous cleaned output archived under: {archive}")
    result = run_worker(config, run_id=run_id)
    print(f"Resume complete: {result}")


@app.command()
def status(run_id: str) -> None:
    """Print concise checkpoint and manifest status."""
    run_dir = _find_run(run_id)
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    typer.echo(
        json.dumps(
            {
                "run_id": run_id,
                "manifest_status": manifest.get("status"),
                "current_stage": checkpoint.get("current_stage"),
                "articles": checkpoint.get("articles"),
                "last_error": checkpoint.get("last_error"),
                "can_resume": checkpoint.get("can_resume"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@app.command()
def validate(run_id: str) -> None:
    """Validate required files, local Markdown images, raw hashes, and selected-item success."""
    errors = _validate_manifest(_find_run(run_id))
    if errors:
        typer.echo("\n".join(errors), err=True)
        raise typer.Exit(1)
    print(f"Validation passed: {run_id}")


@app.command()
def handoff(run_id: str) -> None:
    """Write a compact recovery snapshot and print a prompt for a fresh LM Studio Chat."""
    run_dir = _find_run(run_id)
    json_path, markdown_path, snapshot = write_context_handoff(run_dir)
    print(f"Context snapshot: {json_path}")
    print(f"Human summary: {markdown_path}")
    print("\nNew Chat prompt:\n")
    print(new_chat_prompt(snapshot, json_path))


@app.command("new-source")
def new_source(url: str = typer.Option(...), name: str = typer.Option(...)) -> None:
    """Create a provenance-ready website/collection source without copying the framework."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise typer.BadParameter("--url must be an absolute HTTP(S) URL")
    site_name = safe_slug(parsed.hostname.removeprefix("www."))
    collection_name = safe_slug(name)
    site_dir = default_workspace_root() / "sites" / site_name
    collection = site_dir / collection_name
    source_path = collection / "source.yaml"
    if source_path.exists():
        raise FileExistsError(f"Source already exists: {source_path}")
    for relative in ("runs", "raw", "cleaned", "rewritten"):
        (collection / relative).mkdir(parents=True, exist_ok=True)
    site_config = site_dir / "site.yaml"
    if not site_config.exists():
        site_config.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "site_name": site_name,
                    "base_url": f"{parsed.scheme}://{parsed.netloc}",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    source_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "name": collection_name,
                "site_name": site_name,
                "homepage_url": url,
                "allowed_domains": [parsed.hostname.removeprefix("www.")],
                "strategy": "auto",
                "max_items": None,
                "model": ModelConfig().model_dump(mode="json"),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (collection / "README.md").write_text(
        f"# {name}\n\nSource: {url}\n\nRaw output is immutable after finalization.\n",
        encoding="utf-8",
    )
    (collection / "rewritten" / "README.md").write_text(
        "This directory is reserved for later high-quality rewrites; "
        "the extraction demo does not write here.\n",
        encoding="utf-8",
    )
    print(source_path)


if __name__ == "__main__":
    app()
