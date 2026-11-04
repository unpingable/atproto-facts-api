"""Coverage uncertainty must survive the explicit publication boundary."""
import json
from pathlib import Path

import pytest

from factsapi import routes, sources


GOLDEN = Path(__file__).parent / "fixtures" / "ww-coverage-bounded-summary.json"


@pytest.mark.parametrize("key", ["weatherwatch_conditions", "weatherwatch_metrics"])
def test_bounded_coverage_survives_both_public_transforms(monkeypatch, key):
    monkeypatch.setattr(sources, "WEATHERWATCH_SUMMARY", str(GOLDEN))
    payload, _ = routes.build_fact(key)
    window = payload["facts"]["window"]
    assert window["observed_seconds"] is None
    assert window["coverage_ratio"] is None
    assert window["span_seconds"] == 300
    assert window["observed_seconds_lower"] == 220
    assert window["observed_seconds_upper"] == 240
    assert window["coverage_ratio_lower"] == 0.7333333333333333
    assert window["coverage_ratio_upper"] == 0.8
    assert window["coverage_status"] == "bounded"
    assert window["recorded_observed_seconds"] == 320
    assert window["multi_piece_buckets"] == 2
    assert window["uncertain_buckets"] == 1
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize("key", ["weatherwatch_conditions", "weatherwatch_metrics"])
def test_coverage_fields_remain_an_allowlist(tmp_path, monkeypatch, key):
    document = json.loads(GOLDEN.read_text())
    document["interval"]["internal_rows"] = ["SHOULD-NEVER-APPEAR"]
    document["interval"]["operator_secret"] = "SHOULD-NEVER-APPEAR"
    artifact = tmp_path / "public-summary.json"
    artifact.write_text(json.dumps(document))
    monkeypatch.setattr(sources, "WEATHERWATCH_SUMMARY", str(artifact))
    payload, _ = routes.build_fact(key)
    assert "SHOULD-NEVER-APPEAR" not in json.dumps(payload)
    assert payload["facts"]["window"]["coverage_status"] == "bounded"
