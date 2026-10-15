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
    asset = root / "chrome_extension" / "chatgpt-pairing.js"
    asset.parent.mkdir(parents=True, exist_ok=True)
    temporary = asset.with_suffix(".js.tmp")
    temporary.write_text(
        "// Generated local pairing. Never commit or share.\nself.ChatGptPairing = "
        + json.dumps({"token": token})
        + ";\n",
        encoding="utf-8",
    )
    temporary.replace(asset)
    # Preserve an independently paired optional quiz lane without embedding its
    # local token in the shared worker. Absence does not prevent AskGPT startup.
    quiz_path = root / "runtime" / "state" / "quiz-pairing.json"
    if quiz_path.is_file():
        quiz_token = json.loads(quiz_path.read_text(encoding="utf-8"))["token"]
        if not isinstance(quiz_token, str) or not quiz_token:
            raise ValueError("Invalid optional quiz pairing")
        quiz_asset = root / "chrome_extension" / "quiz-pairing.js"
        quiz_temporary = quiz_asset.with_suffix(".js.tmp")
        quiz_temporary.write_text(
            "// Generated local pairing. Never commit or share.\nself.QuizPairing = "
            + json.dumps({"token": quiz_token})
            + ";\n",
            encoding="utf-8",
        )
        quiz_temporary.replace(quiz_asset)


if __name__ == "__main__":
    prepare_pairing()
    print("Local Companion pairing prepared (secret not displayed). Reload Companion if needed.")
