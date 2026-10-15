from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from web_content_agent.paths import project_root

COMPANION_PROTOCOL_VERSION = 1
_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")


def read_companion_extension_version(manifest_path: Path | None = None) -> str:
    """Read the unpacked extension version from disk on every handshake.

    The desktop app is long-lived while extension files change during local
    development, so this value must not be cached at import or app startup.
    """

    path = manifest_path or project_root() / "chrome_extension" / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = str(payload.get("version", "")).strip()
    if not _VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"invalid companion extension version in {path}: {version!r}")
    return version


def negotiate_companion(
    *,
    observed_version: str,
    observed_protocol: int | None,
    expected_version: str,
) -> dict[str, Any]:
    common = {
        "observed_extension_version": observed_version or None,
        "expected_extension_version": expected_version,
        "protocol_version": COMPANION_PROTOCOL_VERSION,
    }
    if not observed_version or observed_protocol is None:
        return {"action": "manual_reload_required", **common, "compatible": False}
    if observed_protocol != COMPANION_PROTOCOL_VERSION:
        return {
            "action": "incompatible_extension",
            **common,
            "observed_protocol_version": observed_protocol,
            "compatible": False,
        }
    if observed_version != expected_version:
        return {"action": "reload_extension", **common, "compatible": False}
    return {"action": "ready", **common, "compatible": True}
