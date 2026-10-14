"""pywebview が使えないとき（または --browser 指定時）に、ブラウザで画面を開くための小さなサーバー。

127.0.0.1 だけで待ち受け、起動ごとに作るトークンを知っている画面からしか API を呼べない。
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

WEB_DIR = Path(__file__).resolve().parent / "web"


def make_server(api, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    token = secrets.token_urlsafe(16)
    methods = {name for name in dir(api) if not name.startswith("_") and callable(getattr(api, name))}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 静かにする
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("", "/"):
                path = "/index.html"
            target = (WEB_DIR / path.lstrip("/")).resolve()
            if WEB_DIR not in target.parents or not target.is_file():
                self._send(404, b"not found", "text/plain")
                return
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/") or self.headers.get("X-Imiwatch-Token") != token:
                self._send(403, b'{"ok": false, "error": "forbidden"}', "application/json")
                return
            name = path[len("/api/"):]
            if name not in methods:
                self._send(404, b'{"ok": false, "error": "unknown method"}', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                args = body.get("args") or []
            except (ValueError, json.JSONDecodeError):
                self._send(400, b'{"ok": false, "error": "bad request"}', "application/json")
                return
            result = getattr(api, name)(*args)
            self._send(200, json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"), "application/json")

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{httpd.server_address[1]}/index.html?token={token}"
    return httpd, url


def serve_in_thread(api, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    httpd, url = make_server(api, port=port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, url
