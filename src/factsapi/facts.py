"""Artifact -> public fact transforms.

Every transform selects fields by name. Nothing is passed through wholesale,
so a new field appearing in an instrument artifact cannot reach the public
surface without someone adding it here on purpose. Publication is an additive,
deliberate act; that property is enforced here and asserted by the tests.

What is deliberately NOT published:
  - anything from Driftwatch's operational database. Driftwatch observes
    claim propagation per subject; its per-DID state is private. Its reduced
    aggregate contribution is not published in v1 (see docs).
  - subject identities of any kind: no subject DIDs, handles, at:// URIs,
    claim fingerprints, or cohort-resolvable groupings.
  - internal storage, debugging, deployment or build state.

Labeler DIDs ARE published. A labeler is a public moderation service with a
discoverable endpoint, not a person, and Labelwatch already publishes the same
identifiers. That is a deliberate inclusion, not an oversight.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .sources import Artifact, iso, utcnow

CONTRACT_LABELWATCH_OVERVIEW = "atproto.facts.labelwatch.overview/v1"
CONTRACT_WEATHERWATCH_CONDITIONS = "atproto.facts.weatherwatch.conditions/v1"
CONTRACT_WEATHERWATCH_METRICS = "atproto.facts.weatherwatch.metrics/v1"
CONTRACT_INSTRUMENTS = "atproto.facts.instruments/v1"


def _pick(src, *keys):
    """Select named keys only. Absent keys are omitted, never defaulted."""
    if not isinstance(src, dict):
        return {}
    return {k: src[k] for k in keys if k in src}


def _age_seconds(generated_at: str | None, now: datetime) -> float | None:
    if not generated_at:
        return None
    text = generated_at.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((now - dt).total_seconds(), 3)


def _envelope(contract, instrument, generated_at, now, facts, caveats, provenance,
              freshness=None):
    env = {
        "contract": contract,
        "instrument": instrument,
        "generated_at": generated_at,
        "retrieved_at": iso(now),
        "age_seconds": _age_seconds(generated_at, now),
    }
    if freshness is not None:
        env["freshness"] = freshness
    env["facts"] = facts
    env["caveats"] = caveats
    env["provenance"] = provenance
    return env


def labelwatch_overview(art: Artifact, now: datetime | None = None) -> dict:
    now = now or utcnow()
    d = art.data
    facts = {
        "labeler_count": d.get("labeler_count"),
        "alert_count_total": d.get("alert_count"),
        "alerts_by_rule_24h": _pick(
            d.get("alerts_by_rule_24h"),
            "churn_index", "data_gap", "flip_flop",
            "label_rate_spike", "target_concentration",
        ),
        "alerts_by_rule_7d": _pick(
            d.get("alerts_by_rule_7d"),
            "churn_index", "data_gap", "flip_flop",
            "label_rate_spike", "target_concentration",
        ),
        "census": _pick(
            d.get("census"),
            "auditability", "classification_confidence",
            "reachability_state", "visibility_class",
        ),
        "network_weather": _pick(
            d.get("network_weather"),
            "signals", "attribution", "supports_negative_claim",
        ),
        "observation": _pick(
            d.get("observation"),
            "adequacy", "reason", "window_minutes", "coverage_threshold",
            "labelers_attempted", "labelers_adequate", "labelers_degraded",
            "labelers_accessible", "labelers_unattempted",
            "attempts", "successes", "last_attempt_ts", "last_success_ts",
        ),
        "heartbeats": _pick(
            d.get("heartbeats"),
            "last_discovery_ok_ts", "last_ingest_ok_ts",
            "last_report_ok_ts", "last_scan_ok_ts",
        ),
        "top_labelers_7d": [
            _pick(row, "labeler_did", "count")
            for row in (d.get("top_labelers_7d") or [])
            if isinstance(row, dict)
        ],
    }
    caveats = [
        "Counts are of Labelwatch's own observations, not of everything that "
        "happened. A labeler Labelwatch could not reach contributes nothing.",
        "observation.adequacy qualifies the whole reading. When it is "
        "'partial' or 'unobserved', network_weather withholds negative claims "
        "such as 'calm' and reports 'under-observed' instead.",
        "supports_negative_claim is false when the observation window lacked "
        "the standing to assert that something did not happen.",
        "Labeler DIDs identify public moderation services, not people. No "
        "subject identities are published.",
    ]
    provenance = {
        "instrument": "labelwatch",
        "method": "Polls known labeler endpoints, records per-labeler ingest "
                  "outcomes, and derives alerts and an estate-level weather "
                  "verdict from the observations it actually obtained.",
        "artifact_schema_version": d.get("schema_version"),
        "timestamps_assumed_utc": d.get("timestamps_assumed_utc"),
        "max_skew_seconds": d.get("max_skew_seconds"),
    }
    return _envelope(CONTRACT_LABELWATCH_OVERVIEW, "labelwatch",
                     d.get("generated_at"), now, facts, caveats, provenance)


def _weatherwatch_window(d) -> dict:
    interval = d.get("interval") or {}
    window = _pick(interval, "first_bucket_start", "last_bucket_end",
                   "span_seconds", "observed_seconds", "coverage_ratio")
    for src, dst in (("first_bucket_start", "start"), ("last_bucket_end", "end")):
        val = interval.get(src)
        if isinstance(val, (int, float)):
            window[dst] = iso(datetime.fromtimestamp(val, tz=timezone.utc))
    return window


def _weatherwatch_caveats(d) -> list:
    caveats = []
    conditions = d.get("conditions") or {}
    for item in (conditions.get("cannot_see") or []):
        if isinstance(item, str):
            caveats.append(item)
    for item in (d.get("notes") or []):
        if isinstance(item, str):
            caveats.append(item)
    return caveats


def _weatherwatch_provenance(d) -> dict:
    return {
        "instrument": "weatherwatch",
        "artifact_schema": d.get("schema"),
        "collector_version": d.get("collector_version"),
        "source_endpoint": d.get("source_endpoint"),
        "claim": d.get("claim"),
        "measures": d.get("measures"),
        "does_not_measure": list(d.get("does_not_measure") or []),
    }


def weatherwatch_conditions(art: Artifact, now: datetime | None = None) -> dict:
    now = now or utcnow()
    d = art.data
    facts = {
        "conditions": _pick(
            d.get("conditions"),
            "headline", "label", "plain", "sentence", "state",
            "confidence", "confidence_plain", "as_of",
        ),
        "window": _weatherwatch_window(d),
        "total_events": _pick(d.get("total_events"), "total", "mean_rate_per_s"),
    }
    freshness = _pick(
        d.get("freshness"),
        "state", "age_seconds", "basis",
        "newest_observation_end", "newest_complete_observation_end",
        "current_threshold_seconds",
    )
    return _envelope(CONTRACT_WEATHERWATCH_CONDITIONS, "weatherwatch",
                     d.get("generated_at"), now, facts,
                     _weatherwatch_caveats(d), _weatherwatch_provenance(d),
                     freshness=freshness)


def weatherwatch_metrics(art: Artifact, now: datetime | None = None) -> dict:
    now = now or utcnow()
    d = art.data
    metrics = {}
    for name, row in sorted((d.get("metrics") or {}).items()):
        if not isinstance(name, str) or not isinstance(row, dict):
            continue
        metrics[name] = _pick(row, "total", "mean_rate_per_s", "observed_seconds",
                              "observed_windows", "unobserved_windows")
    facts = {
        "window": _weatherwatch_window(d),
        "collections": metrics,
        "total_events": _pick(d.get("total_events"), "total", "mean_rate_per_s"),
    }
    freshness = _pick(d.get("freshness"), "state", "age_seconds", "basis")
    return _envelope(CONTRACT_WEATHERWATCH_METRICS, "weatherwatch",
                     d.get("generated_at"), now, facts,
                     _weatherwatch_caveats(d), _weatherwatch_provenance(d),
                     freshness=freshness)
