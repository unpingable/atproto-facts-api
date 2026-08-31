"""Route table for the public surface.

The table is data, not decorators, so the tests can enumerate the entire public
surface and assert it rather than anyone discovering it by reading a Caddyfile.
"""

from __future__ import annotations

from . import facts, sources

NAMESPACE = "/atproto"
API_PREFIX = f"{NAMESPACE}/v1"

# Every route the service serves. Anything not listed here 404s.
#   path -> (kind, handler_key)
# kind is "doc" (static asset) or "fact" (JSON envelope built from an artifact).
FACT_ROUTES = {
    f"{API_PREFIX}/instruments": "instruments",
    f"{API_PREFIX}/labelwatch/overview": "labelwatch_overview",
    f"{API_PREFIX}/weatherwatch/conditions": "weatherwatch_conditions",
    f"{API_PREFIX}/weatherwatch/metrics": "weatherwatch_metrics",
}

DOC_ROUTES = {
    f"{NAMESPACE}/": "index.html",
    f"{NAMESPACE}/openapi.json": "openapi.json",
    f"{NAMESPACE}/og-card.png": "og-card.png",
    f"{NAMESPACE}/health": None,  # handled inline
}

ALL_PATHS = sorted(set(FACT_ROUTES) | set(DOC_ROUTES))

# Paths that must never exist on this service. Asserted by tests, so a future
# addition of any of these shapes fails CI rather than production.
FORBIDDEN_PREFIXES = (
    f"{NAMESPACE}/admin",
    f"{NAMESPACE}/v1/admin",
    f"{NAMESPACE}/v1/driftwatch",
    f"{NAMESPACE}/v1/exposure",
    f"{NAMESPACE}/v1/strain",
    f"{NAMESPACE}/v1/labels",
    f"{NAMESPACE}/v1/quarantine",
    f"{NAMESPACE}/v1/recent-decisions",
)


def build_fact(key: str, now=None):
    """Return (payload, artifact) for a fact route."""
    if key == "labelwatch_overview":
        art = sources.load(sources.LABELWATCH_OVERVIEW)
        return facts.labelwatch_overview(art, now), art
    if key == "weatherwatch_conditions":
        art = sources.load(sources.WEATHERWATCH_SUMMARY)
        return facts.weatherwatch_conditions(art, now), art
    if key == "weatherwatch_metrics":
        art = sources.load(sources.WEATHERWATCH_SUMMARY)
        return facts.weatherwatch_metrics(art, now), art
    if key == "instruments":
        return build_instruments(now), None
    raise KeyError(key)


def build_instruments(now=None) -> dict:
    """Index of contributing instruments and whether each is currently readable."""
    now = now or sources.utcnow()
    entries = []
    for name, path, endpoints in (
        ("weatherwatch", sources.WEATHERWATCH_SUMMARY,
         [f"{API_PREFIX}/weatherwatch/conditions", f"{API_PREFIX}/weatherwatch/metrics"]),
        ("labelwatch", sources.LABELWATCH_OVERVIEW,
         [f"{API_PREFIX}/labelwatch/overview"]),
    ):
        entry = {"instrument": name, "endpoints": endpoints}
        try:
            art = sources.load(path)
        except sources.SourceUnavailable:
            entry["state"] = "UNAVAILABLE"
            entry["generated_at"] = None
            entry["note"] = ("The instrument's published artifact could not be "
                             "read. This is absence of evidence, not evidence "
                             "that the instrument observed nothing.")
        else:
            entry["state"] = "PRESENT"
            entry["generated_at"] = art.data.get("generated_at")
        entries.append(entry)

    entries.append({
        "instrument": "driftwatch",
        "endpoints": [],
        "state": "NOT_PUBLISHED",
        "generated_at": None,
        "note": ("Driftwatch observes claim propagation per subject. Its "
                 "per-subject state is private and is never published. Its "
                 "reduced aggregate contribution is not published in v1."),
    })

    return {
        "contract": facts.CONTRACT_INSTRUMENTS,
        "retrieved_at": sources.iso(now),
        "instruments": entries,
    }
