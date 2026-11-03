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

import os
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


LABELWATCH_RECENT_SCHEMA = "labelwatch.recent-overview.v1"
LABELWATCH_LEGACY_SCHEMA = 23
LABELWATCH_DEFAULT_MAX_AGE_SECONDS = 7200
# A generated_at further than this in the future is not a reading of the past.
LABELWATCH_FUTURE_SKEW_SECONDS = 300
SERVABLE_FRESHNESS = ("fresh", "warming_up")
FRESHNESS_CODES = {"stale": "source_stale", "held": "source_held",
                   "unavailable": "source_unavailable"}


class FreshnessRefused(Exception):
    """The artifact exists but must not be served as a current fact.

    Carries the freshness block so the refusal can say why (stale, held, or
    unavailable), instead of a cache serving an old reading as current.
    """

    def __init__(self, freshness: dict, message: str):
        super().__init__(message)
        self.freshness = freshness
        self.code = FRESHNESS_CODES.get(freshness.get("state"), "source_unavailable")


def labelwatch_max_age_seconds() -> int:
    raw = os.environ.get("FACTSAPI_LABELWATCH_MAX_AGE_SECONDS")
    try:
        value = int(raw) if raw else LABELWATCH_DEFAULT_MAX_AGE_SECONDS
    except ValueError:
        value = LABELWATCH_DEFAULT_MAX_AGE_SECONDS
    return value if value > 0 else LABELWATCH_DEFAULT_MAX_AGE_SECONDS


def labelwatch_collection_max_age_seconds(default: int) -> int:
    raw = os.environ.get("FACTSAPI_LABELWATCH_COLLECTION_MAX_AGE_SECONDS")
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return value if value > 0 else default


def _recent_shape_ok(d) -> bool:
    if not isinstance(d.get("hold"), bool):
        return False
    if d["hold"]:
        return True
    acquisition, coverage, counts = d.get("acquisition"), d.get("coverage"), d.get("counts")
    return (isinstance(acquisition, dict) and isinstance(acquisition.get("started_at"), str)
            and isinstance(coverage, dict) and isinstance(coverage.get("gaps"), list)
            and isinstance(counts, dict) and type(d.get("window_days")) is int
            and type(coverage.get("observed_days")) is int
            and (coverage.get("warmup") is None or isinstance(coverage.get("warmup"), dict)))


def labelwatch_freshness(d: dict, now: datetime, max_age: int | None = None) -> dict:
    """Weatherwatch-shaped freshness for a Labelwatch artifact.

    fresh       age <= max_age and the window is fully inside the epoch
    warming_up  age <= max_age, but the epoch is younger than the window
    stale       age > max_age (the legacy schema-23 artifact ages into this)
    held        the artifact declares hold:true
    unavailable unknown schema, malformed, no usable generated_at, or (recent
                schema) no usable coverage.observed_through
    """
    max_age = max_age or labelwatch_max_age_seconds()
    block = {"state": "unavailable", "age_seconds": None, "basis": "artifact_generated_at",
             "max_age_seconds": max_age}
    schema = d.get("schema_version")
    legacy = type(schema) is int and schema == LABELWATCH_LEGACY_SCHEMA
    recent = schema == LABELWATCH_RECENT_SCHEMA
    if not (legacy or recent) or (recent and not _recent_shape_ok(d)):
        return block
    age = _age_seconds(d.get("generated_at"), now)
    block["age_seconds"] = age
    if recent and d["hold"]:
        block["state"] = "held"
        return block
    if age is None or age < -LABELWATCH_FUTURE_SKEW_SECONDS:
        return block
    if age > max_age:
        block["state"] = "stale"
        return block
    if recent:
        # A publisher that keeps running after collection stalls would otherwise
        # look fresh forever: the last accepted observation must also be recent.
        observed_age = _age_seconds(d["coverage"].get("observed_through"), now)
        block["collection_age_seconds"] = observed_age
        block["collection_max_age_seconds"] = labelwatch_collection_max_age_seconds(max_age)
        if observed_age is None or observed_age < -LABELWATCH_FUTURE_SKEW_SECONDS:
            # No accepted collector page (null), unparseable, or in the future:
            # there is no current reading to serve.
            block["basis"] = "collection_observed_through"
            return block
        if observed_age > block["collection_max_age_seconds"]:
            block["state"] = "stale"
            block["basis"] = "collection_observed_through"
            return block
    if recent and (d["coverage"].get("warmup") is not None
                   or d["coverage"]["observed_days"] < d["window_days"]):
        block["state"] = "warming_up"
        return block
    block["state"] = "fresh"
    return block


def _labelwatch_window_facts(d) -> dict:
    """window_days / acquisition / coverage, selected by name when present."""
    out = {}
    if "window_days" in d:
        out["window_days"] = d.get("window_days")
    if isinstance(d.get("acquisition"), dict):
        out["acquisition"] = _pick(d["acquisition"], "generation_id", "started_at", "basis")
    coverage = d.get("coverage")
    if isinstance(coverage, dict):
        cov = _pick(coverage, "status", "observed_days", "observed_through", "gap_overflow")
        warmup = coverage.get("warmup")
        cov["warmup"] = _pick(warmup, "start", "end", "reason") if isinstance(warmup, dict) else None
        cov["gaps"] = [_pick(g, "start", "end", "reason")
                       for g in (coverage.get("gaps") or [])[:128] if isinstance(g, dict)]
        cov["sources"] = {k: v for k, v in _pick(
            coverage.get("sources"), "enrolled", "seeded", "discovered", "unstarted", "probing", "established", "refused",
            "pre_epoch_skipped", "undated_skipped", "late_skipped", "cursor_order_unverified",
            "undated_only", "provider_rejects", "unavailable", "future_skipped").items() if type(v) is int and v >= 0}
        out["coverage"] = cov
    return out


def _top_labelers(d) -> list:
    return [_pick(row, "labeler_did", "count")
            for row in (d.get("top_labelers_7d") or [])[:20] if isinstance(row, dict)]


LABELWATCH_DID_CAVEAT = ("Labeler DIDs identify public moderation services, not people. No "
                         "subject identities are published.")


def labelwatch_overview(art: Artifact, now: datetime | None = None) -> dict:
    now = now or utcnow()
    d = art.data
    freshness = labelwatch_freshness(d, now)
    if freshness["state"] not in SERVABLE_FRESHNESS:
        raise FreshnessRefused(freshness, {
            "stale": ("Labelwatch collection has not advanced for more than %ss; not served as current."
                      % freshness.get("collection_max_age_seconds")
                      if freshness.get("basis") == "collection_observed_through" else
                      "Labelwatch overview is older than %ss; not served as current." % freshness["max_age_seconds"]),
            "held": "Labelwatch is held; no current reading is published.",
        }.get(freshness["state"], "Labelwatch overview is unreadable or of an unknown schema."))
    if d.get("schema_version") == LABELWATCH_RECENT_SCHEMA:
        return _labelwatch_recent(d, now, freshness)
    facts = {
        "labeler_count": d.get("labeler_count"),
        "alert_count_total": d.get("alert_count"),
        "alert_count_window": d.get("alert_count_window"),
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
        "top_labelers_7d": _top_labelers(d),
    }
    facts.update(_labelwatch_window_facts(d))
    caveats = [
        "Counts are of Labelwatch's own observations, not of everything that "
        "happened. A labeler Labelwatch could not reach contributes nothing.",
        "observation.adequacy qualifies the whole reading. When it is "
        "'partial' or 'unobserved', network_weather withholds negative claims "
        "such as 'calm' and reports 'under-observed' instead.",
        "supports_negative_claim is false when the observation window lacked "
        "the standing to assert that something did not happen.",
        LABELWATCH_DID_CAVEAT,
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
                     d.get("generated_at"), now, facts, caveats, provenance,
                     freshness=freshness)


def _labelwatch_recent(d, now, freshness) -> dict:
    """labelwatch.recent-overview.v1: a rolling-window epoch, aggregate only."""
    counts = _pick(d.get("counts"), "events_24h", "events_7d", "events_window",
                   "labelers_active_7d", "labelers_active_window")
    facts = {
        "labeler_count": counts.get("labelers_active_window"),
        # No alert pipeline runs in the rolling epoch; absent is reported as
        # absent (null), never as zero.
        "alert_count_total": None,
        "alert_count_window": None,
        **_labelwatch_window_facts(d),
        "counts": counts,
        "top_labelers_7d": _top_labelers(d),
        "current_label_state": "unknown",
    }
    caveats = [
        "Counts cover only the current epoch's window; intervals before "
        "acquisition are not observed.",
        "Counts are of Labelwatch's own observations, not of everything that "
        "happened. A labeler Labelwatch could not reach contributes nothing.",
        "Recorded gaps are disclosed with a reason. No recorded gaps does not "
        "prove complete source coverage.",
        "Labelwatch records observed application and removal events; current "
        "label state is unknown.",
        "labeler_count counts labelers with at least one observation in the "
        "current window of this epoch.",
        LABELWATCH_DID_CAVEAT,
    ]
    provenance = {
        "instrument": "labelwatch",
        "method": "Keeps a rolling window of label application and removal "
                  "events observed from known labeler endpoints, stamped with "
                  "the local acceptance clock; older observations expire.",
        "artifact_schema_version": d.get("schema_version"),
    }
    return _envelope(CONTRACT_LABELWATCH_OVERVIEW, "labelwatch",
                     d.get("generated_at"), now, facts, caveats, provenance,
                     freshness=freshness)


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
