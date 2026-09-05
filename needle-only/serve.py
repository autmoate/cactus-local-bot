"""Minimaler HTTP-Server für ICS-Export v4.1.
GET /            → Status-Seite
GET /ics/{t}.ics → ICS-Feed (ETag, 304 bei If-None-Match)
Token aus .env (ICS_TOKEN) oder auto-generiert.
Start: uv run python needle-only/serve.py [--port 8765]"""
import os
import re
import secrets
import sys
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ics import render_ics, etag_for  # noqa: E402
from orga import Orga  # noqa: E402


def get_token() -> str:
    """ICS_TOKEN aus .env lesen oder generieren + in .env speichern."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    token = env.get("ICS_TOKEN") or os.environ.get("ICS_TOKEN")
    if token:
        return token
    token = secrets.token_urlsafe(16)
    if env_path.exists():
        content = env_path.read_text()
        if "ICS_TOKEN=" not in content:
            with open(env_path, "a") as f:
                f.write(f"ICS_TOKEN={token}\n")
            print(f"ICS_TOKEN generiert und in .env gespeichert.")
    return token


class Handler(BaseHTTPRequestHandler):
    server_version = "orga-ics/4.1"

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._respond(200, "text/plain; charset=utf-8",
                          f"orga ICS-Server v4.1\n"
                          f"Feed: http://<host>:{self.server.server_port}"
                          f"/ics/{self.server.token}.ics\n")
            return
        m = re.match(r"^/ics/([A-Za-z0-9_-]+)\.ics$", path)
        if not m or m.group(1) != self.server.token:
            self._respond(403 if m else 404, "text/plain; charset=utf-8",
                          "Verboten.\n" if m else "Nicht gefunden.\n")
            return
        ics = render_ics(self.server.orga)
        etag = etag_for(ics)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        body = ics.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified",
                         formatdate(usegmt=True))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _respond(self, code, ctype, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[ics] " + (fmt % args) + "\n")


def create_server(orga, port=None):
    """ICS-Server-Instanz erstellen (für run.py --ics-export Flag)."""
    if port is None:
        port = int(os.environ.get("ICS_PORT", "8765"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.orga = orga
    server.token = get_token()
    return server


def main():
    import argparse
    ap = argparse.ArgumentParser(description="ICS-Export-Server (allein lauffähig)")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("ICS_PORT", "8765")))
    args = ap.parse_args()
    server = create_server(None, args.port)
    # Bei alleinigem Start: eigene Orga-Instanz
    from modules.config import load_config
    cfg = load_config()
    server.orga = Orga(cfg.database_url)
    print(f"ICS-Server läuft auf Port {args.port}")
    print(f"Feed-URL: http://<rpis-ip>:{args.port}/ics/{server.token}.ics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer gestoppt.")


if __name__ == "__main__":
    main()
