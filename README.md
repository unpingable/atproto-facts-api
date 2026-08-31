# atproto-facts-api

A small, boring publication service. It serves public, anonymous, read-only
aggregate facts from the ATProto observability instruments at
<https://api.neutral.zone/atproto/>.

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
- **Epistemics survive the boundary.** Coverage, window, freshness, provenance,
  caveats and (for Labelwatch) observation adequacy travel with every fact. A
  negative claim such as `calm` is withheld when the observation window lacked
  the standing to make it.
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

## Dependencies

Python 3 standard library only at runtime. `pytest` for tests; `Pillow` only to
regenerate the social card.
