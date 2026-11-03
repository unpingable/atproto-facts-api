"""Labelwatch overview freshness gate: only fresh or warming-up readings are served."""
import json
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

from conftest import FIXTURES, fresh_copy
from factsapi import facts, routes, server, sources

PATH = "/atproto/v1/labelwatch/overview"
DID_ANY = re.compile(r"did:[a-z0-9]+:[A-Za-z0-9._:%-]+")


@pytest.fixture(scope="module")
def base():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def fetch(base, headers=None):
    req = urllib.request.Request(base + PATH, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, json.loads(body) if body else None, dict(exc.headers)


def stamp(delta=timedelta(0)):
    return (datetime.now(timezone.utc) - delta).isoformat().replace("+00:00", "Z")


def recent(tmp_path, monkeypatch, **changes):
    if "coverage" not in changes:
        # Re-stamp the last accepted page with the artifact, as a live publisher would.
        data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
        changes["coverage"] = dict(data["coverage"], observed_through=stamp())
    target = fresh_copy(FIXTURES / "lw-recent-overview.json", tmp_path, **changes)
    monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW", str(target))
    return json.loads(target.read_text())


def steady(data):
    cov = dict(data["coverage"], warmup=None, observed_days=30, status="gapped", observed_through=stamp())
    return {"coverage": cov}


def assert_refused(status, body, headers, code, state):
    assert status == 503, status
    assert body["error"]["code"] == code
    assert body["error"]["freshness"]["state"] == state
    assert headers["Cache-Control"] == "no-store"


def test_fresh_recent_overview_is_served(base, tmp_path, monkeypatch):
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    recent(tmp_path, monkeypatch, **steady(data))
    status, body, headers = fetch(base)
    assert status == 200 and headers["Cache-Control"] == "public, max-age=60"
    fresh = body["freshness"]
    assert fresh["state"] == "fresh" and fresh["basis"] == "artifact_generated_at"
    assert fresh["max_age_seconds"] == 7200 and 0 <= fresh["age_seconds"] < 60
    f = body["facts"]
    assert f["window_days"] == 30 and f["current_label_state"] == "unknown"
    assert f["acquisition"] == data["acquisition"]
    assert f["coverage"]["warmup"] is None and f["coverage"]["gaps"][0]["reason"] == "source_unavailable"
    assert f["coverage"]["sources"] == {"enrolled": 580, "seeded": 24, "discovered": 556, "unstarted": 120, "probing": 31, "established": 212,
                                        "refused": 217, "pre_epoch_skipped": 1834201, "undated_skipped": 12,
                                        "late_skipped": 4410, "cursor_order_unverified": 3, "undated_only": 2,
                                        "provider_rejects": 5, "unavailable": 7, "future_skipped": 9}
    assert "reason" not in json.dumps(f["coverage"]["sources"])
    assert f["counts"]["events_7d"] == 41234 and f["labeler_count"] == 97
    assert f["alert_count_total"] is None and f["alert_count_window"] is None
    assert "Counts cover only the current epoch's window; intervals before acquisition are not observed." in body["caveats"]
    assert "build_signature" not in json.dumps(body) and "47a5651" not in json.dumps(body)


def test_warming_up_is_served_with_warmup(base, tmp_path, monkeypatch):
    recent(tmp_path, monkeypatch)
    status, body, _ = fetch(base)
    assert status == 200 and body["freshness"]["state"] == "warming_up"
    assert body["facts"]["coverage"]["warmup"]["reason"] == "not_observed"
    assert body["facts"]["coverage"]["observed_days"] == 0
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    # observed_days below the window alone also means warming up.
    recent(tmp_path, monkeypatch, coverage=dict(data["coverage"], warmup=None, observed_days=29,
                                                       observed_through=stamp()))
    assert fetch(base)[1]["freshness"]["state"] == "warming_up"


def test_stale_recent_overview_is_refused(base, tmp_path, monkeypatch):
    recent(tmp_path, monkeypatch, generated_at=stamp(timedelta(hours=2, minutes=1)))
    assert_refused(*fetch(base), "source_stale", "stale")


def test_max_age_is_configurable(base, tmp_path, monkeypatch):
    recent(tmp_path, monkeypatch, generated_at=stamp(timedelta(minutes=2)))
    assert fetch(base)[0] == 200
    monkeypatch.setenv("FACTSAPI_LABELWATCH_MAX_AGE_SECONDS", "60")
    status, body, headers = fetch(base)
    assert_refused(status, body, headers, "source_stale", "stale")
    assert body["error"]["freshness"]["max_age_seconds"] == 60


def test_held_overview_is_refused_even_when_new(base, tmp_path, monkeypatch):
    recent(tmp_path, monkeypatch, hold=True, counts={}, top_labelers_7d=[], acquisition=None)
    assert_refused(*fetch(base), "source_held", "held")


@pytest.mark.parametrize("change", [
    {"schema_version": "labelwatch.recent-overview.v2"},
    {"schema_version": "23"},
    {"hold": "false"},
    {"coverage": "warming"},
    {"generated_at": "not a time"},
    {"generated_at": None},
])
def test_malformed_overview_is_unavailable(base, tmp_path, monkeypatch, change):
    recent(tmp_path, monkeypatch, **change)
    assert_refused(*fetch(base), "source_unavailable", "unavailable")


def test_future_generated_at_is_unavailable(base, tmp_path, monkeypatch):
    recent(tmp_path, monkeypatch, generated_at=stamp(-timedelta(hours=1)))
    assert_refused(*fetch(base), "source_unavailable", "unavailable")


def test_absent_or_unreadable_overview_is_unavailable_and_uncached(base, tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW", str(tmp_path / "missing.json"))
    status, body, headers = fetch(base)
    assert status == 503 and body["error"]["code"] == "source_unavailable"
    assert headers["Cache-Control"] == "no-store"
    garbage = tmp_path / "garbage.json"; garbage.write_text("{not json")
    monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW", str(garbage))
    status, body, headers = fetch(base)
    assert status == 503 and body["error"]["code"] == "source_unavailable" and headers["Cache-Control"] == "no-store"


def test_legacy_september_11_artifact_is_stale_not_a_cached_200(base, monkeypatch):
    legacy = FIXTURES / "lw-overview-legacy-20260911.json"
    monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW", str(legacy))
    status, body, headers = fetch(base)
    assert_refused(status, body, headers, "source_stale", "stale")
    assert body["error"]["freshness"]["age_seconds"] > 7200
    # A cached validator cannot turn the refusal into 304.
    etag = sources.load(str(legacy)).etag
    status, _, headers = fetch(base, {"If-None-Match": etag})
    assert status == 503 and headers["Cache-Control"] == "no-store"
    entry = {e["instrument"]: e for e in routes.build_fact("instruments")[0]["instruments"]}["labelwatch"]
    assert entry["state"] == "PRESENT" and entry["freshness_state"] == "stale"


def test_fresh_legacy_artifact_keeps_its_mapping_and_gains_freshness(base):
    status, body, _ = fetch(base)
    assert status == 200 and body["freshness"]["state"] == "fresh"
    assert body["facts"]["alert_count_total"] == 117159 and body["facts"]["alert_count_window"] is None
    assert body["facts"]["labeler_count"] == 557 and "census" in body["facts"]
    assert "window_days" not in body["facts"] and "coverage" not in body["facts"]


def test_recent_overview_publishes_only_labeler_dids(tmp_path, monkeypatch):
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    poison = dict(data["coverage"], observed_through=stamp(), subject_dids=["did:plc:victimaaaaaaaaaaaaaaaaaaaa"],
                  sources=dict(data["coverage"]["sources"], endpoint="https://internal.example"))
    recent(tmp_path, monkeypatch, coverage=poison, operator_secret="SHOULD-NEVER-APPEAR",
           top_labelers_7d=data["top_labelers_7d"] + [{"labeler_did": "did:plc:labelerz", "count": 1,
                                                        "target_did": "did:plc:victimbbbbbbbbbbbbbbbbbbb"}])
    payload, _ = routes.build_fact("labelwatch_overview")
    body = json.dumps(payload)
    assert set(DID_ANY.findall(body)) == {r["labeler_did"] for r in payload["facts"]["top_labelers_7d"]}
    for token in ("victim", "SHOULD-NEVER-APPEAR", "internal.example", "subject_dids", "target_did"):
        assert token not in body


def test_freshness_states_table():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    state = lambda d: facts.labelwatch_freshness(d, now)["state"]  # noqa: E731
    assert state(data) == "warming_up"
    assert state(dict(data, coverage=dict(data["coverage"], warmup=None, observed_days=30))) == "fresh"
    assert state(dict(data, generated_at="2026-10-09T09:59:59Z")) == "stale"
    assert state(dict(data, generated_at="2026-10-01T00:00:00Z", hold=True)) == "held"
    assert state({"schema_version": 7, "generated_at": "2026-10-09T12:00:00Z"}) == "unavailable"
    legacy = json.loads((FIXTURES / "lw-overview-legacy-20260911.json").read_text())
    assert state(legacy) == "stale"


def test_openapi_documents_freshness_and_refusals():
    import pathlib
    spec = json.loads((pathlib.Path(server.ASSET_DIR) / "openapi.json").read_text())
    get = spec["paths"]["/v1/labelwatch/overview"]["get"]
    schemas = spec["components"]["schemas"]
    assert schemas["LabelwatchFreshness"]["properties"]["state"]["enum"] == [
        "fresh", "warming_up", "stale", "held", "unavailable"]
    assert schemas["FreshnessError"]["properties"]["error"]["properties"]["code"]["enum"] == [
        "source_stale", "source_held", "source_unavailable"]
    assert get["responses"]["503"]["headers"]["Cache-Control"]["description"] == "no-store"
    fields = get["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["facts"]["properties"]
    assert {"window_days", "acquisition", "coverage", "alert_count_window", "counts"} <= set(fields)
    assert fields["alert_count_total"]["description"].startswith(
        "Legacy artifacts: all alerts ever recorded; recent-epoch artifacts: null")


def test_stalled_collection_is_refused_even_when_artifact_is_new(base, tmp_path, monkeypatch):
    """A publisher that keeps running after collection stalls must not look fresh."""
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    stalled = dict(steady(data)["coverage"], observed_through=stamp(timedelta(hours=3)))
    recent(tmp_path, monkeypatch, **dict(steady(data), coverage=stalled))
    status, body, headers = fetch(base)
    assert_refused(status, body, headers, "source_stale", "stale")
    fresh = body["error"]["freshness"]
    assert fresh["basis"] == "collection_observed_through"
    assert fresh["collection_age_seconds"] > 7200 and fresh["collection_max_age_seconds"] == 7200
    assert "collection has not advanced" in body["error"]["message"]
    monkeypatch.setenv("FACTSAPI_LABELWATCH_COLLECTION_MAX_AGE_SECONDS", "86400")
    status, body, _ = fetch(base)
    assert status == 200 and body["freshness"]["collection_max_age_seconds"] == 86400


@pytest.mark.parametrize("through", [None, "not a time", "FUTURE"])
def test_recent_without_usable_observed_through_is_unavailable(base, tmp_path, monkeypatch, through):
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    if through == "FUTURE":
        through = stamp(-timedelta(hours=1))
    recent(tmp_path, monkeypatch, coverage=dict(data["coverage"], observed_through=through))
    assert_refused(*fetch(base), "source_unavailable", "unavailable")


def test_not_modified_carries_cache_control(base, tmp_path, monkeypatch):
    recent(tmp_path, monkeypatch)
    status, _, headers = fetch(base)
    assert status == 200
    status, _, headers = fetch(base, {"If-None-Match": headers["ETag"]})
    assert status == 304 and headers["Cache-Control"] == "public, max-age=60"


def test_non_integer_source_counts_are_dropped(tmp_path, monkeypatch):
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    sources_ = dict(data["coverage"]["sources"], cursor_order_unverified=[{"did": "did:plc:victimccccccccccccccccc"}],
                    late_skipped="many", provider_rejects=-1)
    recent(tmp_path, monkeypatch, coverage=dict(data["coverage"], sources=sources_, observed_through=stamp()))
    payload, _ = routes.build_fact("labelwatch_overview")
    got = payload["facts"]["coverage"]["sources"]
    assert "cursor_order_unverified" not in got and "late_skipped" not in got and "provider_rejects" not in got
    assert got["undated_only"] == 2 and "victim" not in json.dumps(payload)


def _conforms(value, schema, spec, path="$"):
    """Minimal OpenAPI 3.1 subset check: $ref, type, enum, properties (no extras)."""
    if "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    types = schema.get("type")
    if types is not None:
        types = types if isinstance(types, list) else [types]
        kinds = {"string": str, "integer": int, "number": (int, float), "object": dict,
                 "array": list, "boolean": bool, "null": type(None)}
        assert any(isinstance(value, kinds[t]) and not (t in ("integer", "number") and isinstance(value, bool))
                   for t in types), f"{path}: {value!r} is not {types}"
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: {value!r} not in {schema['enum']}"
    if isinstance(value, dict) and "properties" in schema:
        extra = set(value) - set(schema["properties"])
        assert not extra, f"{path}: undocumented fields {extra}"
        for key, item in value.items():
            _conforms(item, schema["properties"][key], spec, f"{path}.{key}")


@pytest.mark.parametrize("case", ["stale", "collection_stale", "held", "unavailable", "absent"])
def test_503_bodies_match_the_openapi_contract(base, tmp_path, monkeypatch, case):
    import pathlib
    spec = json.loads((pathlib.Path(server.ASSET_DIR) / "openapi.json").read_text())
    schema = spec["paths"]["/v1/labelwatch/overview"]["get"]["responses"]["503"]["content"]["application/json"]["schema"]
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    if case == "stale":
        recent(tmp_path, monkeypatch, generated_at=stamp(timedelta(hours=3)))
    elif case == "collection_stale":
        recent(tmp_path, monkeypatch, coverage=dict(data["coverage"], observed_through=stamp(timedelta(hours=3))))
    elif case == "held":
        recent(tmp_path, monkeypatch, hold=True)
    elif case == "unavailable":
        recent(tmp_path, monkeypatch, coverage=dict(data["coverage"], observed_through=None))
    else:
        monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW", str(tmp_path / "missing.json"))
    status, body, headers = fetch(base)
    assert status == 503 and headers["Cache-Control"] == "no-store"
    _conforms(body, schema, spec)
    if case == "collection_stale":
        assert body["error"]["freshness"]["basis"] == "collection_observed_through"


def test_etag_changes_with_the_age_bucket(base, tmp_path, monkeypatch):
    recent(tmp_path, monkeypatch)
    first = fetch(base)[2]["ETag"]
    later = datetime.now(timezone.utc) + timedelta(seconds=61)
    monkeypatch.setattr(facts, "utcnow", lambda: later)
    status, body, headers = fetch(base, {"If-None-Match": first})
    assert status == 200 and headers["ETag"] != first and body["age_seconds"] >= 60


def test_unknown_coverage_status_is_passed_through(base, tmp_path, monkeypatch):
    recent(tmp_path, monkeypatch)
    status, body, _ = fetch(base)
    assert status == 200 and body["facts"]["coverage"]["status"] == "unknown"


def test_lost_predecessor_gap_reason_matches_openapi(tmp_path, monkeypatch):
    """The existing adapter preserves a predecessor loss without joining epochs."""
    from pathlib import Path
    data = json.loads((FIXTURES / "lw-recent-overview.json").read_text())
    gap = dict(data["coverage"]["gaps"][0], reason="observed_then_lost")
    recent(tmp_path, monkeypatch, coverage=dict(data["coverage"],
                                               gaps=[gap], observed_through=stamp()))
    payload, _ = routes.build_fact("labelwatch_overview")
    assert payload["contract"] == "atproto.facts.labelwatch.overview/v1"
    assert payload["facts"]["coverage"]["gaps"] == [gap]
    assert payload["facts"]["acquisition"] == data["acquisition"]
    spec = json.loads((Path(server.ASSET_DIR) / "openapi.json").read_text())
    schema = spec["paths"]["/v1/labelwatch/overview"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    reason = schema["properties"]["facts"]["properties"]["coverage"]["properties"]["gaps"]["items"]["properties"]["reason"]
    _conforms(payload["facts"]["coverage"]["gaps"][0]["reason"], reason, spec)
