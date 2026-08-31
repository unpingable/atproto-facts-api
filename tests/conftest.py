import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def _point_at_fixtures(monkeypatch):
    """Every test reads fixtures, never a live host."""
    from factsapi import sources
    monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW", str(FIXTURES / "lw-overview.json"))
    monkeypatch.setattr(sources, "WEATHERWATCH_SUMMARY", str(FIXTURES / "ww-summary.json"))


@pytest.fixture
def lw_artifact():
    from factsapi import sources
    return sources.load(str(FIXTURES / "lw-overview.json"))


@pytest.fixture
def ww_artifact():
    from factsapi import sources
    return sources.load(str(FIXTURES / "ww-summary.json"))
