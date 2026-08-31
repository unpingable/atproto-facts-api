"""The reachable HTTP surface, asserted rather than discovered by hand.

Starts the real server on an ephemeral port and enumerates what answers.
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from factsapi import routes, server

EXPECTED_PUBLIC_PATHS = {
    "/atproto/",
    "/atproto/health",
    "/atproto/openapi.json",
    "/atproto/og-card.png",
    "/atproto/v1/instruments",
    "/atproto/v1/labelwatch/overview",
    "/atproto/v1/weatherwatch/conditions",
    "/atproto/v1/weatherwatch/metrics",
}

MUST_NOT_EXIST = [
    "/atproto/admin", "/atproto/v1/admin", "/atproto/v1/admin/mappings",
    "/atproto/v1/driftwatch", "/atproto/v1/driftwatch/exposure",
    "/atproto/v1/exposure/did:plc:aaaa", "/atproto/v1/strain/top",
    "/atproto/v1/labels/whatever", "/atproto/v1/quarantine/recent",
    "/atproto/v1/recent-decisions", "/atproto/v1/query", "/atproto/v1/sql",
    "/atproto/../etc/passwd", "/", "/v1/instruments", "/openapi.json",
]


@pytest.fixture(scope="module")
def base():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def fetch(base, path, method="GET"):
    req = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


@pytest.mark.parametrize("path", sorted(EXPECTED_PUBLIC_PATHS))
def test_expected_paths_answer(base, path):
    status, body, _ = fetch(base, path)
    assert status == 200, f"{path} returned {status}"
    assert body


@pytest.mark.parametrize("path", MUST_NOT_EXIST)
def test_paths_that_must_not_exist(base, path):
    status, _, _ = fetch(base, path)
    assert status == 404, f"{path} returned {status}, expected 404"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutating_methods_are_refused(base, method):
    status, _, headers = fetch(base, "/atproto/v1/instruments", method=method)
    assert status == 405
    assert "GET" in headers.get("Allow", "")


def test_head_returns_no_body_but_same_status(base):
    status, body, _ = fetch(base, "/atproto/v1/instruments", method="HEAD")
    assert status == 200
    assert body == b""


def test_cors_is_open_for_public_get(base):
    _, _, headers = fetch(base, "/atproto/v1/instruments")
    assert headers.get("Access-Control-Allow-Origin") == "*"


def test_etag_yields_304(base):
    _, _, headers = fetch(base, "/atproto/v1/labelwatch/overview")
    etag = headers.get("ETag")
    assert etag
    req = urllib.request.Request(base + "/atproto/v1/labelwatch/overview")
    req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    assert status == 304


def test_namespace_root_redirects(base):
    req = urllib.request.Request(base + "/atproto")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(req, timeout=10)
        status = 200
    except urllib.error.HTTPError as exc:
        status, location = exc.code, exc.headers.get("Location")
        assert location == "/atproto/"
    assert status in (200, 308)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def test_docs_page_carries_social_preview(base):
    _, body, headers = fetch(base, "/atproto/")
    html = body.decode("utf-8")
    assert headers["Content-Type"].startswith("text/html")
    assert html.lstrip().lower().startswith("<!doctype html>")
    for tag in ('property="og:title"', 'property="og:image"', 'property="og:url"',
                'name="twitter:card"'):
        assert tag in html, f"docs page missing {tag}"
    assert "<head>" in html and "</head>" in html


def test_og_card_is_a_real_png(base):
    status, body, headers = fetch(base, "/atproto/og-card.png")
    assert status == 200
    assert headers["Content-Type"] == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_openapi_is_valid_json_and_matches_namespace(base):
    _, body, _ = fetch(base, "/atproto/openapi.json")
    spec = json.loads(body)
    assert spec["openapi"].startswith("3.1")
    assert spec["servers"][0]["url"].endswith("/atproto")


def test_route_table_matches_what_is_actually_served(base):
    """The declared surface and the real surface must not drift apart."""
    declared = set(routes.ALL_PATHS)
    for path in declared:
        status, _, _ = fetch(base, path)
        assert status == 200, f"declared route {path} returned {status}"
    assert declared == EXPECTED_PUBLIC_PATHS
