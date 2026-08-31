"""Assertion of the LIVE externally reachable HTTP surface.

Opt-in, because it makes real requests to production:

    FACTSAPI_EXTERNAL=1 python3 -m pytest tests/test_external_surface.py -q

This exists because the 2026-08-31 Driftwatch exposure - seven routes open to
the internet for roughly six months, three of them running unbounded per-subject
SQL - was found by a human reading a Caddyfile. What is publicly reachable
should be an assertion that fails, not an archaeology exercise.
"""
import os
import socket
import ssl
import urllib.error
import urllib.request

import pytest

EXTERNAL = os.environ.get("FACTSAPI_EXTERNAL") == "1"
pytestmark = pytest.mark.skipif(
    not EXTERNAL, reason="set FACTSAPI_EXTERNAL=1 to probe production")

API = os.environ.get("FACTSAPI_BASE", "https://api.neutral.zone")
DRIFT = os.environ.get("FACTSAPI_DRIFTWATCH", "https://driftwatch.sp00ky.net")
TIMEOUT = 25


def probe(url):
    """Return an HTTP status, or a string describing why there wasn't one."""
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": "factsapi-surface-test"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, ssl.SSLError, socket.timeout, OSError) as exc:
        return f"unreachable: {exc}"


# --- the API's own surface -------------------------------------------------
MUST_BE_PUBLIC = [
    "/atproto/", "/atproto/openapi.json", "/atproto/og-card.png", "/atproto/health",
    "/atproto/v1/instruments", "/atproto/v1/labelwatch/overview",
    "/atproto/v1/weatherwatch/conditions", "/atproto/v1/weatherwatch/metrics",
]

MUST_NOT_BE_PUBLIC = [
    "/atproto/admin", "/atproto/v1/admin", "/atproto/v1/admin/mappings",
    "/atproto/v1/driftwatch", "/atproto/v1/exposure/did:plc:aaaaaaaaaaaaaaaaaaaaaaaa",
    "/atproto/v1/strain/top", "/atproto/v1/labels/x", "/atproto/v1/quarantine/recent",
    "/atproto/v1/recent-decisions", "/atproto/v1/query",
]


@pytest.mark.parametrize("path", MUST_BE_PUBLIC)
def test_public_paths_are_reachable(path):
    assert probe(API + path) == 200, f"{path} is not serving"


@pytest.mark.parametrize("path", MUST_NOT_BE_PUBLIC)
def test_absent_paths_are_absent(path):
    status = probe(API + path)
    assert status in (403, 404), f"{path} returned {status}; it must not be served"


# --- Driftwatch operational surface must stay unreachable ------------------
DRIFTWATCH_MUST_BE_UNREACHABLE = [
    "/admin/mappings", "/admin/cooldowns", "/recent-decisions",
    "/quarantine/recent", "/exposure/did:plc:aaaaaaaaaaaaaaaaaaaaaaaa",
    "/strain/top", "/labels/x", "/metrics", "/health/extended",
]


@pytest.mark.parametrize("path", DRIFTWATCH_MUST_BE_UNREACHABLE)
def test_driftwatch_operational_routes_stay_unreachable(path):
    """Refused at the edge. Not 200, and not a hang that means SQL ran."""
    status = probe(DRIFT + path)
    assert status in (403, 404), (
        f"driftwatch{path} returned {status}; operational routes must not be "
        "publicly reachable"
    )


def test_driftwatch_health_is_the_only_public_route():
    assert probe(DRIFT + "/health") == 200
