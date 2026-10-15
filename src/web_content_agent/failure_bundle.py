from __future__ import annotations

import json
import re
import shutil
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_setup import redact

ERROR_TYPES = {
    "TRANSIENT_NETWORK",
    "LOGIN_REQUIRED",
    "CAPTCHA_REQUIRED",
    "PAGE_STRUCTURE_UNKNOWN",
    "MODEL_SERVER_ERROR",
    "MODEL_OUTPUT_INVALID",
    "BROWSER_CRASH",
    "EXTRACTION_FAILED",
    "CODE_BUG",
    "DAILY_PAGE_LIMIT",
}


def classify_error(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "dailypagelimit" in name or "daily page limit" in message:
        return "DAILY_PAGE_LIMIT"
    if "captcha" in message:
        return "CAPTCHA_REQUIRED"
    if "login" in message or "sign in" in message:
        return "LOGIN_REQUIRED"
    if "modelserver" in name or "lm studio" in message or "model server" in message:
        return "MODEL_SERVER_ERROR"
    if "modeloutput" in name or "validation" in message and "json" in message:
        return "MODEL_OUTPUT_INVALID"
    http_transient = re.search(r"(?:http(?: status)?\s*|status[=: ]+)(429|502|503)\b", message)
    if "timeout" in name or http_transient:
        return "TRANSIENT_NETWORK"
    if "playwright" in name or "browser" in message and "closed" in message:
        return "BROWSER_CRASH"
    if "extract" in message or "article body" in message:
        return "EXTRACTION_FAILED"
    return "CODE_BUG"


def _copy_or_placeholder(source: Path | None, destination: Path, placeholder: str = "") -> None:
    if source and source.exists():
        shutil.copy2(source, destination)
    else:
        destination.write_text(placeholder, encoding="utf-8")


def create_failure_bundle(
    *,
    failure_root: Path,
    exc: BaseException,
    checkpoint_path: Path,
    events_path: Path,
    current_url: str,
    page_html_path: Path | None,
    screenshot_path: Path | None,
    trace_path: Path | None,
    last_llm_request: dict[str, Any] | None,
    last_llm_response: str | None,
) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S%z")
    bundle = failure_root / stamp
    suffix = 2
    while bundle.exists():
        bundle = failure_root / f"{stamp}-{suffix}"
        suffix += 1
    bundle.mkdir(parents=True)
    error_type = classify_error(exc)
    error = {
        "error_type": error_type,
        "exception": type(exc).__name__,
        "message": str(exc),
        "timestamp": datetime.now().astimezone().isoformat(),
        "resumable": error_type not in {"CAPTCHA_REQUIRED", "LOGIN_REQUIRED"},
    }
    (bundle / "error.json").write_text(json.dumps(error, indent=2), encoding="utf-8")
    (bundle / "traceback.txt").write_text(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), encoding="utf-8"
    )
    if events_path.exists():
        lines = events_path.read_text(encoding="utf-8").splitlines()[-100:]
        (bundle / "recent-events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        (bundle / "recent-events.jsonl").write_text("", encoding="utf-8")
    (bundle / "current-url.txt").write_text(current_url, encoding="utf-8")
    _copy_or_placeholder(page_html_path, bundle / "page.html", "<!-- unavailable -->")
    _copy_or_placeholder(screenshot_path, bundle / "screenshot.png")
    if trace_path and trace_path.exists():
        shutil.copy2(trace_path, bundle / "playwright-trace.zip")
    else:
        with zipfile.ZipFile(bundle / "playwright-trace.zip", "w") as archive:
            archive.writestr("unavailable.txt", "Trace was unavailable before browser failure.")
    (bundle / "last-llm-request.json").write_text(
        json.dumps(redact(last_llm_request or {}), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (bundle / "last-llm-response.txt").write_text(last_llm_response or "", encoding="utf-8")
    _copy_or_placeholder(checkpoint_path, bundle / "checkpoint.json", "{}")
    return bundle
