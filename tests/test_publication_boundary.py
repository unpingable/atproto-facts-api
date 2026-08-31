"""What must never reach the public surface.

These are the tests that would have caught the 2026-08-31 Driftwatch exposure
if they had existed: they assert the boundary rather than documenting it.
"""
import json
import re

import pytest

from factsapi import facts, routes, sources

ALL_FACT_KEYS = ["labelwatch_overview", "weatherwatch_conditions",
                 "weatherwatch_metrics", "instruments"]

# Subject-identity shapes. A labeler DID is a public service identifier and is
# published on purpose; these patterns look for everything else.
AT_URI = re.compile(r"at://")
DID_ANY = re.compile(r"did:[a-z]+:[a-z0-9]+", re.I)
FORBIDDEN_SUBSTRINGS = (
    "claim_fingerprint", "fingerprint", "subject_uri", "subject_did",
    "exposure_score", "strain", "quarantine", "cooldown",
    "actor_identity", "handle", "email", "ip_address",
)


def _body(key):
    payload, _ = routes.build_fact(key)
    return json.dumps(payload)


@pytest.mark.parametrize("key", ALL_FACT_KEYS)
def test_no_at_uris_anywhere(key):
    assert not AT_URI.search(_body(key)), f"{key} leaked an at:// URI"


@pytest.mark.parametrize("key", ALL_FACT_KEYS)
def test_no_driftwatch_identity_vocabulary(key):
    body = _body(key).lower()
    for token in FORBIDDEN_SUBSTRINGS:
        assert token not in body, f"{key} leaked forbidden token {token!r}"


def test_only_labeler_dids_are_published():
    """Any DID present must be a labeler DID, reached via a labeler_did key."""
    payload, _ = routes.build_fact("labelwatch_overview")
    dids_in_body = set(DID_ANY.findall(json.dumps(payload)))
    declared = {row["labeler_did"] for row in payload["facts"]["top_labelers_7d"]}
    assert dids_in_body == declared, (
        "a DID appeared outside top_labelers_7d.labeler_did: "
        f"{dids_in_body - declared}"
    )


def test_no_driftwatch_endpoint_exists():
    for path in routes.ALL_PATHS:
        assert "driftwatch" not in path


def test_forbidden_prefixes_are_not_served():
    for prefix in routes.FORBIDDEN_PREFIXES:
        for path in routes.ALL_PATHS:
            assert not path.startswith(prefix), f"{path} matches forbidden {prefix}"


def test_allowlist_blocks_unknown_artifact_fields(tmp_path, monkeypatch):
    """A new field inside an instrument artifact must not reach the surface.

    This is the load-bearing property: publication is additive by choice, not by
    default. Poison the artifact and confirm none of it comes out.
    """
    original = json.loads(open(sources.LABELWATCH_OVERVIEW).read())
    poison = dict(original)
    poison["operator_secret"] = "SHOULD-NEVER-APPEAR"
    poison["subject_dids"] = ["did:plc:victimaaaaaaaaaaaaaaaaaaaa"]
    poison["observation"] = dict(original.get("observation") or {})
    poison["observation"]["internal_db_path"] = "/var/lib/labelwatch/labelwatch.db"
    poisoned = tmp_path / "poisoned.json"
    poisoned.write_text(json.dumps(poison))

    monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW", str(poisoned))
    body = json.dumps(routes.build_fact("labelwatch_overview")[0])

    assert "SHOULD-NEVER-APPEAR" not in body
    assert "operator_secret" not in body
    assert "subject_dids" not in body
    assert "victimaaaa" not in body
    assert "internal_db_path" not in body
    assert "/var/lib" not in body


def test_absent_artifact_is_reported_as_absence_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "LABELWATCH_OVERVIEW", str(tmp_path / "missing.json"))
    with pytest.raises(sources.SourceUnavailable):
        routes.build_fact("labelwatch_overview")
    payload, _ = routes.build_fact("instruments")
    entry = {e["instrument"]: e for e in payload["instruments"]}["labelwatch"]
    assert entry["state"] == "UNAVAILABLE"
    assert "absence of evidence" in entry["note"]


def test_provenance_never_carries_deployment_detail():
    for key in ("labelwatch_overview", "weatherwatch_conditions"):
        prov = json.dumps(routes.build_fact(key)[0]["provenance"])
        for token in ("/opt/", "/var/", "git_commit", "config_hash", "127.0.0.1"):
            assert token not in prov, f"{key} provenance leaked {token!r}"
