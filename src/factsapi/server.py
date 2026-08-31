"""A small, boring, read-only publication service.

Anonymous. GET/HEAD only. Reads materialized instrument artifacts from disk and
serves them as versioned public facts. No database, no auth, no mutation, no
per-subject queries, and no path from the Internet to any instrument's
operational store.
"""

from __future__ import annotations

import json
import os
from datetime import timezone
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import routes, sources

ASSET_DIR = os.environ.get(
    "FACTSAPI_ASSET_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"),
)
BIND_HOST = os.environ.get("FACTSAPI_HOST", "127.0.0.1")
BIND_PORT = int(os.environ.get("FACTSAPI_PORT", "8425"))

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
}
SERVER_TOKEN = "atproto-facts-api"


class Handler(BaseHTTPRequestHandler):
    server_version = SERVER_TOKEN
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # keep stdout to journald-friendly lines
        self.log_date_time_string()
        print("%s %s" % (self.address_string(), fmt % args), flush=True)

    def _send(self, status, body: bytes, ctype, *, etag=None, last_modified=None,
              extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Public, anonymous, browser-consumable.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "If-None-Match, If-Modified-Since")
        self.send_header("Access-Control-Expose-Headers", "ETag, Last-Modified")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if etag:
            self.send_header("ETag", etag)
        if last_modified is not None:
            self.send_header(
                "Last-Modified",
                formatdate(last_modified.astimezone(timezone.utc).timestamp(), usegmt=True),
            )
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, payload, **kw):
        body = json.dumps(payload, indent=2, sort_keys=False).encode("utf-8") + b"\n"
        self._send(status, body, CONTENT_TYPES[".json"], **kw)

    def _error(self, status, code, message):
        self._json(status, {"error": {"status": status, "code": code,
                                      "message": message}})

    def _not_modified(self, etag=None, last_modified=None):
        self.send_response(304)
        self.send_header("Access-Control-Allow-Origin", "*")
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- methods ----------------------------------------------------------
    def do_OPTIONS(self):
        self._send(204, b"", "text/plain; charset=utf-8")

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def _reject_method(self):
        self._send(
            405, json.dumps({"error": {"status": 405, "code": "method_not_allowed",
                                       "message": "This API is read-only. Use GET or HEAD."}}
                            ).encode("utf-8") + b"\n",
            CONTENT_TYPES[".json"], extra={"Allow": "GET, HEAD, OPTIONS"})

    do_POST = do_PUT = do_PATCH = do_DELETE = _reject_method

    # -- dispatch ---------------------------------------------------------
    def _handle(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        # /atproto and /atproto/ are the same page.
        if path == routes.NAMESPACE:
            self._send(308, b"", "text/plain; charset=utf-8",
                       extra={"Location": routes.NAMESPACE + "/"})
            return

        if path == f"{routes.NAMESPACE}/health":
            self._json(200, {"status": "ok", "service": SERVER_TOKEN})
            return

        if path in routes.DOC_ROUTES:
            self._serve_asset(routes.DOC_ROUTES[path])
            return

        if path in routes.FACT_ROUTES:
            self._serve_fact(routes.FACT_ROUTES[path])
            return

        self._error(404, "not_found",
                    "No such path. See %s/ for the published surface." % routes.NAMESPACE)

    def _serve_asset(self, filename):
        full = os.path.join(ASSET_DIR, filename)
        try:
            with open(full, "rb") as fh:
                body = fh.read()
            mtime = os.stat(full).st_mtime
        except OSError:
            self._error(503, "asset_unavailable", "Documentation asset unavailable.")
            return
        import hashlib
        from datetime import datetime
        etag = '"%s"' % hashlib.sha256(body).hexdigest()[:32]
        if self.headers.get("If-None-Match") == etag:
            self._not_modified(etag=etag)
            return
        ext = os.path.splitext(filename)[1]
        self._send(200, body, CONTENT_TYPES.get(ext, "application/octet-stream"),
                   etag=etag,
                   last_modified=datetime.fromtimestamp(mtime, tz=timezone.utc))

    def _serve_fact(self, key):
        try:
            payload, art = routes.build_fact(key)
        except sources.SourceUnavailable as exc:
            # Absence of the artifact is reported as absence, not as a fact.
            self._error(503, "source_unavailable", str(exc))
            return
        except Exception:  # pragma: no cover - defensive
            self._error(500, "internal_error", "The service failed to build this fact.")
            return

        etag = art.etag if art is not None else None
        if etag and self.headers.get("If-None-Match") == etag:
            self._not_modified(etag=etag)
            return
        self._json(200, payload, etag=etag,
                   last_modified=art.last_modified if art is not None else None,
                   extra={"Cache-Control": "public, max-age=60"})


def main():
    server = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    print(f"{SERVER_TOKEN} listening on {BIND_HOST}:{BIND_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
