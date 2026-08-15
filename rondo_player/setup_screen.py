"""Local activation screen displayed before a player has credentials."""

from __future__ import annotations

import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SetupScreen:
    """Serve a tiny local page whose code can be updated by the agent."""

    def __init__(self, port: int = 8765) -> None:
        self.port = port
        self.code = "Verbinding maken…"
        self.status = "De player maakt contact met Rondo."
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def update(self, code: str, status: str) -> None:
        self.code = code
        self.status = status

    def start(self) -> None:
        screen = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = screen.render().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def render(self) -> str:
        code = html.escape(self.code)
        status = html.escape(self.status)
        return f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><meta http-equiv="refresh" content="3">
<meta name="viewport" content="width=device-width"><title>Rondo Player</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#020617;color:#fff;font-family:system-ui,sans-serif}}
main{{text-align:center;padding:6vw}}p{{color:#a5f3fc;font-size:2vw;text-transform:uppercase;letter-spacing:.2em}}h1{{font-size:5vw;margin:.2em 0}}.code{{font:700 7vw ui-monospace,monospace;letter-spacing:.08em;color:#67e8f9;margin:.5em 0}}.status{{color:#cbd5e1;font-size:1.8vw;text-transform:none;letter-spacing:0}}
</style></head><body><main><p>Rondo Club TV</p><h1>Player activeren</h1><div class="code">{code}</div><p class="status">{status}</p></main></body></html>"""
