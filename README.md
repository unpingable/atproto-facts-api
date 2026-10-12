# atproto-facts-api

This documentation describes production runtime `f320f8e` and its public rolling-epoch contract. Runtime integration into the default branch is separate from this documentation update.

A small, boring publication service. It serves public, anonymous, read-only
aggregate facts from the ATProto observability instruments at
<https://api.neutral.zone/atproto/>.

## Use the public API

[Endpoint and response guide](https://api.neutral.zone/atproto/) ·
[OpenAPI 3.1](https://api.neutral.zone/atproto/openapi.json)

```sh
curl -sS -i https://api.neutral.zone/atproto/v1/instruments
curl -sS -i https://api.neutral.zone/atproto/v1/labelwatch/overview
curl -sS -i https://api.neutral.zone/atproto/v1/weatherwatch/conditions
curl -sS -i https://api.neutral.zone/atproto/v1/weatherwatch/metrics
```

Check the HTTP status, then carry `freshness`, coverage, `caveats` and
`provenance` with the counts. Index `PRESENT` means readable, not necessarily
servable. Labelwatch serves only `fresh` or `warming_up`; publication age and
collection age are checked independently. `503 source_stale`, `source_held` or
`source_unavailable` means no current reading, never zero observations.
Rolling-epoch alert counts are `null` and current label state is `unknown`.
Weatherwatch freshness is copied from its artifact without the Labelwatch refusal
policy. Neither a fresh response nor no recorded gaps proves complete coverage.

Artifact-backed fact responses support exact `If-None-Match` validators;
`304` has no body. ETags also change by age bucket. The index has no validators,
and `If-Modified-Since` is not evaluated. Successful facts use
`Cache-Control: public, max-age=60`; source/freshness refusals use `no-store`.
GET and HEAD retrieve resources; OPTIONS supports browser preflight.

## What it is

A thin read layer over artifacts the instruments already generate and already
publish. It opens two JSON files and reshapes them under a versioned contract.

It has no database, no authentication, no write path, and no query language.
It cannot reach any instrument's operational store, so its failure cannot
impair Labelwatch, Weatherwatch or Driftwatch — the worst it can do is stop
serving.

## Design constraints

- **Allow-list publication.** Every published field is selected by name in
  `facts.py`. A new field appearing inside an instrument artifact cannot reach
  the public surface without someone adding it deliberately. Asserted by
  `tests/test_publication_boundary.py`.
- **Observation limits survive the boundary.** Coverage, window, freshness,
  provenance and caveats accompany instrument facts. Legacy Labelwatch schema-23
  artifacts also carry observation adequacy; the rolling epoch carries acquisition
  and source coverage instead.
- **Absence is reported as absence.** A missing artifact yields `503`, never an
  empty or zeroed reading.
- **No subject identities.** Labeler DIDs are published because a labeler is a
  public moderation service; nothing else identity-shaped is.
- **The surface is asserted, not discovered.** `routes.py` holds the route table
  as data; the tests enumerate it and fail if the served surface drifts.

## Layout

    src/factsapi/sources.py   reading materialized artifacts
    src/factsapi/facts.py     artifact -> public fact transforms (the allow-list)
    src/factsapi/routes.py    the route table
    src/factsapi/server.py    stdlib HTTP service, GET/HEAD only
    src/factsapi/assets/      docs page, OpenAPI document, social card
    tools/make_og_card.py     regenerates the Open Graph card
    deploy/factsapi.service   systemd unit

## Tests

    python3 -m pytest -q

Production surface probes are opt-in:

    FACTSAPI_EXTERNAL=1 python3 -m pytest tests/test_external_surface.py -q

## Local run

    FACTSAPI_LABELWATCH_OVERVIEW=tests/fixtures/lw-overview.json \
    FACTSAPI_WEATHERWATCH_SUMMARY=tests/fixtures/ww-summary.json \
    PYTHONPATH=src python3 -m factsapi.server

The fixed-time response example in the public guide is generated from
`tests/fixtures/lw-recent-overview.json`. Its timestamps are illustrative. The
checked-in Labelwatch fixtures age into refusal during a local run; use the unit
tests for fixed-time successful responses rather than treating them as live data.

## Dependencies

Python 3 standard library only at runtime. `pytest` for tests; `Pillow` only to
regenerate the social card.

## License

Unless otherwise noted, this repository is licensed under MIT OR Apache-2.0,
at your option. Contributions are accepted under the same terms.
See [`LICENSE-MIT`](LICENSE-MIT) and [`LICENSE-APACHE`](LICENSE-APACHE).

Published facts are aggregate observations of a public network. The licence
above covers this source code; it makes no claim over the observed network
itself.
