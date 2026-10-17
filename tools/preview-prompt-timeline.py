"""Serve only the timeline concept and its pinned toolkit assets."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "/": ("docs/concepts/prompt-variant-timeline.html", "text/html; charset=utf-8"),
    "/toolkit/core.js": ("web/js/shared/toolkit/core.js", "text/javascript"),
    "/toolkit/controls.css": ("web/js/shared/toolkit/controls.css", "text/css"),
}


class PreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        target = FILES.get(urlsplit(self.path).path)
        if target is None:
            self.send_error(404)
            return
        filename, mime = target
        body = (ROOT / filename).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), PreviewHandler)
    print(f"Timeline prototype: http://{args.bind}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
