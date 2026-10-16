"""Local Companion pairing, separate from browser/account credentials."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_pairing_token(root: Path | None = None) -> str:
    path = (root or project_root()) / "runtime" / "state" / "chatgpt-pairing.json"
    try:
        token = json.loads(path.read_text(encoding="utf-8"))["token"]
    except (OSError, KeyError, ValueError) as exc:
        raise RuntimeError(
            "Run python -m web_content_agent.chatgpt_pairing, then reload Companion"
        ) from exc
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("Invalid Companion pairing token; re-pair locally")
    return token


def _write_extension_asset(asset: Path, global_name: str, token: str) -> None:
    asset.parent.mkdir(parents=True, exist_ok=True)
    temporary = asset.with_suffix(".js.tmp")
    temporary.write_text(
        "// Generated local pairing. Never commit or share.\nself."
        + global_name
        + " = "
        + json.dumps({"token": token})
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(asset)


def prepare_quiz_pairing(root: Path | None = None) -> str:
    """Return the per-install token for the optional 1point3acres desktop app.

    A missing or malformed token (including the fixed token used by early
    private builds) is replaced with a fresh random
    one, and the matching extension asset is regenerated. Reload the extension
    after a replacement so its requests carry the new token.
    """
    root = root or project_root()
    path = root / "runtime" / "state" / "quiz-pairing.json"
    token = ""
    try:
        token = json.loads(path.read_text(encoding="utf-8"))["token"]
    except (OSError, KeyError, TypeError, ValueError):
        token = ""
    asset = root / "chrome_extension" / "quiz-pairing.js"
    valid = isinstance(token, str) and re.fullmatch(r"[0-9a-f]{64}", token) is not None
    if not valid:
        token = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps({"token": token}), encoding="utf-8")
        temporary.replace(path)
    if not valid or not asset.is_file() or token not in asset.read_text(encoding="utf-8"):
        _write_extension_asset(asset, "QuizPairing", token)
    return token


def prepare_pairing(root: Path | None = None) -> None:
    root = root or project_root()
    path = root / "runtime" / "state" / "chatgpt-pairing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # Exclusive creation: never rotate a working pairing during reinstall.
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump({"token": secrets.token_hex(32)}, handle)
        except FileExistsError:
            pass
    token = load_pairing_token(root)
    _write_extension_asset(
        root / "chrome_extension" / "chatgpt-pairing.js", "ChatGptPairing", token
    )
    # The optional 1point3acres lane is paired only once its desktop app has run.
    if (root / "runtime" / "state" / "quiz-pairing.json").is_file():
        prepare_quiz_pairing(root)


if __name__ == "__main__":
    prepare_pairing()
    print("Local Companion pairing prepared (secret not displayed). Reload Companion if needed.")
