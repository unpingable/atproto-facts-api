import json
import os
import pathlib
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests" / "fixtures"


def fresh_copy(source, directory, **changes):
    """A fixture artifact re-stamped as generated now (plus overrides).

    The Labelwatch overview route refuses stale artifacts, so tests that want
    a servable reading must say so explicitly instead of relying on the date
    a fixture was captured.
    """
    data = json.loads(pathlib.Path(source).read_text())
    data["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    data.update(changes)
    target = pathlib.Path(directory) / pathlib.Path(source).name
    target.write_text(json.dumps(data))
    return target


@pytest.fixture(autouse=True)
def _point_at_fixtures(monkeypatch, tmp_path):
    """Every test reads fixtures, never a live host."""
    from factsapi import sources
    monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW",
                        str(fresh_copy(FIXTURES / "lw-overview.json", tmp_path)))
    monkeypatch.setattr(sources, "WEATHERWATCH_SUMMARY", str(FIXTURES / "ww-summary.json"))
    monkeypatch.delenv("FACTSAPI_LABELWATCH_MAX_AGE_SECONDS", raising=False)


@pytest.fixture
def lw_artifact():
    from factsapi import sources
    return sources.load(str(FIXTURES / "lw-overview.json"))


@pytest.fixture
def ww_artifact():
    from factsapi import sources
    return sources.load(str(FIXTURES / "ww-summary.json"))
