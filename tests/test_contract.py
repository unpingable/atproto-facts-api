"""The published contract is stable and self-consistent."""
import json
import pathlib

import pytest

from factsapi import facts, routes

ASSETS = pathlib.Path(__file__).resolve().parents[1] / "src" / "factsapi" / "assets"

ENVELOPE_KEYS = {"contract", "instrument", "generated_at", "retrieved_at",
                 "age_seconds", "facts", "caveats", "provenance"}


@pytest.mark.parametrize("key", ["labelwatch_overview", "weatherwatch_conditions",
                                 "weatherwatch_metrics"])
def test_envelope_shape(key):
    payload, _ = routes.build_fact(key)
    assert ENVELOPE_KEYS <= set(payload), f"{key} missing envelope keys"
    assert payload["contract"].endswith("/v1")
    assert isinstance(payload["caveats"], list) and payload["caveats"]
    assert isinstance(payload["provenance"], dict) and payload["provenance"]


def test_contract_ids_are_distinct_and_versioned():
    ids = {facts.CONTRACT_LABELWATCH_OVERVIEW, facts.CONTRACT_WEATHERWATCH_CONDITIONS,
           facts.CONTRACT_WEATHERWATCH_METRICS, facts.CONTRACT_INSTRUMENTS}
    assert len(ids) == 4
    assert all(i.startswith("atproto.facts.") and i.endswith("/v1") for i in ids)


def test_openapi_paths_match_the_route_table():
    spec = json.loads((ASSETS / "openapi.json").read_text())
    documented = {routes.NAMESPACE + p for p in spec["paths"]}
    served = set(routes.FACT_ROUTES) | {f"{routes.NAMESPACE}/health"}
    assert documented == served, (
        "OpenAPI and the route table disagree; "
        f"only in spec={documented - served} only in code={served - documented}"
    )


def test_openapi_declares_only_get():
    spec = json.loads((ASSETS / "openapi.json").read_text())
    for path, item in spec["paths"].items():
        assert set(item) == {"get"}, f"{path} declares a non-GET method"


def test_adequacy_gate_is_carried_through():
    """A negative claim must not survive an inadequate observation window."""
    payload, _ = routes.build_fact("labelwatch_overview")
    weather = payload["facts"]["network_weather"]
    observation = payload["facts"]["observation"]
    if observation.get("adequacy") in ("partial", "unobserved"):
        assert weather.get("supports_negative_claim") is False
        assert "calm" not in weather.get("signals", [])


def test_window_semantics_present_for_weatherwatch():
    payload, _ = routes.build_fact("weatherwatch_conditions")
    window = payload["facts"]["window"]
    for field in ("start", "end", "span_seconds", "observed_seconds", "coverage_ratio"):
        assert field in window, f"window missing {field}"
    assert 0 < window["coverage_ratio"] <= 1


def test_instruments_index_declares_driftwatch_unpublished():
    payload, _ = routes.build_fact("instruments")
    entry = {e["instrument"]: e for e in payload["instruments"]}["driftwatch"]
    assert entry["state"] == "NOT_PUBLISHED"
    assert entry["endpoints"] == []
