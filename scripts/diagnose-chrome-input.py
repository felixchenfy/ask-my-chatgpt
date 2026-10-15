"""Test real Companion input on an inert localhost page; no quiz/model requests.

Temporarily replace the idle App's listener, then restore the App. Never change
the daily ledger or use a forum submission permit. Results are private diagnostics.
"""

from __future__ import annotations

import importlib.util
import json
import secrets
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from web_content_agent.onepoint3acres.chrome_bridge import (
    BridgeHandler,
    BridgeServer,
    ChromeBridge,
)
from web_content_agent.paths import project_root


class InputDiagnosticBridge(ChromeBridge):
    def __init__(self) -> None:
        super().__init__(lambda message: print(message, flush=True))
        self.diagnostic_id = secrets.token_hex(12)
        self.dispatched = False
        self.finished = threading.Event()
        self.report: dict | None = None

    def claim_command(self, **kwargs: object) -> dict:
        response = super().claim_command(**kwargs)
        if response.get("action") == "idle":
            with self._lock:
                if not self.dispatched:
                    self.dispatched = True
                    return {"action": "diagnose_input", "diagnostic_id": self.diagnostic_id}
        return response


class InputDiagnosticHandler(BridgeHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/diagnostics/input-fixture":
            fixture = project_root() / "tests/fixtures/onepoint3acres/trusted-input-local.html"
            body = fixture.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "frame-ancestors 'none'; form-action 'none'",
            )
            self.end_headers()
            self.wfile.write(body)
            return
        if path != "/command":
            self._json(404, {"error": "diagnostic-only server"})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            super().do_POST()  # Drain the bounded body before returning 403 on Windows.
            return
        # Drain even denied routes: unread body bytes can reset the Windows socket
        # before the client receives the 404. Parsing does not authorize a command.
        try:
            body = self._body()
        except ValueError as error:
            self._json(400, {"error": str(error)})
            return
        if self.path != "/diagnostics/input-result":
            self._json(404, {"error": "diagnostic-only server"})
            return
        bridge = self.server.bridge
        if body.get("diagnostic_id") != bridge.diagnostic_id or bridge.finished.is_set():
            self._json(409, {"error": "stale diagnostic result"})
            return
        bridge.report = body
        self._json(200, {"ok": True})
        bridge.finished.set()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "daily_review", Path(__file__).with_name("codex-daily-review.py")
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    restore = False
    try:
        status = helper.request("/status?log_limit=0")
        if status.get("running"):
            raise RuntimeError("App is busy; continue that run before diagnosing")
        if helper.request("/close", {}).get("accepted") is not True:
            raise RuntimeError("App refused idle close; do not interrupt it")
        restore = True
        for _ in range(30):
            time.sleep(0.2)
            try:
                helper.request("/status?log_limit=0")
            except httpx.ConnectError:
                break
            except httpx.ReadError:
                continue  # Windows may reset one connection while the listener closes.
        else:
            raise RuntimeError("App has not released its listener")
    except httpx.ConnectError:
        pass
    except Exception:
        if restore:
            helper.ensure_app()
        raise

    bridge = InputDiagnosticBridge()
    server = None
    try:
        server = BridgeServer(bridge)
        server.RequestHandlerClass = InputDiagnosticHandler
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print("Waiting for Companion's next poll; local input test only.", flush=True)
        if not bridge.finished.wait(150):
            raise TimeoutError("No diagnostic result within 150 seconds; not repeated")
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H%M%S")
        output = project_root() / ".local/debug/onepoint3acres" / f"chrome-input-{stamp}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(bridge.report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": bridge.report.get("ok"),
                    "report": str(output),
                    "cases": [
                        {
                            "name": item["name"],
                            "passed": item["passed"],
                            "before": item["before"],
                            "after": item["after"],
                            "error": item.get("error"),
                        }
                        for item in bridge.report.get("cases", [])
                    ],
                    "error": bridge.report.get("error"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if restore:
            helper.ensure_app()
    if not bridge.report or bridge.report.get("ok") is not True:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
